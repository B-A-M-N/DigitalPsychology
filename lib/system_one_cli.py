"""Operator preflight and per-family qualification commands for Laya."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from typing import Any, Mapping

from .feedback_loop import EventFactory, trusted_state_root
from .protocol_artifact import QUESTION_REGISTRY
from .system_one_backends import (PROVIDER_PROTOCOL_HASH, model_content_digest)
from .system_one_calibration import (CalibrationRecord, calibration_metrics,
                                     calibration_record_from_events)


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

    model_path_value = env.get("DIGITALPSYCHOLOGY_LAYA_MODEL_PATH", "")
    revision = env.get("DIGITALPSYCHOLOGY_LAYA_MODEL_REVISION", "")
    expected_hash = env.get("DIGITALPSYCHOLOGY_LAYA_MODEL_SHA256", "")
    model_id = env.get("DIGITALPSYCHOLOGY_LAYA_MODEL_ID", "laya-typed-decisions")
    device = env.get("DIGITALPSYCHOLOGY_LAYA_DEVICE", "cpu")
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
                  f"advisory readiness: {report['advisory_readiness']}"))
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
    args = parser.parse_args(argv)
    report = laya_doctor_report()
    print(json.dumps(report, sort_keys=True, indent=2) if args.json
          else _format_doctor(report))
    return 0 if report["shadow_readiness"] == "PASS" else 1


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


def _write_candidate(path: Path, record: CalibrationRecord) -> None:
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".laya-candidate-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(record.canonical(), stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
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


def laya_qualify_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fit and evaluate one Laya question-family calibration candidate")
    parser.add_argument("--event-db", default=os.environ.get(
        "DIGITALPSYCHOLOGY_EVENT_DB", str(trusted_state_root() / "events.sqlite3")))
    parser.add_argument("--question-family", required=True,
                        choices=tuple(QUESTION_REGISTRY))
    parser.add_argument("--output", required=True,
                        help="new candidate CalibrationRecord JSON path")
    parser.add_argument("--minimum-outcomes", type=int,
                        default=PRODUCTION_MINIMUM_OUTCOMES)
    parser.add_argument("--minimum-trajectories", type=int,
                        default=PRODUCTION_MINIMUM_TRAJECTORIES)
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
    if (args.minimum_outcomes < PRODUCTION_MINIMUM_OUTCOMES
            or args.minimum_trajectories < PRODUCTION_MINIMUM_TRAJECTORIES
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
            minimum_cohort_trajectories=args.minimum_cohort_trajectories,
            minimum_accuracy=args.minimum_accuracy,
            maximum_brier=args.maximum_brier,
            maximum_ece=args.maximum_ece,
        )
        output_path = Path(args.output)
        if output_path.exists():
            raise ValueError("candidate output already exists; choose a new path")
        _write_candidate(output_path, record)
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
            "training": record.training_count,
            "calibration": record.calibration_count,
            "holdout": record.holdout_count,
        },
        "activation": "candidate only; requires independent host review",
    }, sort_keys=True, indent=2))
    return 0


__all__ = ["laya_doctor_main", "laya_doctor_report", "laya_qualify_main",
           "load_authenticated_system_one_events"]
