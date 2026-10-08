"""Operator preflight and per-family qualification commands for Laya."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
import tempfile
import time
from typing import Any, Mapping

from .feedback_loop import EventFactory, trusted_state_root
from .protocol_artifact import QUESTION_REGISTRY
from .system_one_backends import (LayaProvider, PROVIDER_PROTOCOL_HASH,
                                 model_content_digest)
from .system_one_contracts import (ActivityFact, CriterionState, QuestionSpec,
                                   SemanticState, SystemOneFrame,
                                   SystemOneIdentity, SystemOneJudgment,
                                   canonical_hash)
from .system_one_calibration import (CalibrationBundle, CalibrationRecord, calibration_metrics,
                                     calibration_corpus_counts,
                                     calibration_record_from_events,
                                     calibration_task_instance_hashes,
                                     calibration_task_fixture_hashes,
                                     PRODUCTION_MINIMUM_INDEPENDENT_FIXTURES)
from .system_one_learning import (CalibrationLifecycleError,
                                  SystemOneCalibrationLifecycle)
from .laya_configuration import model_settings
from .system_one_service import SystemOneService


PRODUCTION_MINIMUM_OUTCOMES = 100
PRODUCTION_MINIMUM_TRAJECTORIES = 30
PRODUCTION_MINIMUM_COHORT_TRAJECTORIES = 20
PRODUCTION_MINIMUM_ACCURACY = 0.8
PRODUCTION_MAXIMUM_BRIER = 0.25
PRODUCTION_MAXIMUM_ECE = 0.12


def _active_question_families(environment: Mapping[str, str]) -> tuple[str, ...]:
    raw = environment.get("DIGITALPSYCHOLOGY_SYSTEM_ONE_QUESTIONS",
                          ",".join(QUESTION_REGISTRY))
    values = tuple(item.strip() for item in raw.split(",") if item.strip())
    if (not values or len(values) != len(set(values))
            or any(item not in QUESTION_REGISTRY for item in values)):
        raise ValueError("configured System-1 question families are invalid")
    return values


def laya_doctor_report(environment: Mapping[str, str] | None = None) -> dict[str, Any]:
    env = dict(os.environ if environment is None else environment)
    try:
        laya_import = importlib.util.find_spec("laya") is not None
    except (ImportError, ValueError):
        laya_import = False

    model_config = model_settings(env)
    model_path_value = model_config["model_path"]
    revision = model_config["model_revision"]
    expected_hash = model_config["model_content_sha256"]
    model_id = model_config["model_id"]
    device = model_config["device"]
    checkpoint_path = Path(model_path_value).expanduser() if model_path_value else None
    checkpoint_directory = bool(checkpoint_path and checkpoint_path.is_dir())
    computed_hash: str | None = None
    hash_error: str | None = None
    if checkpoint_directory:
        try:
            computed_hash = model_content_digest(checkpoint_path)
        except (OSError, RuntimeError, ValueError) as exc:
            hash_error = str(exc)
    hash_matches = bool(
        len(expected_hash) == 64
        and all(char in "0123456789abcdef" for char in expected_hash)
        and computed_hash == expected_hash)

    mode = env.get("DIGITALPSYCHOLOGY_SYSTEM_ONE_MODE", "disabled").strip()
    if mode not in {"disabled", "shadow", "advisory"}:
        mode = "invalid"
    try:
        enabled_families = _active_question_families(env)
        family_error = None
    except ValueError as exc:
        enabled_families = ()
        family_error = str(exc)

    calibration_path_value = env.get("DIGITALPSYCHOLOGY_SYSTEM_ONE_CALIBRATION_PATH", "")
    calibration_configured = bool(calibration_path_value)
    calibration_valid = False
    calibration_error: str | None = None
    qualified: dict[str, str] = {family: "MISSING" for family in QUESTION_REGISTRY}
    if calibration_configured:
        path = Path(calibration_path_value).expanduser()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            from .system_one_calibration import CalibrationBundle
            bundle = CalibrationBundle.from_mapping(raw)
            calibration_valid = True
            for family, record in bundle.records.items():
                matches = bool(
                    record.backend_id == "digitalpsychology-laya-native"
                    and record.model_id == model_id
                    and record.model_revision == revision
                    and record.model_content_hash == expected_hash
                    and record.provider_protocol_hash == PROVIDER_PROTOCOL_HASH
                    and record.qualification_passed
                    and record.drift_status == "clear")
                qualified[family] = "ACTIVE" if matches else "SHIFTED"
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            calibration_error = str(exc)

    device_valid = device in {"cpu", "cuda", "mps", "xpu"}
    runtime_ready = bool(
        laya_import and checkpoint_directory and hash_matches and revision
        and model_id and device_valid)
    qualified_enabled = [family for family in enabled_families
                         if qualified.get(family) == "ACTIVE"]
    missing_enabled = [family for family in enabled_families
                       if qualified.get(family) != "ACTIVE"]
    if not runtime_ready or not calibration_valid:
        advisory_readiness = "FAIL"
    elif qualified_enabled and missing_enabled:
        advisory_readiness = "PARTIAL"
    elif qualified_enabled:
        advisory_readiness = "PASS"
    else:
        advisory_readiness = "FAIL"
    if mode == "invalid" or family_error:
        advisory_readiness = "FAIL"
    return {
        "laya_import_present": laya_import,
        "checkpoint_directory_exists": checkpoint_directory,
        "checkpoint_path": str(checkpoint_path) if checkpoint_path else None,
        "expected_checkpoint_hash": expected_hash or None,
        "computed_checkpoint_hash": computed_hash,
        "checkpoint_hash_matches": hash_matches,
        "checkpoint_hash_error": hash_error,
        "model_id": model_id,
        "model_revision": revision or None,
        "device": device,
        "device_valid": device_valid,
        "provider_protocol_hash": PROVIDER_PROTOCOL_HASH,
        "calibration_path_configured": calibration_configured,
        "calibration_bundle_valid": calibration_valid,
        "calibration_error": calibration_error,
        "qualified_question_families": qualified,
        "enabled_question_families": list(enabled_families),
        "question_family_error": family_error,
        "mode": mode,
        "shadow_readiness": "PASS" if runtime_ready else "FAIL",
        "advisory_readiness": advisory_readiness,
        "advisory_enabled_families": qualified_enabled,
        "advisory_shadow_only_families": missing_enabled,
    }


def _format_doctor(report: Mapping[str, Any]) -> str:
    def state(name: str) -> str:
        return "PASS" if report[name] else "FAIL"

    required_mode = report.get(
        "required_mode", "advisory" if report["mode"] == "advisory" else "shadow")
    required_readiness = report.get(
        "required_readiness", report[f"{required_mode}_readiness"])

    lines = [
        f"Laya runtime: {state('laya_import_present')}",
        f"checkpoint directory: {state('checkpoint_directory_exists')}",
        f"expected checkpoint hash: {report['expected_checkpoint_hash'] or 'MISSING'}",
        f"computed checkpoint hash: {report['computed_checkpoint_hash'] or 'UNAVAILABLE'}",
        f"checkpoint identity: {state('checkpoint_hash_matches')}",
        f"model ID: {report['model_id']}",
        f"model revision: {report['model_revision'] or 'MISSING'}",
        f"device: {report['device']}",
        f"provider protocol hash: {report['provider_protocol_hash']}",
        "calibration file configured: "
        f"{'YES' if report['calibration_path_configured'] else 'NO'}",
        "calibration bundle valid: "
        f"{state('calibration_bundle_valid') if report['calibration_path_configured'] else 'N/A'}",
    ]
    for family, status in report["qualified_question_families"].items():
        lines.append(f"{family} calibration: {status}")
    lines.extend((f"mode: {report['mode']}",
                  f"shadow readiness: {report['shadow_readiness']}",
                  f"advisory readiness: {report['advisory_readiness']}",
                  f"required readiness ({required_mode}): {required_readiness}"))
    if report["advisory_shadow_only_families"]:
        lines.append("advisory shadow-only families: " + ", ".join(
            report["advisory_shadow_only_families"]))
    for key in ("checkpoint_hash_error", "calibration_error", "question_family_error"):
        if report.get(key):
            lines.append(f"{key.replace('_', ' ')}: {report[key]}")
    return "\n".join(lines)


def laya_doctor_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check optional Laya runtime readiness")
    parser.add_argument("--json", action="store_true", help="print a machine-readable report")
    parser.add_argument("--require", choices=("shadow", "advisory"),
                        help="readiness mode required for a successful exit")
    parser.add_argument("--require-all-enabled-families", action="store_true",
                        help="require every configured question family to have active calibration")
    args = parser.parse_args(argv)
    report = laya_doctor_report()
    if args.require == "shadow" and args.require_all_enabled_families:
        parser.error("--require-all-enabled-families requires advisory readiness")
    required_mode = args.require or (
        "advisory" if args.require_all_enabled_families or report["mode"] == "advisory"
        else "shadow")
    required_readiness = report[f"{required_mode}_readiness"]
    if required_mode == "shadow":
        required_pass = required_readiness == "PASS"
    else:
        required_pass = required_readiness == "PASS" or (
            required_readiness == "PARTIAL" and not args.require_all_enabled_families)
    report["required_mode"] = required_mode
    report["required_readiness"] = required_readiness
    report["require_all_enabled_families"] = args.require_all_enabled_families
    report["required_readiness_pass"] = required_pass
    print(json.dumps(report, sort_keys=True, indent=2) if args.json
          else _format_doctor(report))
    return 0 if required_pass else 1


def _laya_smoke_frame(service: SystemOneService, question_family: str) -> SystemOneFrame:
    """Build one stable, bounded synthetic frame for checking the native path."""
    definition = QUESTION_REGISTRY[question_family]
    capability = service.capability()
    identity = SystemOneIdentity(
        namespace_id="digital-psychology-smoke",
        application_id="digital-psychology",
        application_instance_id="laya-smoke",
        agent_id="laya-smoke-agent",
        agent_instance_id="laya-smoke-agent-1",
        session_id="laya-smoke-session",
        task_id="laya-smoke-task",
        attempt_id="laya-smoke-attempt",
    )
    activity = ActivityFact(
        sequence=1, activity_type="action", action_class="verification",
        host_declared_relation="ambiguous", target_kind="workspace",
        proposed_purpose="verify the requested behavior",
        affected_criterion_ids=("smoke-criterion",), relation_certainty="unknown",
    )
    state = SemanticState(
        task={
            "objective": "Verify that the requested behavior is implemented.",
            "work_mode": "delivery", "deliverable_kind": "code_behavior",
            "task_complexity": "standard", "phase": "verification",
            "active_segment": "smoke-check", "active_statework": None,
            "active_flow": None, "non_goals": ["unrelated changes"],
            "domain_tags": ["software"],
        },
        situation={
            "current_state": "VERIFY", "blockers": [],
            "actions_since_goal_progress": 1,
            "actions_since_artifact_change": 1,
            "verification_actions_since_artifact_change": 0,
            "recovery_epochs": 0, "repeated_branch_count": 0,
        },
        criteria=(CriterionState(
            "smoke-criterion", "Requested behavior works", "functional"),),
        activity=(activity,), workflows=(), convergence=(), candidate=activity,
        legal_routes=("implement", "diagnose", "verify"),
    )
    questions = (QuestionSpec(
        question_id=question_family, family=definition["family"],
        question_type=definition["type"], instructions=definition["instructions"],
        criteria=tuple(definition["criteria"].items()),
    ),)
    state_digest = canonical_hash(state.to_dict())
    question_digest = canonical_hash([item.to_dict() for item in questions])
    policy_hash = canonical_hash({"smoke": "laya-native", "version": 1})
    sequence = 1
    request_digest = canonical_hash({
        "identity": identity.to_dict(), "sequence": sequence,
        "state_digest": state_digest,
        "question_bundle_digest": question_digest,
        "policy_hash": policy_hash,
    })
    return SystemOneFrame(
        identity=identity, request_id="laya-smoke-request", sequence=sequence,
        state_digest=state_digest, question_bundle_digest=question_digest,
        request_digest=request_digest, policy_hash=policy_hash,
        backend_id=str(capability["backend_id"]), model_id=str(capability["model_id"]),
        model_revision=str(capability["model_revision"]),
        model_content_hash=str(capability["model_content_hash"]),
        provider_protocol_hash=str(capability["provider_protocol_hash"]),
        calibration_bundle_hash=service.calibration_bundle_hash,
        state=state, questions=questions,
    )


def laya_smoke_report(question_family: str = "progress_relation") -> dict[str, Any]:
    """Load and exercise one pinned native Laya inference in shadow mode."""
    started = time.monotonic()
    provider: LayaProvider | None = None
    service: SystemOneService | None = None
    base: dict[str, Any] = {
        "schema_version": "laya-runtime-smoke/v1",
        "question_family": question_family,
        "mode": "shadow",
        "runtime_loaded": False,
        "typed_judgment": False,
        "efficacy_claim": False,
    }
    try:
        if question_family not in QUESTION_REGISTRY:
            raise ValueError("question family is not registered")
        provider = LayaProvider.from_environment()
        base.update({
            "checkpoint_hash": provider.model_content_hash,
            "backend_id": provider.backend_id,
            "model_id": provider.model_id,
            "model_revision": provider.model_revision,
            "device": provider.device,
        })
        service = SystemOneService(
            provider, mode="shadow", enabled_question_families=(question_family,))
        base["warmup_latency_ms"] = provider.warmup_and_wait()
        base["runtime_loaded"] = True
        frame = _laya_smoke_frame(service, question_family)
        inference_started = time.monotonic()
        result = service.judge(frame, mode="shadow")
        base["inference_latency_ms"] = round(
            (time.monotonic() - inference_started) * 1000)
        judgment = result.judgment
        if result.status != "shadow" or not isinstance(judgment, SystemOneJudgment):
            raise RuntimeError(
                f"shadow inference did not return a typed judgment (status={result.status})")
        answer = judgment.answer(question_family)
        if answer is None or answer.selected not in {
                label for label, _description in frame.questions[0].criteria}:
            raise RuntimeError("shadow inference returned an invalid typed answer")
        base.update({
            "success": True,
            "typed_judgment": True,
            "status": result.status,
            "selected": answer.selected,
            "latency_ms": round((time.monotonic() - started) * 1000),
            "calibrated": bool(answer.calibrated_probabilities),
        })
    except Exception as exc:
        base.update({
            "success": False,
            "status": "failed",
            "error": f"{type(exc).__name__}: {exc}",
            "latency_ms": round((time.monotonic() - started) * 1000),
        })
    finally:
        try:
            if service is not None:
                service.close()
            elif provider is not None:
                provider.close()
        except Exception as exc:
            base["success"] = False
            base["status"] = "failed"
            base.setdefault("error", f"provider close failed: {type(exc).__name__}: {exc}")
    return base


def laya_smoke_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Load a pinned Laya checkpoint and run one bounded shadow inference")
    parser.add_argument("--json", action="store_true", help="print a machine-readable report")
    parser.add_argument("--question-family", default="progress_relation",
                        choices=tuple(QUESTION_REGISTRY),
                        help="registered question family for the smoke frame")
    args = parser.parse_args(argv)
    report = laya_smoke_report(args.question_family)
    print(json.dumps(report, sort_keys=True, indent=2) if args.json else
          "\n".join(f"{key}: {value}" for key, value in report.items()))
    return 0 if report["success"] else 1


def _read_private_json(path: Path, *, description: str) -> Any:
    supplied = path.expanduser()
    if supplied.is_symlink():
        raise ValueError(f"{description} must not be a symlink")
    try:
        info = supplied.stat()
    except OSError as exc:
        raise ValueError(f"{description} is unavailable") from exc
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600):
        raise ValueError(f"{description} must be a user-owned mode-0600 regular file")
    try:
        return json.loads(supplied.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{description} contains invalid JSON") from exc


def _trusted_output_parent(path: Path) -> Path:
    parent = path.expanduser().absolute().parent
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if any(ancestor.is_symlink() for ancestor in (parent, *parent.parents)):
        raise ValueError("private JSON output directory must not traverse symlinks")
    if (parent.stat().st_uid != os.getuid() or parent.stat().st_mode & 0o022):
        raise ValueError("private JSON output directory is not trusted")
    return parent


def _bundle_file_digest(path: Path) -> str:
    raw = _read_private_json(path, description="existing active bundle")
    return CalibrationBundle.from_mapping(raw).digest


def _write_active_bundle(path: Path, bundle: CalibrationBundle, *,
                         expected_file_hash: str | None) -> None:
    """Atomically export an active bundle if its current file still matches."""
    parent = _trusted_output_parent(path)
    target = parent / path.name

    def check_precondition() -> None:
        if target.is_symlink():
            raise ValueError("active bundle output must not be a symlink")
        if target.exists():
            if expected_file_hash is None:
                raise ValueError("active bundle output appeared during activation")
            if _bundle_file_digest(target) != expected_file_hash:
                raise ValueError("active bundle output changed during activation")
        elif expected_file_hash is not None:
            raise ValueError("active bundle output disappeared during activation")

    check_precondition()
    fd, temporary = tempfile.mkstemp(prefix=".laya-active-bundle-", dir=parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(bundle.to_dict(), stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        check_precondition()
        os.replace(temporary, target)
        directory_fd = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def laya_activate_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Activate a qualified Laya calibration candidate after host review")
    parser.add_argument("--candidate", required=True,
                        help="mode-0600 candidate CalibrationRecord JSON")
    parser.add_argument("--candidate-digest", required=True,
                        help="expected SHA-256 digest printed by laya-qualify")
    parser.add_argument("--question-family", required=True,
                        choices=tuple(QUESTION_REGISTRY))
    parser.add_argument("--lifecycle", required=True,
                        help="mode-0600 calibration lifecycle state path")
    parser.add_argument("--output", required=True,
                        help="runtime active bundle path to create or replace")
    parser.add_argument("--authority-ref", required=True,
                        help="independent host review reference for this activation")
    parser.add_argument("--expected-prior-hash", required=True,
                        help="current active bundle digest, or 'none' when no family is active")
    args = parser.parse_args(argv)

    try:
        candidate_path = Path(args.candidate).expanduser()
        lifecycle_path = Path(args.lifecycle).expanduser()
        output_path = Path(args.output).expanduser()
        if not args.authority_ref or len(args.authority_ref) > 256:
            raise ValueError("activation requires a bounded independent host review reference")
        path_keys = {str(path.absolute()) for path in
                     (candidate_path, lifecycle_path, output_path)}
        if len(path_keys) != 3:
            raise ValueError("candidate, lifecycle, and active bundle paths must be distinct")

        raw_candidate = _read_private_json(candidate_path, description="calibration candidate")
        candidate = CalibrationRecord.from_mapping(raw_candidate)
        if candidate.digest != args.candidate_digest:
            raise ValueError("candidate digest does not match --candidate-digest")
        if candidate.status != "candidate" or candidate.question_id != args.question_family:
            raise ValueError("candidate status or question family is not eligible")
        if not candidate.qualification_passed or candidate.drift_status != "clear":
            raise ValueError("candidate does not pass the independent qualification gate")
        if candidate.backend_id != "digitalpsychology-laya-native":
            raise ValueError("candidate was not qualified for the native Laya provider")
        if candidate.provider_protocol_hash != PROVIDER_PROTOCOL_HASH:
            raise ValueError("candidate provider protocol differs from this runtime")
        model_config = model_settings()
        identity_pins = {
            "model_id": model_config["model_id"],
            "model_revision": model_config["model_revision"],
            "model_content_hash": model_config["model_content_sha256"],
        }
        if (not identity_pins["model_revision"]
                or len(identity_pins["model_content_hash"]) != 64
                or any(char not in "0123456789abcdef"
                       for char in identity_pins["model_content_hash"])):
            raise ValueError("activation requires a pinned Laya model revision and SHA-256")
        for field, expected in identity_pins.items():
            if getattr(candidate, field) != expected:
                raise ValueError(f"candidate {field} differs from the configured Laya pin")

        _trusted_output_parent(lifecycle_path)
        _trusted_output_parent(output_path)
        lifecycle = SystemOneCalibrationLifecycle(lifecycle_path)
        revision = lifecycle.revision()
        before_bundle = lifecycle.active_bundle()
        prior_hash = before_bundle.digest
        if args.expected_prior_hash == "none":
            supplied_prior = ""
        elif len(args.expected_prior_hash) == 64 and not any(
                char not in "0123456789abcdef" for char in args.expected_prior_hash):
            supplied_prior = args.expected_prior_hash
        else:
            raise ValueError("expected prior hash must be a lowercase SHA-256 digest or 'none'")
        if supplied_prior != prior_hash:
            raise ValueError("expected prior hash differs from the active lifecycle bundle")

        output_exists = output_path.exists() or output_path.is_symlink()
        output_prior_hash: str | None = None
        if output_exists:
            output_prior_hash = _bundle_file_digest(output_path)
            if output_prior_hash != prior_hash:
                raise ValueError("exported active bundle differs from lifecycle state")

        staged = lifecycle.stage(candidate, expected_revision=revision)
        activation = lifecycle.activate(
            staged["candidate_id"], question_id=args.question_family,
            authority_ref=args.authority_ref, expected_revision=staged["revision"])
        active_bundle = lifecycle.active_bundle()
        active_record = active_bundle.records.get(args.question_family)
        expected_active = candidate.with_status("active")
        if (active_record is None or active_record.digest != expected_active.digest
                or active_record.status != "active" or not active_record.qualification_passed
                or active_record.drift_status != "clear"):
            raise CalibrationLifecycleError("activated bundle failed post-activation validation")
        _write_active_bundle(output_path, active_bundle,
                             expected_file_hash=output_prior_hash)
        receipt = {
            "schema_version": "laya-activation-receipt/v1",
            "success": True,
            "activated_at": datetime.now(timezone.utc).isoformat(),
            "question_family": args.question_family,
            "candidate_digest": candidate.digest,
            "model_id": active_record.model_id,
            "model_revision": active_record.model_revision,
            "checkpoint_hash": active_record.model_content_hash,
            "active_calibration_digest": active_record.digest,
            "prior_bundle_hash": prior_hash or None,
            "active_bundle_hash": active_bundle.digest,
            "lifecycle_revision": activation["revision"],
            "authority_ref": args.authority_ref,
            "lifecycle_path": str(lifecycle_path),
            "active_bundle_path": str(output_path),
            "active_bundle_mode": "0600",
        }
        print(json.dumps(receipt, sort_keys=True, indent=2))
        return 0
    except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
        print(f"activation failed: {exc}", file=sys.stderr)
        return 1


def load_authenticated_system_one_events(path: str | Path) -> list[Any]:
    """Read only authenticated decision/outcome events from the event store."""
    supplied_path = Path(path).expanduser()
    if supplied_path.is_symlink():
        raise ValueError("event database must not be a symlink")
    event_path = supplied_path.resolve(strict=True)
    if not event_path.is_file():
        raise ValueError("event database must be a regular file")
    uri = event_path.as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as database:
        rows = database.execute(
            "SELECT e.payload FROM events AS e "
            "JOIN authenticated_events AS a "
            "ON a.namespace_id=e.namespace_id "
            "AND a.application_instance_id=e.application_instance_id "
            "AND a.event_id=e.event_id "
            "ORDER BY e.timestamp, e.row_id").fetchall()
    events = []
    for (encoded,) in rows:
        try:
            event = EventFactory.from_dict(json.loads(encoded))
        except (TypeError, json.JSONDecodeError, ValueError) as exc:
            raise ValueError("event database contains a malformed authenticated event") from exc
        if event.event_type in {"system_one_decision", "system_one_outcome"}:
            events.append(event)
    return events


def laya_corpus_status(path: str | Path) -> dict[str, Any]:
    """Summarize a trusted event corpus without returning event payloads."""
    supplied_path = Path(path).expanduser()
    if supplied_path.is_symlink():
        raise ValueError("event database must not be a symlink")
    event_path = supplied_path.resolve(strict=True)
    if not event_path.is_file():
        raise ValueError("event database must be a regular file")
    event_counts: dict[str, dict[str, int]] = {}
    with sqlite3.connect(event_path.as_uri() + "?mode=ro", uri=True) as database:
        rows = database.execute(
            "SELECT e.payload, CASE WHEN a.event_id IS NULL THEN 0 ELSE 1 END "
            "FROM events AS e LEFT JOIN authenticated_events AS a "
            "ON a.namespace_id=e.namespace_id "
            "AND a.application_instance_id=e.application_instance_id "
            "AND a.event_id=e.event_id ORDER BY e.row_id").fetchall()
    for encoded, authenticated in rows:
        try:
            event = json.loads(encoded)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("event database contains a malformed event") from exc
        event_type = event.get("event_type") if isinstance(event, dict) else None
        if not isinstance(event_type, str) or not event_type:
            event_type = "unknown"
        counts = event_counts.setdefault(event_type, {"total": 0, "authenticated": 0})
        counts["total"] += 1
        counts["authenticated"] += int(bool(authenticated))

    trusted_events = load_authenticated_system_one_events(event_path)
    return {
        "schema_version": "laya-corpus-status/v1",
        "event_database_path": str(event_path),
        "read_only": True,
        "event_count": sum(item["total"] for item in event_counts.values()),
        "authenticated_event_count": sum(
            item["authenticated"] for item in event_counts.values()),
        "event_type_counts": event_counts,
        "system_one_event_count": len(trusted_events),
        "question_families": calibration_corpus_counts(trusted_events),
    }


def laya_corpus_status_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Report authenticated Laya corpus counts without event content")
    parser.add_argument("--event-db", default=os.environ.get(
        "DIGITALPSYCHOLOGY_EVENT_DB", str(trusted_state_root() / "events.sqlite3")))
    args = parser.parse_args(argv)
    try:
        report = laya_corpus_status(args.event_db)
    except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
        print(f"corpus status failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0


def _write_candidate(path: Path, record: CalibrationRecord) -> None:
    path = path.expanduser()
    parent = _trusted_output_parent(path)
    target = parent / path.name
    if target.exists() or target.is_symlink():
        raise ValueError("candidate output already exists; choose a new path")
    fd, temporary = tempfile.mkstemp(prefix=".laya-candidate-", dir=parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(record.canonical(), stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        if target.exists() or target.is_symlink():
            raise ValueError("candidate output appeared during qualification")
        os.replace(temporary, target)
        directory_fd = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _write_corpus_manifest(path: Path, *, corpus_id: str, question_family: str,
                           candidate_digest: str,
                           task_instance_hashes: tuple[str, ...],
                           task_fixture_hashes: Mapping[str, str]) -> dict[str, Any]:
    task_hashes = list(task_instance_hashes)
    fixtures = dict(task_fixture_hashes)
    if (not isinstance(corpus_id, str) or not corpus_id or len(corpus_id) > 256
            or question_family not in QUESTION_REGISTRY
            or len(candidate_digest) != 64
            or any(char not in "0123456789abcdef" for char in candidate_digest)
            or not task_hashes or len(task_hashes) != len(set(task_hashes))
            or any(len(value) != 64 or any(char not in "0123456789abcdef"
                                           for char in value) for value in task_hashes)
            or set(fixtures) != set(task_hashes)
            or any(not isinstance(value, str) or len(value) != 64
                   or any(char not in "0123456789abcdef" for char in value)
                   for value in fixtures.values())
            or len(set(fixtures.values())) < PRODUCTION_MINIMUM_INDEPENDENT_FIXTURES):
        raise ValueError("calibration corpus identity is malformed")
    task_hashes.sort()
    task_fixtures = [{"task_instance_hash": task_hash,
                      "fixture_hash": fixtures[task_hash]}
                     for task_hash in task_hashes]
    manifest: dict[str, Any] = {
        "schema_version": "laya-calibration-corpus/v2",
        "corpus_id": corpus_id,
        "question_family": question_family,
        "candidate_digest": candidate_digest,
        "task_instance_hashes": task_hashes,
        "task_fixture_hashes": task_fixtures,
        "independent_fixture_count": len(set(fixtures.values())),
        "fixture_set_hash": canonical_hash({"fixtures": task_fixtures}),
    }
    manifest["manifest_digest"] = canonical_hash(manifest)
    path = path.expanduser()
    parent = _trusted_output_parent(path)
    target = parent / path.name
    if target.exists() or target.is_symlink():
        raise ValueError("corpus manifest output already exists; choose a new path")
    fd, temporary = tempfile.mkstemp(prefix=".laya-corpus-manifest-", dir=parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(manifest, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        if target.exists() or target.is_symlink():
            raise ValueError("corpus manifest output appeared during qualification")
        os.replace(temporary, target)
        directory_fd = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return manifest


def laya_qualify_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fit and evaluate one Laya question-family calibration candidate")
    parser.add_argument("--event-db", default=os.environ.get(
        "DIGITALPSYCHOLOGY_EVENT_DB", str(trusted_state_root() / "events.sqlite3")))
    parser.add_argument("--question-family", required=True,
                        choices=tuple(QUESTION_REGISTRY))
    parser.add_argument("--output", required=True,
                        help="new candidate CalibrationRecord JSON path")
    parser.add_argument("--corpus-id",
                        help="stable identity for the qualification corpus")
    parser.add_argument("--corpus-manifest-out", type=Path,
                        help="write a private task-instance manifest for overlap rejection")
    parser.add_argument("--minimum-outcomes", type=int,
                        default=PRODUCTION_MINIMUM_OUTCOMES)
    parser.add_argument("--minimum-trajectories", type=int,
                        default=PRODUCTION_MINIMUM_TRAJECTORIES)
    parser.add_argument("--minimum-fixtures", type=int,
                        default=PRODUCTION_MINIMUM_INDEPENDENT_FIXTURES)
    parser.add_argument("--minimum-cohort-trajectories", type=int,
                        default=PRODUCTION_MINIMUM_COHORT_TRAJECTORIES)
    parser.add_argument("--minimum-accuracy", type=float,
                        default=PRODUCTION_MINIMUM_ACCURACY)
    parser.add_argument("--maximum-brier", type=float,
                        default=PRODUCTION_MAXIMUM_BRIER)
    parser.add_argument("--maximum-ece", type=float,
                        default=PRODUCTION_MAXIMUM_ECE)
    parser.add_argument("--minimum-confidence", type=float, default=0.9)
    args = parser.parse_args(argv)
    if bool(args.corpus_id) != bool(args.corpus_manifest_out):
        parser.error("--corpus-id and --corpus-manifest-out must be supplied together")
    if args.corpus_id and len(args.corpus_id) > 256:
        parser.error("--corpus-id must be at most 256 characters")
    if (args.minimum_outcomes < PRODUCTION_MINIMUM_OUTCOMES
            or args.minimum_trajectories < PRODUCTION_MINIMUM_TRAJECTORIES
            or args.minimum_fixtures < PRODUCTION_MINIMUM_INDEPENDENT_FIXTURES
            or args.minimum_cohort_trajectories < PRODUCTION_MINIMUM_COHORT_TRAJECTORIES
            or args.minimum_accuracy < PRODUCTION_MINIMUM_ACCURACY
            or args.maximum_brier > PRODUCTION_MAXIMUM_BRIER
            or args.maximum_ece > PRODUCTION_MAXIMUM_ECE):
        parser.error("qualification settings cannot weaken production thresholds")
    if not 0.9 <= args.minimum_confidence <= 1.0:
        parser.error("minimum confidence must be between 0.9 and 1.0")

    try:
        events = load_authenticated_system_one_events(args.event_db)
        metrics = calibration_metrics(events, label=args.question_family)
        record = calibration_record_from_events(
            events,
            question_id=args.question_family,
            minimum_confidence=args.minimum_confidence,
            minimum_outcomes=args.minimum_outcomes,
            minimum_independent_trajectories=args.minimum_trajectories,
            minimum_independent_fixtures=args.minimum_fixtures,
            minimum_cohort_trajectories=args.minimum_cohort_trajectories,
            minimum_accuracy=args.minimum_accuracy,
            maximum_brier=args.maximum_brier,
            maximum_ece=args.maximum_ece,
        )
        output_path = Path(args.output)
        if output_path.exists() or output_path.is_symlink():
            raise ValueError("candidate output already exists; choose a new path")
        _trusted_output_parent(output_path)
        manifest_path = (args.corpus_manifest_out.expanduser()
                         if args.corpus_manifest_out is not None else None)
        if manifest_path is not None:
            if manifest_path.absolute() == output_path.expanduser().absolute():
                raise ValueError("candidate and corpus manifest paths must be distinct")
            if manifest_path.exists() or manifest_path.is_symlink():
                raise ValueError("corpus manifest output already exists; choose a new path")
            _trusted_output_parent(manifest_path)
        _write_candidate(output_path, record)
        corpus_manifest = None
        if manifest_path is not None:
            task_hashes = calibration_task_instance_hashes(
                events, question_id=args.question_family)
            task_fixtures = calibration_task_fixture_hashes(
                events, question_id=args.question_family)
            corpus_manifest = _write_corpus_manifest(
                manifest_path, corpus_id=args.corpus_id,
                question_family=args.question_family,
                candidate_digest=record.digest,
                task_instance_hashes=task_hashes,
                task_fixture_hashes=task_fixtures)
    except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
        print(f"qualification failed: {exc}", file=sys.stderr)
        return 1

    print(json.dumps({
        "question_family": args.question_family,
        "status": record.status,
        "candidate_digest": record.digest,
        "output": str(output_path),
        "metrics": metrics,
        "holdout": {
            "accuracy": record.holdout_accuracy,
            "brier_score": record.holdout_brier,
            "expected_calibration_error": record.holdout_ece,
            "log_loss": record.holdout_log_loss,
        },
        "counts": {
            "independent_outcomes": record.sample_count,
            "independent_trajectories": record.independent_trajectory_count,
            "independent_fixtures": record.independent_fixture_count,
            "training": record.training_count,
            "calibration": record.calibration_count,
            "holdout": record.holdout_count,
            "training_fixtures": record.training_fixture_count,
            "calibration_fixtures": record.calibration_fixture_count,
            "holdout_fixtures": record.holdout_fixture_count,
        },
        "activation": "candidate only; requires independent host review",
        "corpus_manifest": (corpus_manifest if corpus_manifest is not None else None),
    }, sort_keys=True, indent=2))
    return 0


__all__ = ["laya_doctor_main", "laya_doctor_report", "laya_qualify_main",
           "laya_activate_main", "laya_smoke_main", "laya_smoke_report",
           "laya_corpus_status", "laya_corpus_status_main",
           "_write_corpus_manifest",
           "load_authenticated_system_one_events"]
