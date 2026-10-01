"""Host-only calibration candidate, activation, drift, and rollback lifecycle."""
from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import threading
from typing import Any, Iterable, Mapping

from .system_one_calibration import (CalibrationBundle, CalibrationRecord,
                                     _ece, _paired_rows, _brier)


class CalibrationLifecycleError(ValueError):
    pass


class SystemOneCalibrationLifecycle:
    """Durably manages one active calibration per System-1 question family.

    This API is for the trusted host/service. It is not registered as an MCP
    tool. Candidate fitting already uses a held-out partition; activation
    additionally records the host's independent review reference.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()

    def _empty(self) -> dict[str, Any]:
        return {"schema_version": "1.0.0", "revision": 0,
                "records": {}, "active": {}, "history": []}

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._empty()
        if self.path.is_symlink() or not self.path.is_file():
            raise CalibrationLifecycleError("calibration lifecycle path must be a regular file")
        info = self.path.stat()
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise CalibrationLifecycleError("calibration lifecycle file must be user-owned mode 0600")
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CalibrationLifecycleError("calibration lifecycle file is unreadable") from exc
        if not isinstance(value, dict) or set(value) != {
                "schema_version", "revision", "records", "active", "history"}:
            raise CalibrationLifecycleError("calibration lifecycle state is malformed")
        if value["schema_version"] != "1.0.0" or not isinstance(value["revision"], int):
            raise CalibrationLifecycleError("calibration lifecycle version is unsupported")
        if not isinstance(value["records"], dict) or not isinstance(value["active"], dict):
            raise CalibrationLifecycleError("calibration lifecycle maps are malformed")
        if not isinstance(value["history"], list):
            raise CalibrationLifecycleError("calibration lifecycle history is malformed")
        for digest, raw in value["records"].items():
            record = CalibrationRecord.from_mapping(raw)
            if record.digest != digest:
                raise CalibrationLifecycleError("calibration record content hash mismatch")
        return value

    def _write(self, value: Mapping[str, Any]) -> None:
        parent = self.path.parent
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if parent.is_symlink() or parent.stat().st_uid != os.getuid() or parent.stat().st_mode & 0o022:
            raise CalibrationLifecycleError("calibration lifecycle directory is not trusted")
        fd, temporary = tempfile.mkstemp(prefix=".system-one-calibration-", dir=parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(value, stream, sort_keys=True, separators=(",", ":"))
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
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

    def revision(self) -> int:
        with self._lock:
            return int(self._read()["revision"])

    def stage(self, record: CalibrationRecord, *, expected_revision: int) -> dict[str, Any]:
        if record.status != "candidate":
            raise CalibrationLifecycleError("only a held-out candidate can be staged")
        with self._lock:
            state = self._read()
            if state["revision"] != expected_revision:
                raise CalibrationLifecycleError("calibration lifecycle revision changed")
            candidate_id = record.digest
            state["records"][candidate_id] = record.canonical()
            state["revision"] += 1
            self._write(state)
            return {"candidate_id": candidate_id, "revision": state["revision"]}

    def activate(self, candidate_id: str, *, question_id: str,
                 authority_ref: str, expected_revision: int) -> dict[str, Any]:
        if not authority_ref or len(authority_ref) > 256:
            raise CalibrationLifecycleError("activation requires an independent host review reference")
        with self._lock:
            state = self._read()
            if state["revision"] != expected_revision:
                raise CalibrationLifecycleError("calibration lifecycle revision changed")
            raw = state["records"].get(candidate_id)
            if raw is None:
                raise CalibrationLifecycleError("calibration candidate is unknown")
            candidate = CalibrationRecord.from_mapping(raw)
            if candidate.status != "candidate" or candidate.question_id != question_id:
                raise CalibrationLifecycleError("candidate is not eligible for this question family")
            previous_id = state["active"].get(question_id)
            if previous_id:
                previous = CalibrationRecord.from_mapping(state["records"][previous_id])
                stale = replace(previous, status="stale")
                state["records"][stale.digest] = stale.canonical()
                state["history"].append({"question_id": question_id,
                                          "calibration_id": stale.digest,
                                          "status": "replaced"})
            active = candidate.with_status("active")
            active_id = active.digest
            state["records"][active_id] = active.canonical()
            state["active"][question_id] = active_id
            state["history"].append({"question_id": question_id,
                                      "calibration_id": active_id,
                                      "status": "activated",
                                      "authority_ref": authority_ref})
            state["revision"] += 1
            self._write(state)
            return {"calibration_id": active_id, "revision": state["revision"]}

    def monitor_drift(self, events: Iterable[Any], *, minimum_observations: int = 30,
                      maximum_accuracy_drop: float = 0.15,
                      maximum_ece_increase: float = 0.10,
                      expected_revision: int) -> dict[str, Any]:
        with self._lock:
            state = self._read()
            if state["revision"] != expected_revision:
                raise CalibrationLifecycleError("calibration lifecycle revision changed")
            rows = tuple(events)
            changed: dict[str, str] = {}
            for question_id, active_id in tuple(state["active"].items()):
                record = CalibrationRecord.from_mapping(state["records"][active_id])
                paired = _paired_rows(rows, question_id)
                if len(paired) < minimum_observations:
                    continue
                correct = [row["selected"] == row["actual"] for row in paired]
                accuracy = sum(map(int, correct)) / len(correct)
                probabilities = [(row["probabilities"], row["actual"]) for row in paired]
                brier = _brier(probabilities)
                ece = _ece(probabilities, record.temperature)
                status = "clear"
                if (accuracy < record.validation_accuracy - maximum_accuracy_drop
                        or ece > record.validation_ece + maximum_ece_increase
                        or brier > max(0.4, record.validation_brier + 0.15)):
                    status = "drifted"
                elif (accuracy < record.validation_accuracy - maximum_accuracy_drop / 2
                      or ece > record.validation_ece + maximum_ece_increase / 2):
                    status = "warning"
                if status != record.drift_status:
                    updated = replace(record, drift_status=status)
                    state["records"][updated.digest] = updated.canonical()
                    state["active"][question_id] = updated.digest
                    state["history"].append({"question_id": question_id,
                                              "calibration_id": updated.digest,
                                              "status": f"drift_{status}"})
                    changed[question_id] = status
            if changed:
                state["revision"] += 1
                self._write(state)
            return {"revision": state["revision"], "changed": changed}

    def rollback(self, *, question_id: str, calibration_id: str,
                 authority_ref: str, expected_revision: int) -> dict[str, Any]:
        if not authority_ref or len(authority_ref) > 256:
            raise CalibrationLifecycleError("rollback requires a host review reference")
        with self._lock:
            state = self._read()
            if state["revision"] != expected_revision:
                raise CalibrationLifecycleError("calibration lifecycle revision changed")
            raw = state["records"].get(calibration_id)
            if raw is None:
                raise CalibrationLifecycleError("rollback target is unknown")
            target = CalibrationRecord.from_mapping(raw)
            if target.question_id != question_id:
                raise CalibrationLifecycleError("rollback target belongs to another question family")
            active = replace(target, status="active", drift_status="clear")
            state["records"][active.digest] = active.canonical()
            state["active"][question_id] = active.digest
            state["history"].append({"question_id": question_id,
                                      "calibration_id": active.digest,
                                      "status": "rolled_back",
                                      "authority_ref": authority_ref})
            state["revision"] += 1
            self._write(state)
            return {"calibration_id": active.digest, "revision": state["revision"]}

    def active_bundle(self) -> CalibrationBundle:
        with self._lock:
            state = self._read()
            return CalibrationBundle({
                question_id: CalibrationRecord.from_mapping(state["records"][record_id])
                for question_id, record_id in state["active"].items()})


__all__ = ["CalibrationLifecycleError", "SystemOneCalibrationLifecycle"]
