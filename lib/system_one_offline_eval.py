"""Read-only replay of authenticated System-1 frames with local pinned Laya."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from .laya_configuration import model_settings
from .protocol_artifact import QUESTION_REGISTRY
from .system_one_backends import (
    PROVIDER_PROTOCOL_HASH,
    LayaProvider,
    ProviderContractError,
    ProviderHealthError,
)
from .system_one_calibration import (
    _brier,
    _class_metrics,
    _cluster_accuracy_interval,
    _log_loss,
    _paired_rows,
    _risk_coverage,
    _top_label_brier,
)
from .system_one_contracts import SystemOneFrame, canonical_hash
from .system_one_diagnostics import validate_laya_diagnostics

MAX_REPLAY_TICKS = 100_000
MAX_REPLAY_CASES = 1_000
_SPLIT_NAMES = ("training", "calibration", "holdout")


def _json_digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _event_identity(event: Any) -> tuple[str, ...]:
    return tuple(str(getattr(event, field, "") or "") for field in (
        "namespace_id", "application_id", "application_instance_id", "agent_id",
        "agent_instance_id", "session_id", "task_id", "attempt_id", "interaction_id"))


def _frame_event_matches(frame: SystemOneFrame, event: Any,
                         tick_id: str, listed_event_ids: Sequence[str]) -> bool:
    payload = getattr(event, "payload", {}) or {}
    return bool(
        event.event_type == "system_one_decision"
        and event.category == "decision"
        and event.actor_id == "digitalpsychology_runtime"
        and event.authority_source == "digitalpsychology_system_one"
        and event.event_id in listed_event_ids
        and event.scope
        and (not payload.get("system_one_tick_id")
             or payload.get("system_one_tick_id") == tick_id)
        and payload.get("system_one_decision_id") == frame.request_id
        and payload.get("system_one_state_digest") == frame.state_digest
        and payload.get("system_one_question_bundle_hash") == frame.question_bundle_digest
        and _event_identity(event) == tuple(str(frame.identity.to_dict().get(key) or "")
                                             for key in (
            "namespace_id", "application_id", "application_instance_id", "agent_id",
            "agent_instance_id", "session_id", "task_id", "attempt_id", "interaction_id")))


def _load_committed_frames(path: str | Path, events: Sequence[Any]
                           ) -> tuple[dict[tuple[tuple[str, ...], str], dict[str, Any]],
                                      dict[str, int]]:
    supplied = Path(path).expanduser()
    if supplied.is_symlink():
        raise ValueError("runtime state database must not be a symlink")
    runtime_path = supplied.resolve(strict=True)
    if not runtime_path.is_file():
        raise ValueError("runtime state database must be a regular file")
    event_by_scope_id = {(str(getattr(event, "scope", "") or ""),
                          str(getattr(event, "event_id", "") or "")): event
                         for event in events}
    frames: dict[tuple[tuple[str, ...], str], dict[str, Any]] = {}
    stats = {"committed_ticks": 0, "committed_ticks_with_frame": 0,
             "authenticated_frame_matches": 0, "unsupported_frames": 0,
             "malformed_ticks": 0}
    uri = runtime_path.as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as database:
        tables = {str(row[0]) for row in database.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if "control_ticks" not in tables:
            return frames, stats
        rows = database.execute(
            "SELECT scope, tick_id, basis_json, result_json FROM control_ticks "
            "WHERE status='committed' ORDER BY created_at LIMIT ?",
            (MAX_REPLAY_TICKS + 1,)).fetchall()
    if len(rows) > MAX_REPLAY_TICKS:
        raise ValueError("runtime database exceeds the bounded replay tick limit")
    stats["committed_ticks"] = len(rows)
    for scope, tick_id, basis_json, result_json in rows:
        try:
            basis = json.loads(basis_json)
            result = json.loads(result_json) if result_json else {}
        except (TypeError, json.JSONDecodeError):
            stats["malformed_ticks"] += 1
            continue
        system_one = basis.get("system_one") if isinstance(basis, Mapping) else None
        raw_frame = system_one.get("frame") if isinstance(system_one, Mapping) else None
        event_ids = result.get("system_one_decision_event_ids") if isinstance(result, Mapping) else None
        if not isinstance(raw_frame, Mapping) or not isinstance(event_ids, list):
            continue
        stats["committed_ticks_with_frame"] += 1
        try:
            frame = SystemOneFrame.from_mapping(raw_frame)
        except (TypeError, ValueError, KeyError):
            stats["unsupported_frames"] += 1
            continue
        for event_id in event_ids[:16]:
            event = event_by_scope_id.get((str(scope), str(event_id)))
            if event is None or not _frame_event_matches(
                    frame, event, str(tick_id), event_ids):
                continue
            key = (_event_identity(event), frame.request_id)
            if key in frames:
                raise ValueError("authenticated decision maps to multiple committed frames")
            frames[key] = {
                "frame": frame,
                "source_frame_digest": _json_digest(raw_frame),
                "scope": str(scope),
                "tick_id": str(tick_id),
                "event_id": str(event_id),
            }
            stats["authenticated_frame_matches"] += 1
    return frames, stats


def _union_find_split_manifest(rows: Sequence[Mapping[str, Any]],
                               question_family: str) -> dict[str, Any]:
    """Build deterministic task/trajectory/fixture-disjoint descriptive splits."""
    parent = list(range(len(rows)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    owner: dict[tuple[str, str], int] = {}
    for index, row in enumerate(rows):
        for kind in ("task_instance_hash", "trajectory_id", "fixture_set_hash"):
            value = str(row.get(kind) or "")
            if not value:
                continue
            token = (kind, value)
            prior = owner.setdefault(token, index)
            union(index, prior)
    groups: dict[int, list[int]] = {}
    for index in range(len(rows)):
        groups.setdefault(find(index), []).append(index)
    clusters = []
    for indices in groups.values():
        identities = sorted({f"{kind}:{rows[index].get(kind, '')}"
                             for index in indices for kind in (
                                 "task_instance_hash", "trajectory_id", "fixture_set_hash")
                             if rows[index].get(kind)})
        clusters.append((hashlib.sha256("\n".join(identities).encode()).hexdigest(), indices))
    clusters.sort(key=lambda item: item[0])
    assignments: dict[str, str] = {}
    status = "available"
    if len(clusters) < 3:
        status = "descriptive_only"
    else:
        train_end = max(1, int(len(clusters) * 0.6))
        calibration_end = min(len(clusters) - 1,
                              max(train_end + 1, int(len(clusters) * 0.8)))
        for index, (_digest, members) in enumerate(clusters):
            split = ("training" if index < train_end else
                     "calibration" if index < calibration_end else "holdout")
            for row_index in members:
                task_hash = str(rows[row_index]["task_instance_hash"])
                prior = assignments.setdefault(task_hash, split)
                if prior != split:
                    raise ValueError("connected task evidence crossed split boundaries")
    manifest: dict[str, Any] = {
        "schema_version": "laya-offline-splits/v1",
        "question_family": question_family,
        "split_policy": "connected-task-trajectory-fixture-hash-60-20-20/v1",
        "status": status,
        "independent_clusters": len(clusters),
        "assignments": dict(sorted(assignments.items())),
    }
    manifest["manifest_digest"] = canonical_hash(manifest)
    return manifest


def _validate_split_manifest(value: Mapping[str, Any], rows: Sequence[Mapping[str, Any]],
                             question_family: str) -> dict[str, Any]:
    fields = {"schema_version", "question_family", "split_policy", "status",
              "independent_clusters", "assignments", "manifest_digest"}
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ValueError("offline split manifest is malformed")
    body = {key: value[key] for key in fields - {"manifest_digest"}}
    if (value["schema_version"] != "laya-offline-splits/v1"
            or value["question_family"] != question_family
            or value["split_policy"] != "connected-task-trajectory-fixture-hash-60-20-20/v1"
            or value["status"] != "available"
            or type(value["independent_clusters"]) is not int
            or value["independent_clusters"] < 3
            or value["independent_clusters"] !=
            _union_find_split_manifest(rows, question_family)["independent_clusters"]
            or value["manifest_digest"] != canonical_hash(body)
            or not isinstance(value["assignments"], Mapping)):
        raise ValueError("offline split manifest identity or digest is invalid")
    required_tasks = {str(row["task_instance_hash"]) for row in rows}
    assignments = {str(key): str(split) for key, split in value["assignments"].items()}
    if set(assignments) != required_tasks or not set(assignments.values()).issubset(_SPLIT_NAMES):
        raise ValueError("offline split manifest does not cover the paired task set")
    if set(assignments.values()) != set(_SPLIT_NAMES):
        raise ValueError("offline split manifest must contain train, calibration, and holdout")
    cluster_splits: dict[tuple[str, str], str] = {}
    for row in rows:
        split = assignments[str(row["task_instance_hash"])]
        for kind in ("trajectory_id", "fixture_set_hash"):
            token = (kind, str(row.get(kind) or ""))
            prior = cluster_splits.setdefault(token, split)
            if prior != split:
                raise ValueError("offline split manifest leaks a trajectory or fixture")
    return dict(value)


def _metrics(rows: Sequence[Mapping[str, Any]], labels: Sequence[str], *,
             majority_label: str | None, split_name: str) -> dict[str, Any]:
    if not rows:
        return {"count": 0, "status": "no_eligible_predictions"}
    rows = [dict(row, probabilities=dict(sorted(row["probabilities"].items())))
            for row in rows]
    classes = _class_metrics(list(rows), labels)
    per_class = []
    for name, tp, fp, fn, precision, recall in classes:
        f1 = (2 * precision * recall / (precision + recall)
              if precision + recall else 0.0)
        per_class.append({"label": name, "support": tp + fn,
                          "precision": precision, "recall": recall, "f1": f1})
    probability_rows = [(dict(row["probabilities"]), str(row["actual"])) for row in rows]
    correct = [max(row["probabilities"].items(), key=lambda item: item[1])[0]
               == row["actual"] for row in rows]
    confusion = {label: {predicted: 0 for predicted in labels} for label in labels}
    for row in rows:
        predicted = max(row["probabilities"].items(), key=lambda item: item[1])[0]
        confusion.setdefault(str(row["actual"]), {item: 0 for item in labels})
        confusion[str(row["actual"])][predicted] += 1
    host_rows = [row for row in rows if row.get("host_baseline") in labels]
    aurc, curve = _risk_coverage(list(rows))
    bins = []
    for index in range(10):
        lower, upper = index / 10, (index + 1) / 10
        bucket = []
        for row in rows:
            _label, confidence = max(row["probabilities"].items(), key=lambda item: item[1])
            if min(9, int(confidence * 10)) == index:
                predicted = max(row["probabilities"].items(), key=lambda item: item[1])[0]
                bucket.append((confidence, predicted == row["actual"]))
        if bucket:
            mean_conf = sum(item[0] for item in bucket) / len(bucket)
            accuracy = sum(int(item[1]) for item in bucket) / len(bucket)
            bins.append({"lower": lower, "upper": upper, "count": len(bucket),
                         "mean_confidence": mean_conf, "accuracy": accuracy,
                         "absolute_gap": abs(mean_conf - accuracy)})
    return {
        "status": "descriptive_only",
        "count": len(rows),
        "top1_accuracy": sum(map(int, correct)) / len(correct),
        "macro_f1": sum(item["f1"] for item in per_class) / len(per_class),
        "per_class": per_class,
        "confusion_matrix": confusion,
        "multiclass_brier_score": _brier(probability_rows),
        "top_label_brier_score": _top_label_brier(list(rows)),
        "log_loss": _log_loss(probability_rows),
        "ece_bin_count": 10,
        "expected_calibration_error": sum(
            item["count"] / len(rows) * item["absolute_gap"] for item in bins),
        "reliability_bins": bins,
        "risk_coverage_curve": curve,
        "aurc": aurc,
        "host_baseline_count": len(host_rows),
        "host_baseline_accuracy": (
            sum(row["host_baseline"] == row["actual"] for row in host_rows)
            / len(host_rows) if host_rows else None),
        "training_majority_label": majority_label,
        "training_majority_accuracy": (
            sum(majority_label == row["actual"] for row in rows) / len(rows)
            if majority_label is not None and split_name != "training" else None),
        "accuracy_95_ci_by_trajectory": _cluster_accuracy_interval(
            list(rows), "trajectory_id"),
        "accuracy_95_ci_by_fixture": _cluster_accuracy_interval(
            list(rows), "fixture_set_hash"),
    }


def _read_split(path: str | Path) -> Mapping[str, Any]:
    supplied = Path(path).expanduser()
    if supplied.is_symlink():
        raise ValueError("split manifest must not be a symlink")
    info = supplied.stat()
    if (not supplied.is_file() or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600):
        raise ValueError("split manifest must be a user-owned mode-0600 regular file")
    try:
        raw = json.loads(supplied.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("split manifest contains invalid JSON") from exc
    if not isinstance(raw, Mapping):
        raise TypeError("split manifest must be a JSON object")
    return raw


def run_offline_replay(event_db: str | Path, runtime_state_db: str | Path,
                       question_family: str, *,
                       split_manifest: Mapping[str, Any] | None = None
                       ) -> dict[str, Any]:
    """Replay authenticated, independently labeled frames without model APIs."""
    if question_family not in QUESTION_REGISTRY:
        raise ValueError("unsupported System-1 question family")
    from .system_one_cli import load_authenticated_system_one_events

    events = load_authenticated_system_one_events(event_db)
    paired = _paired_rows(events, question_family)
    frames, frame_stats = _load_committed_frames(runtime_state_db, events)
    decisions = {( _event_identity(event),
                   str((getattr(event, "payload", {}) or {}).get(
                       "system_one_decision_id") or "")): event
                 for event in events if getattr(event, "event_type", None) ==
                 "system_one_decision"}
    cases = []
    missing_frames = 0
    for row in paired:
        key = (tuple(row["identity"]), str(row["decision_id"]))
        stored = frames.get(key)
        event = decisions.get(key)
        if stored is None or event is None:
            missing_frames += 1
            continue
        frame = stored["frame"]
        if question_family not in frame.question_ids:
            missing_frames += 1
            continue
        question = next(item for item in frame.questions
                        if item.question_id == question_family)
        cases.append({**row, "frame": frame,
                      "source_frame_digest": stored["source_frame_digest"],
                      "question_labels": [label for label, _desc in question.criteria],
                      "original_backend_id": str(event.payload.get(
                          "system_one_backend_id") or ""),
                      "original_model_id": str(event.payload.get(
                          "system_one_model_id") or ""),
                      "original_model_revision": str(event.payload.get(
                          "system_one_model_revision") or ""),
                      "original_model_content_hash": str(event.payload.get(
                          "system_one_model_content_hash") or ""),
                      "original_policy_influenced": bool(event.payload.get(
                          "system_one_semantic_influenced")),
                      "original_selection_changed": bool(event.payload.get(
                          "system_one_changed_selection")),
                      "original_changed_frameworks": list(event.payload.get(
                          "system_one_changed_frameworks") or ())})
    if len(cases) > MAX_REPLAY_CASES:
        raise ValueError("paired replay corpus exceeds the bounded case limit")
    generated_manifest = _union_find_split_manifest(cases, question_family)
    if split_manifest is not None and cases:
        manifest = _validate_split_manifest(split_manifest, cases, question_family)
    else:
        manifest = generated_manifest
    assignments = manifest.get("assignments", {})
    for case in cases:
        case["split"] = assignments.get(case["task_instance_hash"], "descriptive")

    settings = model_settings()
    matching = [case for case in cases if (
        case["original_backend_id"] == "digitalpsychology-laya-native"
        and case["original_model_id"] == settings["model_id"]
        and case["original_model_revision"] == settings["model_revision"]
        and case["original_model_content_hash"] == settings["model_content_sha256"])]
    outputs: list[dict[str, Any]] = []
    provider_error: str | None = None
    provider_load_ms: int | None = None
    provider = None
    if matching:
        try:
            provider = LayaProvider.from_environment()
            if provider.model_content_hash != settings["model_content_sha256"]:
                raise ValueError("local checkpoint differs from configured content pin")
            provider_load_ms = provider.warmup_and_wait()
            for case in matching:
                replay_frame = replace(case["frame"],
                                       provider_protocol_hash=PROVIDER_PROTOCOL_HASH)
                try:
                    provider.reset_identity(replay_frame.identity)
                    judgment = provider.judge(replay_frame)
                    answer = judgment.answer(question_family)
                    if answer is None:
                        raise ProviderContractError("replay omitted requested family")
                    diagnostics = validate_laya_diagnostics(
                        judgment.local_inference_diagnostics)
                    probabilities = {item.label: float(item.probability)
                                     for item in sorted(answer.raw_probabilities,
                                                        key=lambda item: item.label)}
                    actual = case["actual"]
                    eligible = (diagnostics["advisory_eligible"] is True
                                and set(probabilities) == set(case["question_labels"])
                                and actual in probabilities)
                    outputs.append({
                        "decision_id": case["decision_id"],
                        "task_instance_hash": case["task_instance_hash"],
                        "trajectory_id": case["trajectory_id"],
                        "fixture_set_hash": case["fixture_set_hash"],
                        "split": case["split"], "actual": actual,
                        "selected": answer.selected,
                        "probabilities": probabilities,
                        "host_baseline": case.get("host_baseline"),
                        "advisory_eligible": eligible,
                        "diagnostics": diagnostics,
                        "source_frame_digest": case["source_frame_digest"],
                        "replay_protocol_hash": PROVIDER_PROTOCOL_HASH,
                        "replay_frame_digest": _json_digest(replay_frame.canonical_payload()),
                        "original_policy_influenced": case["original_policy_influenced"],
                        "original_selection_changed": case["original_selection_changed"],
                        "original_changed_frameworks": case["original_changed_frameworks"],
                    })
                except (KeyError, OSError, ProviderContractError,
                        ProviderHealthError, RuntimeError, TypeError, ValueError) as exc:
                    outputs.append({
                        "decision_id": case["decision_id"],
                        "task_instance_hash": case["task_instance_hash"],
                        "trajectory_id": case["trajectory_id"],
                        "fixture_set_hash": case["fixture_set_hash"],
                        "split": case["split"], "actual": case["actual"],
                        "failure_class": type(exc).__name__,
                        "advisory_eligible": False,
                        "source_frame_digest": case["source_frame_digest"],
                        "original_policy_influenced": case["original_policy_influenced"],
                        "original_selection_changed": case["original_selection_changed"],
                    })
        except (ImportError, OSError, ProviderContractError,
                ProviderHealthError, RuntimeError, TypeError, ValueError) as exc:
            provider_error = type(exc).__name__
            outputs.extend({
                "decision_id": case["decision_id"],
                "task_instance_hash": case["task_instance_hash"],
                "trajectory_id": case["trajectory_id"],
                "fixture_set_hash": case["fixture_set_hash"],
                "split": case["split"], "actual": case["actual"],
                "failure_class": provider_error,
                "advisory_eligible": False,
                "source_frame_digest": case["source_frame_digest"],
            } for case in matching)
        finally:
            if provider is not None:
                provider.close()

    labels = sorted({label for case in cases for label in case["question_labels"]})
    eligible_outputs = [row for row in outputs if row.get("advisory_eligible") is True]
    training_actual_counts = Counter(row["actual"] for row in eligible_outputs
                                     if row["split"] == "training")
    majority_label = (max(sorted(training_actual_counts),
                          key=lambda item: training_actual_counts[item])
                      if training_actual_counts else None)
    split_reports = {}
    for split in (*_SPLIT_NAMES, "descriptive"):
        split_rows = [row for row in eligible_outputs if row["split"] == split]
        attempted = [row for row in outputs if row["split"] == split]
        if attempted or split == "descriptive":
            split_reports[split] = {
                "inference_attempts": len(attempted),
                "provider_failures": sum("failure_class" in row for row in attempted),
                "coverage_abstentions": sum(
                    row.get("advisory_eligible") is not True
                    and "failure_class" not in row for row in attempted),
                "eligible_metrics": _metrics(split_rows, labels,
                                              majority_label=majority_label,
                                              split_name=split),
            }
    policy_influenced = sum(case["original_policy_influenced"] for case in cases)
    selection_changed = sum(case["original_selection_changed"] for case in cases)
    return {
        "schema_version": "laya-offline-replay/v1",
        "question_family": question_family,
        "provenance": "authenticated_real_trace",
        "evaluation_kind": "local_laya_replay_not_new_trajectory",
        "read_only_sources": True,
        "inference_provider": "local_native_laya_only",
        "model_api_calls": 0,
        "checkpoint_identity": {key: settings[key] for key in (
            "model_id", "model_revision", "model_content_sha256")},
        "provider_protocol_hash": PROVIDER_PROTOCOL_HASH,
        "provider_load_ms": provider_load_ms,
        "provider_error_class": provider_error,
        "paired_independent_outcomes": len(paired),
        "authenticated_frames_loaded": frame_stats["authenticated_frame_matches"],
        "paired_cases_with_immutable_frames": len(cases),
        "paired_cases_missing_frames": missing_frames,
        "nonmatching_checkpoint_or_backend_cases": len(cases) - len(matching),
        "frame_store_diagnostics": frame_stats,
        "split_status": manifest.get("status"),
        "split_manifest": manifest,
        "replayed_inferences": len(outputs),
        "eligible_replayed_predictions": len(eligible_outputs),
        "unqualified_replay_outputs": sum(
            row.get("advisory_eligible") is not True
            and "failure_class" not in row for row in outputs),
        "provider_failures": sum("failure_class" in row for row in outputs),
        "predictive_performance": split_reports,
        "logged_original_policy_influence": {
            "paired_cases": len(cases),
            "semantic_influence_receipts": policy_influenced,
            "changed_selection_receipts": selection_changed,
            "scope": "historical event telemetry only; not caused by replay",
        },
        "policy_actuation": "not measured by offline replay",
        "agent_level_task_efficacy": "not measured by offline replay",
        "qualification": "not evaluated; no threshold or checkpoint changes",
        "replay_outputs": outputs,
    }


def load_split_manifest(path: str | Path) -> Mapping[str, Any]:
    return _read_split(path)


__all__ = ["load_split_manifest", "run_offline_replay"]
