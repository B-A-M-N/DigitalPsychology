"""Durable post-deployment lifecycle for behavioral routing profiles.

CFW remains a read-only policy consumer. This control-plane module records
independent trajectory observations and marks a profile stale when its
declared sample budget is exhausted; it never changes a task policy in place.
"""
from __future__ import annotations

import json
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
        state = self._load()
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
            updated["profile_hash"] = profile_semantic_hash(updated)
            record["status"] = "stale"
        state["profiles"][profile_hash] = record
        state["revision"] = int(state.get("revision", 0) or 0) + 1
        state["updated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        self._save(state)
        return updated
