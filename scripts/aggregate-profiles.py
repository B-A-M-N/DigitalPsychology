#!/usr/bin/env python3
"""Behavioral profile aggregator.

Computes per-episode/per-probe effectiveness for guard packs from the event
stream shared with the compiled guard pack. Profile paths the APPLICABLE
episodes/probes, not raw event counts: one evaluator FAIL on a window of 100
events is 1 violation, scored over the applicable episodes it was evaluated
on — not a 99% effective guard.

Pipeline contract:
    aggregate-profiles <event-store> --out profiles.json
    monitor-guards profiles.json
(or library calls around the same store). Profiles join a per-task context
sidecar keyed by task_id (model/harness/guard pack/toolset/framework hash)
instead of repeating metadata per event. Schema-v2 manifests are keyed by
session/task/attempt; task-only attribution is treated as legacy and is not
promotion-grade.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.feedback_loop import load_events, EVALUATORS, ProbeRunner, EpisodeBuilder  # noqa: E402
from lib.routing_profiles import build_routing_pack, derive_profiles  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def evaluator_for(guard: Dict[str, Any]) -> str | None:
    evaluator = guard.get("evaluator")
    if evaluator:
        return evaluator if evaluator in EVALUATORS else None
    for name in EVALUATORS:
        if name in (guard.get("target_behavior") or "").lower().replace(" ", "_"):
            return name
    for name in EVALUATORS:
        if name in (guard.get("target_behavior") or "").lower().replace(" ", "_").replace("-", "_"):
            return name
    return None


def _episode_context(episode, task_context: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    session_id = getattr(episode, "session_id", "")
    task_id = getattr(episode, "task_id", "")
    attempt_id = getattr(episode, "attempt_id", "")
    if session_id and task_id and attempt_id:
        identity = f"{getattr(episode, 'namespace_id', 'default')}|{getattr(episode, 'application_instance_id', 'unknown-application')}|{getattr(episode, 'agent_instance_id', 'agent')}|{session_id}|{task_id}|{attempt_id}|{getattr(episode, 'behavioral_subject', '')}|{getattr(episode, 'role', None)}|{getattr(episode, 'interaction_id', None)}|{getattr(episode, 'delegation_id', None)}"
        return dict(task_context.get(identity) or task_context.get(f"{session_id}:{task_id}:{attempt_id}", {}))
    # Legacy imports are deliberately explicit and never silently promoted.
    return dict(task_context.get(f"legacy:{task_id}", {}))


def _context_key(context: Dict[str, Any]) -> Tuple[Any, ...]:
    return (context.get("agent_instance_id"), context.get("model"),
            context.get("harness"), context.get("task_family"),
            context.get("task_shape"), tuple(sorted(context.get("domain_tags") or ())),
            context.get("phase"), context.get("environment"),
            context.get("framework_version"),
            json.dumps(context.get("statework_versions") or context.get("stateworks") or {},
                       sort_keys=True, separators=(",", ":")),
            context.get("guard_pack_hash") or context.get("guard_pack"), context.get("toolset"),
            context.get("comparison_context_hash"), context.get("cohort"),
            context.get("window_id"))


def _window_id(context: Dict[str, Any], event_times: List[str], window_minutes: float) -> str:
    explicit = context.get("window_id") or context.get("experiment_batch_id")
    if explicit:
        return str(explicit)
    if not event_times:
        return "unknown"
    try:
        instant = datetime.fromisoformat(str(min(event_times)).replace("Z", "+00:00"))
        bucket = int(instant.timestamp() // max(window_minutes * 60.0, 1.0))
        return f"utc-{bucket}"
    except (TypeError, ValueError, OverflowError):
        return "unknown"


def _guard_scope_matches(guard: Dict[str, Any], context: Dict[str, Any]) -> bool:
    # Production manifests carry the resolver's authoritative result.  Scope
    # matching below is only a compatibility path for older sidecars.
    assignments = context.get("guard_assignments") or {}
    guard_key = guard.get("key") or guard.get("id")
    if guard_key in assignments:
        return assignments[guard_key] != "ineligible"
    scope = guard.get("scope") or {}
    def values(name: str, legacy: Any = None) -> set[str]:
        value = scope.get(name)
        if value is None:
            value = legacy
        if value is None:
            return set()
        return set(value if isinstance(value, (list, tuple, set)) else [value])
    for field, context_key, legacy in (
            ("domains", "domain_tags", guard.get("domain")),
            ("stateworks", "stateworks", None),
            ("task_shapes", "task_shape", guard.get("task_shapes")),
            ("models", "model", guard.get("model")),
            ("harnesses", "harness", guard.get("harness"))):
        allowed = values(field, legacy)
        if not allowed:
            continue
        actual = context.get(context_key)
        actual_set = set(actual if isinstance(actual, (list, tuple, set)) else [actual]) if actual else set()
        if field in {"models", "harnesses"}:
            if not actual or actual not in allowed:
                return False
        elif not actual_set.intersection(allowed):
            return False
    return True


def _guard_cohort(guard: Dict[str, Any], context: Dict[str, Any]) -> str:
    """Causal attribution: treatment/control/ineligible, never unknown-as-control."""
    guard_key = guard.get("key") or guard.get("id")
    assignments = context.get("guard_assignments") or {}
    if guard_key in assignments:
        return str(assignments[guard_key])
    if not _guard_scope_matches(guard, context):
        return "ineligible"
    active = set(context.get("active_guard_keys") or [])
    assignments = context.get("canary_assignments") or {}
    if isinstance(assignments, dict):
        assigned = assignments.get(context.get("task_id"), assignments.get(guard_key, False))
        if isinstance(assigned, (list, tuple, set)):
            assigned = guard_key in assigned
    else:
        assigned = guard_key in assignments
    if guard.get("status") == "canary":
        return "treatment" if bool(assigned) or guard_key in active else "control"
    if guard_key in active:
        return "treatment"
    # Active policy absent from a task is a control only if the task proves it
    # was eligible for the same experiment.
    if context.get("eligible_guard_keys") is not None:
        return "control" if guard_key in set(context.get("eligible_guard_keys") or []) else "ineligible"
    return "ineligible"


def aggregate(events, pack: Dict[str, Any], task_context: Dict[str, Dict[str, Any]],
              window_minutes: float = 30.0) -> List[Dict[str, Any]]:
    """Per-guard, per-applicable-episode metrics with rate + sample count +
    confidence interval (Wilson)."""
    builder = EpisodeBuilder(window_minutes=window_minutes)
    for ev in events:
        builder.add(ev)
    episodes = builder.build()
    runner = ProbeRunner()

    profiles: List[Dict[str, Any]] = []
    for guard in pack.get("guards", []):
        evaluator = evaluator_for(guard)
        if not evaluator:
            profiles.append({
        "guard": guard.get("id"), "evaluator": None,
                "applicable_episodes": 0, "passing_episodes": 0, "rate": None,
                "window": "", "task_context": {}, "cohort": "ineligible",
                "note": "no registered evaluator",
            })
            continue
        buckets: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
        for ep in episodes:
            result = runner.run(evaluator, ep.events)
            if result.outcome == "NOT_APPLICABLE":
                continue
            context = dict(_episode_context(ep, task_context))
            context["task_id"] = getattr(ep, "task_id", "")
            context.setdefault("agent_instance_id", ep.agent_instance_id)
            context.setdefault("session_id", ep.session_id)
            context.setdefault("attempt_id", ep.attempt_id)
            context.setdefault("behavioral_subject", ep.behavioral_subject)
            context["trajectory_key"] = [ep.agent_instance_id, ep.session_id,
                                          ep.task_id, ep.attempt_id,
                                          ep.behavioral_subject]
            cohort = _guard_cohort(guard, context)
            if cohort == "ineligible":
                continue
            context["cohort"] = cohort
            event_times = [event.timestamp for event in ep.events]
            context["window_id"] = _window_id(context, event_times, window_minutes)
            # The statistical key is intentionally independent of session and
            # attempt identity. Those values remain provenance on each trial.
            key = _context_key(context)
            bucket = buckets.setdefault(key, {
                "context": context, "cohort": cohort, "trials": {},
                "ci_low": [], "ci_high": [], "window_start": None, "window_end": None,
            })
            if event_times:
                bucket["window_start"] = min(x for x in [bucket["window_start"], min(event_times)] if x)
                bucket["window_end"] = max(x for x in [bucket["window_end"], max(event_times)] if x)
            trajectory_key = json.dumps(context["trajectory_key"], separators=(",", ":"))
            trial = bucket["trials"].setdefault(
                trajectory_key, {"passing": True, "trajectory_key": context["trajectory_key"]})
            trial["passing"] = bool(trial["passing"] and result.passed)
        for key, bucket in buckets.items():
            applicable = len(bucket["trials"])
            passing = sum(1 for trial in bucket["trials"].values() if trial["passing"])
            rate = passing / applicable if applicable else None
            z = 1.96
            ci_low = ci_high = None
            if applicable and rate is not None:
                denom = 1 + z * z / applicable
                center = (rate + z * z / (2 * applicable)) / denom
                margin = z * (rate * (1 - rate) / applicable + z * z /
                              (4 * applicable * applicable)) ** 0.5 / denom
                ci_low = max(0.0, center - margin)
                ci_high = min(1.0, center + margin)
            profiles.append({
                "guard": guard.get("id"), "guard_key": guard.get("key") or guard.get("id"),
                "evaluator": evaluator,
                "cohort": bucket["cohort"],
                "applicable_episodes": applicable,
                "treatment_count": applicable if bucket["cohort"] == "treatment" else 0,
                "control_count": applicable if bucket["cohort"] == "control" else 0,
                "passing_episodes": passing,
                "rate": round(rate, 4) if rate is not None else None,
                "ci_lower_bound": round(ci_low, 4) if ci_low is not None else None,
                "ci_upper_bound": round(ci_high, 4) if ci_high is not None else None,
                "window_start": bucket["window_start"] or "",
                "window_end": bucket["window_end"] or "",
                "window_id": bucket["context"].get("window_id", "unknown"),
                "trajectory_ids": [trial["trajectory_key"] for trial in bucket["trials"].values()],
                "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "window": "",
                "task_context": bucket["context"],
                "stratification_key": repr(key),
                "note": "per-applicable-episode metrics stratified by task policy/context",
            })
    return profiles


def load_task_context(path: Path | None, *, diagnostics: Dict[str, List[str]] | None = None) -> Dict[str, Dict[str, Any]]:
    """Per-task context sidecar: model, harness, framework/policy hash,
    statework versions, guard pack, toolset, task family, environment."""
    diagnostics = diagnostics if diagnostics is not None else {}
    diagnostics.setdefault("invalid_manifests", [])
    diagnostics.setdefault("unattributed_tasks", [])
    diagnostics.setdefault("policy_hash_mismatches", [])
    if path is None:
        return {}
    if path.is_dir():
        contexts: Dict[str, Dict[str, Any]] = {}
        for manifest in sorted(path.glob("*.manifest.json")):
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                diagnostics["invalid_manifests"].append(str(manifest))
                continue
            task_id = data.get("task_id")
            if not task_id:
                diagnostics["invalid_manifests"].append(str(manifest))
                continue
            session_id = data.get("session_id")
            attempt_id = data.get("attempt_id")
            if session_id and attempt_id:
                contexts[f"{session_id}:{task_id}:{attempt_id}"] = data
            else:
                data["legacy_context"] = True
                contexts[f"legacy:{task_id}"] = data
        return contexts
    if not path.exists():
        diagnostics["invalid_manifests"].append(str(path))
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        diagnostics["invalid_manifests"].append(str(path))
        return {}
    if not isinstance(data, dict):
        diagnostics["invalid_manifests"].append(str(path))
        return {}
    contexts: Dict[str, Dict[str, Any]] = {}
    for key, value in (data.get("tasks", {}) or {}).items():
        if not isinstance(value, dict):
            continue
        task_id = value.get("task_id") or key
        if value.get("session_id") and value.get("attempt_id"):
            contexts[f"{value['session_id']}:{task_id}:{value['attempt_id']}"] = value
        else:
            value = dict(value)
            value["legacy_context"] = True
            contexts[f"legacy:{task_id}"] = value
    return contexts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("event_store", nargs="?", type=Path, default=ROOT / "fixtures" / "events.ndjson",
                        help="NDJSON event store (or SQLite file)")
    parser.add_argument("--out", type=Path, default=None,
                        help="write profiles.json instead of stdout")
    parser.add_argument("--task-context", type=Path, default=None,
                        help="per-task context sidecar or runtime manifest directory")
    parser.add_argument("--guard-pack", type=Path, default=ROOT / "compiled-guard-pack.json")
    parser.add_argument("--routing-profile-out", type=Path, default=None,
                        help="write evidence-backed candidate routing profile artifact(s)")
    args = parser.parse_args()

    if not args.event_store.exists() or not args.guard_pack.exists():
        print(f"Need event store ({args.event_store}) and guard pack ({args.guard_pack})")
        return 1
    try:
        events = load_events(args.event_store)
    except Exception as exc:
        print(f"Loader error on {args.event_store}: {exc}")
        return 1
    pack = json.loads(args.guard_pack.read_text(encoding="utf-8"))
    context_path = args.task_context
    if context_path is None:
        xdg_runtime = os.environ.get("XDG_RUNTIME_DIR")
        runtime_root = Path(xdg_runtime) if xdg_runtime else Path("/tmp")
        generated = runtime_root / "cognitiveframeworks" / "telemetry"
        context_path = generated if generated.exists() else None
    diagnostics: Dict[str, List[str]] = {}
    task_context = load_task_context(context_path, diagnostics=diagnostics)
    event_tasks = {getattr(event, "task_id", "") for event in events if getattr(event, "task_id", "")}
    def event_context(event):
        session_id = getattr(event, "session_id", "")
        task_id = getattr(event, "task_id", "")
        attempt_id = getattr(event, "attempt_id", "")
        if session_id and task_id and attempt_id:
            return task_context.get(f"{session_id}:{task_id}:{attempt_id}")
        return task_context.get(f"legacy:{task_id}")

    diagnostics["unattributed_tasks"] = sorted(
        {getattr(event, "task_id", "") for event in events if event_context(event) is None})
    for event in events:
        context = event_context(event) or {}
        expected = context.get("policy_hash") or context.get("framework_policy_hash")
        actual = (getattr(event, "payload", {}) or {}).get("policy_hash")
        if expected and actual and expected != actual:
            diagnostics["policy_hash_mismatches"].append(getattr(event, "task_id", ""))
    profiles = aggregate(events, pack, task_context)
    routing_profiles = derive_profiles(events, task_context)
    payload = {"profiles": profiles, "count": len(profiles),
               "routing_profiles": routing_profiles,
               "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
               "attribution": diagnostics,
               "promotion_grade": not any(diagnostics.values())}
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {len(profiles)} profile(s) -> {args.out}")
    if args.routing_profile_out:
        args.routing_profile_out.parent.mkdir(parents=True, exist_ok=True)
        source_revision = hashlib.sha256(
            args.event_store.read_bytes() + args.guard_pack.read_bytes()).hexdigest()
        artifact: Any = build_routing_pack(routing_profiles, source_revision=source_revision)
        args.routing_profile_out.write_text(json.dumps(artifact, indent=2) + "\n",
                                            encoding="utf-8")
        print(f"Wrote {len(routing_profiles)} routing profile(s) -> {args.routing_profile_out}")
    else:
        print(json.dumps(payload, indent=2))
    if any(diagnostics.values()):
        print("Promotion-grade aggregation refused: attribution diagnostics are present")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
