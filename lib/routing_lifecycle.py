"""Durable post-deployment lifecycle for behavioral routing profiles.

CFW remains a read-only policy consumer. This control-plane module records
independent trajectory observations and marks a profile stale when its
declared sample budget is exhausted; it never changes a task policy in place.
"""
from __future__ import annotations

import json
import fcntl
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from .routing_profiles import RoutingProfileError, profile_semantic_hash, validate_profile


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


class RoutingLifecycleStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock_path = path.with_name(path.name + ".lock")
        self.deployment_path = path.with_name("active-routing-profile.json")

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schema_version": "1.0.0", "revision": 0, "profiles": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RoutingProfileError(f"routing lifecycle state is unreadable: {self.path}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("profiles", {}), dict):
            raise RoutingProfileError("invalid routing lifecycle state")
        return data

    def _save(self, state: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=str(self.path.parent), prefix=".routing-lifecycle-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(dict(state), stream, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
            directory_fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def observe(self, profile: Mapping[str, Any], observations: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
        """Record unique host-bound trajectories and return the current profile.

        Each observation must identify the exact profile hash and a unique
        trajectory. Duplicate telemetry is ignored. Expiry is evaluated only
        against eligible observations, so repeated events cannot consume a
        profile's lifecycle budget.
        """
        validate_profile(profile)
        profile_hash = str(profile.get("profile_hash") or "")
        observations = list(observations)
        self.path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.lock_path.touch(mode=0o600, exist_ok=True)
        with self.lock_path.open("r+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            state = self._load()
            current_revision = int(state.get("revision", 0) or 0)
            if profile.get("status") == "stale":
                # A stale artifact must never seed a fresh lifecycle counter
                # under its new semantic hash.
                return dict(profile)
            record = dict(state["profiles"].get(profile_hash) or {
                "trajectory_ids": [], "observations": [], "status": profile.get("status")})
            known = set(record.get("trajectory_ids") or [])
            for observation in observations:
                if observation.get("profile_hash") != profile_hash:
                    raise RoutingProfileError("routing lifecycle observation targets another profile")
                trajectory_id = str(observation.get("trajectory_id") or "")
                if not trajectory_id or not observation.get("eligible", True) or trajectory_id in known:
                    continue
                known.add(trajectory_id)
                record.setdefault("trajectory_ids", []).append(trajectory_id)
                record.setdefault("observations", []).append(dict(observation))
            record["trajectory_ids"] = sorted(known)
            record["sample_count"] = len(known)
            threshold = min(
                int(adjustment["expires_after_samples"])
                for adjustment in profile.get("routing_adjustments", [])
                if adjustment.get("expires_after_samples") is not None
            ) if any(adjustment.get("expires_after_samples") is not None
                     for adjustment in profile.get("routing_adjustments", [])) else None
            updated = json.loads(json.dumps(dict(profile)))
            if threshold is not None and len(known) >= threshold:
                updated["status"] = "stale"
                updated["expires_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                updated["lifecycle_status"] = "stale"
                record["status"] = "stale"
            else:
                updated["lifecycle_status"] = updated.get("status", "candidate")
            state["profiles"][profile_hash] = record
            state["revision"] = current_revision + 1
            state["updated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            updated["lifecycle_revision"] = state["revision"]
            updated["profile_hash"] = profile_semantic_hash(updated)
            self._save(state)
            self._save_deployment(updated, state["revision"])
            return updated

    def deploy(self, profile: Mapping[str, Any], *, reason: str = "validated experiment receipt") -> dict[str, Any]:
        """Publish an active profile without counting deployment as a trajectory."""
        validate_profile(profile, require_deployable=True)
        self.path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.lock_path.touch(mode=0o600, exist_ok=True)
        with self.lock_path.open("r+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            state = self._load(); revision = int(state.get("revision", 0) or 0) + 1
            updated = json.loads(json.dumps(dict(profile)))
            updated["lifecycle_status"] = "active"; updated["lifecycle_revision"] = revision
            state["profiles"][updated["profile_hash"]] = {"status": "active", "deployment_revision": revision, "reason": reason}
            state["revision"] = revision; state.setdefault("deployments", []).append({"profile_hash": updated["profile_hash"], "revision": revision, "reason": reason})
            state["updated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            self._save(state); self._save_deployment(updated, revision)
            return updated

    def _transition(self, profile: Mapping[str, Any], *, status: str,
                    reason: str, evidence: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if status not in {"rolled_back", "stale"}:
            raise RoutingProfileError("unsupported lifecycle transition")
        if not reason or len(reason) > 512:
            raise RoutingProfileError("lifecycle transition requires a bounded reason")
        validate_profile(profile)
        profile_hash = str(profile.get("profile_hash") or "")
        self.path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.lock_path.touch(mode=0o600, exist_ok=True)
        with self.lock_path.open("r+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            state = self._load()
            current_revision = int(state.get("revision", 0) or 0)
            record = dict(state["profiles"].get(profile_hash) or {
                "trajectory_ids": [], "observations": [], "status": profile.get("status")})
            event = {"from_status": profile.get("status"), "to_status": status,
                     "reason": reason, "evidence": dict(evidence or {}),
                     "recorded_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}
            record["status"] = status
            record.setdefault("events", []).append(event)
            updated = json.loads(json.dumps(dict(profile)))
            updated["status"] = status
            updated["lifecycle_status"] = status
            updated["lifecycle_reason"] = reason
            updated["lifecycle_revision"] = current_revision + 1
            updated["profile_hash"] = profile_semantic_hash(updated)
            state["profiles"][profile_hash] = record
            state["revision"] = current_revision + 1
            state.setdefault("events", []).append({"profile_hash": profile_hash, **event})
            state["updated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            self._save(state)
            self._save_deployment(updated, state["revision"])
            return updated

    def rollback(self, profile: Mapping[str, Any], *, reason: str,
                 receipt_ref: str | None = None) -> dict[str, Any]:
        return self._transition(profile, status="rolled_back", reason=reason,
                                evidence={"receipt_ref": receipt_ref} if receipt_ref else {})

    def reject(self, profile: Mapping[str, Any], *, reason: str,
               evidence_refs: Iterable[str] = ()) -> dict[str, Any]:
        return self._transition(profile, status="stale", reason=reason,
                                evidence={"evidence_refs": list(evidence_refs)})

    def _save_deployment(self, profile: Mapping[str, Any], revision: int) -> None:
        """Publish the current revision and retain a known-good predecessor."""
        if self.deployment_path == self.path:
            return
        previous = None
        previous_revision = None
        if self.deployment_path.exists():
            try:
                existing = json.loads(self.deployment_path.read_text(encoding="utf-8"))
                if isinstance(existing, Mapping) and isinstance(existing.get("profile"), Mapping):
                    candidate = dict(existing["profile"])
                    if candidate.get("profile_hash") != profile.get("profile_hash"):
                        previous = candidate; previous_revision = existing.get("revision")
            except (OSError, json.JSONDecodeError, TypeError):
                pass
        payload = {"schema_version": "1.0.0", "revision": revision,
                   "status": profile.get("status"), "profile": dict(profile)}
        if previous is not None:
            payload["previous_profile"] = previous
            payload["previous_revision"] = previous_revision
        fd, temporary = tempfile.mkstemp(
            dir=str(self.deployment_path.parent), prefix=".active-routing-profile.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, indent=2)
                stream.write("\n"); stream.flush(); os.fsync(stream.fileno())
            os.chmod(temporary, 0o600); os.replace(temporary, self.deployment_path)
            directory_fd = os.open(self.deployment_path.parent, os.O_RDONLY)
            try: os.fsync(directory_fd)
            finally: os.close(directory_fd)
        finally:
            if os.path.exists(temporary): os.unlink(temporary)
