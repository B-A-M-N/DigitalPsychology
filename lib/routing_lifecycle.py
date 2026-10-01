"""Durable post-deployment lifecycle for behavioral routing profiles.

CFW remains a read-only policy consumer. This control-plane module records
independent trajectory observations and marks a profile stale when its
declared sample budget is exhausted; it never changes a task policy in place.
"""
from __future__ import annotations

import json
import fcntl
import hashlib
import os
import tempfile
import uuid
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

    def registry_path(self) -> Path:
        return self.path.with_name("routing-profile-registry.json")

    def _registry_lock(self):
        path = self.registry_path(); path.parent.mkdir(parents=True, exist_ok=True)
        lock = path.with_name(path.name + ".lock"); lock.touch(mode=0o600, exist_ok=True)
        return lock

    def _load_registry(self) -> dict[str, Any]:
        path = self.registry_path()
        if not path.exists(): return {"schema_version":"1.0.0","revision":0,"profiles":{}}
        data=json.loads(path.read_text())
        if not isinstance(data,dict) or not isinstance(data.get("profiles"),dict): raise RoutingProfileError("invalid routing profile registry")
        return data

    def _save_registry(self,state:Mapping[str,Any]) -> None:
        path=self.registry_path(); fd,tmp=tempfile.mkstemp(dir=path.parent,prefix=".routing-registry-",suffix=".tmp")
        try:
            with os.fdopen(fd,"w",encoding="utf-8") as f: json.dump(dict(state),f,indent=2);f.write("\n");f.flush();os.fsync(f.fileno())
            os.chmod(tmp,0o600);os.replace(tmp,path)
        finally:
            if os.path.exists(tmp): os.unlink(tmp)

    def list_registry(self) -> list[dict[str,Any]]:
        with self._registry_lock().open("r+",encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(),fcntl.LOCK_SH)
            try:return [dict(v) for v in self._load_registry()["profiles"].values()]
            finally:fcntl.flock(lock.fileno(),fcntl.LOCK_UN)

    @staticmethod
    def _lineage_of(entry:Mapping[str,Any]) -> str:
        return str(entry.get("deployment_lineage_id") or entry.get("profile_hash") or "")

    def adopt_profile(self, profile:Mapping[str,Any], *, adopter_agent_id:str, reason:str,
                      source_profile_hash:str|None=None) -> str:
        """Register/sync a deployment under its immutable source lineage.

        The mutable artifact hash changes when lifecycle status changes, so the
        registry entry is keyed by the *source* profile hash and the deployment
        lineage id. Registry lifecycle status is authoritative; the stored
        artifact is only a projection of it.
        """
        validate_profile(profile,require_deployable=True)
        if not adopter_agent_id: raise RoutingProfileError("adopter_agent_id is required")
        ph=str(source_profile_hash or profile["profile_hash"]); lock=self._registry_lock()
        with lock.open("r+",encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(),fcntl.LOCK_EX)
            state=self._load_registry()
            entry=dict(state["profiles"].get(ph) or {"profile_hash":ph,"profile":dict(profile),
                "status":profile.get("status"),"scope":profile.get("sharing_scope","agent_private"),
                "revisions":[]})
            # Always synchronize, never only-on-first-adopt.
            entry["profile"]=dict(profile)
            entry["artifact_hash"]=str(profile["profile_hash"])
            entry["status"]=str(profile.get("lifecycle_status") or profile.get("status") or "active")
            entry["scope"]=str(profile.get("sharing_scope","agent_private"))
            entry.setdefault("deployment_lineage_id",str(profile.get("deployment_lineage_id") or ph))
            entry.setdefault("adopters",{})[str(adopter_agent_id)]={"authorized":True,"reason":reason,
                "recorded_at":datetime.now(timezone.utc).isoformat().replace("+00:00","Z")}
            state["profiles"][ph]=entry;state["revision"]=int(state.get("revision",0))+1
            state["updated_at"]=datetime.now(timezone.utc).isoformat().replace("+00:00","Z")
            self._save_registry(state);return ph

    def set_lifecycle_status(self, *, source_profile_hash:str, status:str,
                             artifact:Mapping[str,Any]|None=None, reason:str="") -> None:
        """Make registry lifecycle state authoritative for a lineage.

        A stale or rolled-back lineage must never reappear in advice, even
        though its immutable artifact still says ``active``.
        """
        ph=str(source_profile_hash); lock=self._registry_lock()
        with lock.open("r+",encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(),fcntl.LOCK_EX)
            state=self._load_registry(); entry=dict(state["profiles"].get(ph) or {"profile_hash":ph,"revisions":[]})
            entry["status"]=status
            if reason: entry["status_reason"]=reason
            if artifact is not None:
                entry["profile"]=dict(artifact)
                entry["artifact_hash"]=str(artifact.get("profile_hash") or "")
            entry["status_revision"]=int(state.get("revision",0))+1
            state["profiles"][ph]=entry
            state["revision"]=int(state.get("revision",0))+1
            state["updated_at"]=datetime.now(timezone.utc).isoformat().replace("+00:00","Z")
            self._save_registry(state)

    def is_active(self, *, source_profile_hash:str) -> bool:
        entry=self.list_registry_by_hash().get(str(source_profile_hash))
        if not entry: return False
        return str(entry.get("status")) in {"active", "validated"}

    def list_registry_by_hash(self) -> dict[str,dict[str,Any]]:
        return {str(entry.get("profile_hash")):entry for entry in self.list_registry()}

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
            prior_record = dict(state["profiles"].get(profile_hash) or {})
            lineage_id = str(profile.get("deployment_lineage_id") or prior_record.get("deployment_lineage_id") or uuid.uuid4())
            record = prior_record or {"trajectory_ids": [], "observations": [], "status": profile.get("status")}
            record["deployment_lineage_id"] = lineage_id
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
            updated["deployment_lineage_id"] = lineage_id
            updated["profile_hash"] = profile_semantic_hash(updated)
            self._save(state)
            if updated.get("status") == "stale":
                # The expired lineage must stop being advisory-eligible even
                # though the immutable artifact still records "active".
                self.set_lifecycle_status(source_profile_hash=profile_hash,
                                          status="stale", artifact=updated,
                                          reason="sample budget exhausted")
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
            prior_lineage = str(profile.get("deployment_lineage_id") or uuid.uuid4())
            updated["lifecycle_status"] = "active"; updated["lifecycle_revision"] = revision
            updated["deployment_lineage_id"] = prior_lineage
            source_hash = str(updated["profile_hash"])
            state["profiles"][source_hash] = {"status": "active", "deployment_revision": revision,
                "deployment_lineage_id": prior_lineage, "reason": reason}
            state["revision"] = revision; state.setdefault("deployments", []).append({"profile_hash": updated["profile_hash"], "revision": revision, "reason": reason})
            state["updated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            self._save(state)
            self.adopt_profile(updated, adopter_agent_id="publisher", reason=reason,
                               source_profile_hash=source_hash)
            self._save_deployment(updated, revision)
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
            lineage_id = str(profile.get("deployment_lineage_id") or record.get("deployment_lineage_id") or uuid.uuid4())
            updated["deployment_lineage_id"] = lineage_id
            updated["lifecycle_revision"] = current_revision + 1
            updated["profile_hash"] = profile_semantic_hash(updated)
            state["profiles"][profile_hash] = record
            state["revision"] = current_revision + 1
            state.setdefault("events", []).append({"profile_hash": profile_hash, **event})
            state["updated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            self._save(state)
            self.set_lifecycle_status(source_profile_hash=profile_hash,
                                      status=status, artifact=updated, reason=reason)
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
        if self.deployment_path == self.path:
            return
        # Authoritative lifecycle truth lives in the registry, not in the
        # immutable artifact (whose hash changes when status changes).
        active=[]
        for entry in self.list_registry():
            status=str(entry.get("status"))
            if status in {"stale","rolled_back","candidate"}:
                continue
            stored=dict(entry.get("profile") or {})
            if not stored:
                continue
            stored["lifecycle_status"]=status
            active.append(stored)
        if (str(profile.get("lifecycle_status") or profile.get("status")) not in
                {"stale","rolled_back","candidate"}):
            if not any(item.get("profile_hash")==profile.get("profile_hash") for item in active):
                active.append(dict(profile))
        payload={"schema_version":"2.0.0","revision":revision,"status":"pack","profiles":active,"profiles_hash":hashlib.sha256(_canonical(active).encode()).hexdigest()}
        fd,tmp=tempfile.mkstemp(dir=str(self.deployment_path.parent),prefix=".active-routing-profile-",suffix=".tmp")
        try:
            with os.fdopen(fd,"w",encoding="utf-8") as stream:
                json.dump(payload,stream,indent=2);stream.write("\n");stream.flush();os.fsync(stream.fileno())
            os.chmod(tmp,0o600);os.replace(tmp,self.deployment_path)
        finally:
            if os.path.exists(tmp): os.unlink(tmp)
