"""DigitalPsychology MCP slow-loop service.

The service is the authoritative ingestion/control-plane boundary.  CFW and
CSW remain usable without it; an unavailable service therefore produces no
adaptive advice rather than changing static runtime policy.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import stat
from pathlib import Path
from typing import Any, Mapping

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:  # transport is optional; the service remains testable
    FastMCP = None

from .feedback_loop import EventFactory, EventError, SQLiteSink, trusted_state_root
from .routing_profiles import (BehavioralRoutingProfile, RoutingProfileError,
                               applicable_adjustments,
                               validate_profile)
from .slow_loop import BoundedLearningController


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


class DigitalPsychologyService:
    """Small service object kept separate from MCP transport for testing."""

    def __init__(self, *, event_db: Path | None = None,
                 profile_path: Path | None = None,
                 trusted_producers: Mapping[str, Mapping[str, Any]] | None = None) -> None:
        root = trusted_state_root()
        self.event_db = event_db or Path(
            os.environ.get("DIGITALPSYCHOLOGY_EVENT_DB", root / "events.sqlite3"))
        self.profile_path = profile_path
        self._trusted_producers = {str(k): dict(v) for k, v in (trusted_producers or {}).items()}
        self._attestation_secret = os.environ.get("DIGITALPSYCHOLOGY_INGESTION_SECRET", "").encode()
        self._promotion_secret = os.environ.get("DIGITALPSYCHOLOGY_PROMOTION_SECRET", "").encode()
        self._sink = SQLiteSink(self.event_db)
        self._sink._conn.execute("""CREATE TABLE IF NOT EXISTS authenticated_events (
            namespace_id TEXT NOT NULL, application_instance_id TEXT NOT NULL,
            event_id TEXT NOT NULL, PRIMARY KEY(namespace_id, application_instance_id, event_id))""")
        self._sink._conn.commit()

    def close(self) -> None:
        self._sink.close()

    def _attest_event(self, data: Mapping[str, Any]) -> None:
        privileged = data.get("category") in {"validation", "state_transition", "completion"} or data.get("actor_id") in {"operator", "repository_owner", "service_owner"}
        if privileged and not self._trusted_producers and not self._attestation_secret:
            raise ValueError("privileged behavior events require host attestation")
        if not self._trusted_producers and not self._attestation_secret:
            return False
        if not self._attestation_secret:
            raise ValueError("trusted producer configuration requires a cryptographic ingestion secret")
        if self._attestation_secret:
            signature = str(data.get("host_attestation", {}).get("signature", ""))
            body = {k: v for k, v in data.items() if k != "host_attestation"}
            expected = hmac.new(self._attestation_secret, _canonical(body).encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError("behavior event host attestation is invalid")
            return True
        key = (str(data.get("namespace_id")), str(data.get("application_instance_id")), str(data.get("agent_instance_id")))
        producer = self._trusted_producers.get("|".join(key))
        if not producer or data.get("actor_id") not in set(producer.get("allowed_actors", ())):
            raise ValueError("behavior event producer is not host-authorized")
        if data.get("host_attestation", {}).get("issuer") != producer.get("issuer"):
            raise ValueError("behavior event attestation issuer is invalid")
        return True

    def ingest_behavior_event(self, event: Mapping[str, Any]) -> dict[str, Any]:
        try:
            data = dict(event)
            authenticated = self._attest_event(data)
            data.pop("host_attestation", None)
            parsed = EventFactory.from_dict(data)
            self._sink.emit(parsed)
            if authenticated:
                self._sink._conn.execute(
                    "INSERT OR IGNORE INTO authenticated_events(namespace_id, application_instance_id, event_id) VALUES (?,?,?)",
                    (parsed.namespace_id, parsed.application_instance_id, parsed.event_id))
                self._sink._conn.commit()
        except (EventError, ValueError) as exc:
            raise ValueError(f"behavior event rejected: {exc}") from exc
        data = parsed.to_dict()
        return {
            "accepted": True,
            "idempotent_key": {
                "namespace_id": data["namespace_id"],
                "application_instance_id": data["application_instance_id"],
                "event_id": data["event_id"],
            },
            "schema_version": data["schema_version"],
        }

    def _eligible_route_keys(self, routes: list[Any]) -> set[tuple[str, str]]:
        result: set[tuple[str, str]] = set()
        for route in routes:
            if isinstance(route, str):
                result.add(("*", route))
            elif isinstance(route, Mapping):
                route_type = str(route.get("route_type") or "*")
                name = str(route.get("route") or "")
                if name:
                    result.add((route_type, name))
        return result

    def _load_profiles(self) -> tuple[list[dict[str, Any]], str]:
        path = self.profile_path
        if path is None:
            path = trusted_state_root() / "active-routing-profile.json"
        if not path.exists():
            return [], ""
        if path.is_symlink():
            raise RoutingProfileError("routing profile path must not be a symlink")
        info = path.stat()
        if info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise RoutingProfileError(
                "routing profile artifact must be owned by the service user and not writable by others")
        parent = path.parent
        while True:
            parent_info = parent.lstat()
            shared_sticky_dir = (stat.S_ISDIR(parent_info.st_mode)
                                 and bool(parent_info.st_mode & stat.S_ISVTX))
            if stat.S_ISLNK(parent_info.st_mode) or (
                    parent_info.st_mode & 0o022 and not shared_sticky_dir):
                raise RoutingProfileError(
                    "routing profile parent directory is not a protected trust boundary")
            if parent == parent.parent:
                break
            parent = parent.parent
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, Mapping) and isinstance(data.get("profile"), Mapping):
            profiles = [dict(data["profile"])]
            revision = data.get("revision")
            if profiles[0].get("lifecycle_revision") != revision:
                raise RoutingProfileError("active routing profile revision mismatch")
            current = profiles[0]
            lifecycle_status = current.get("lifecycle_status") or current.get("status")
            if current.get("status") in {"stale", "rolled_back"} or lifecycle_status in {"stale", "rolled_back"}:
                return [], ""
            try:
                validate_profile(current, require_deployable=True)
            except RoutingProfileError:
                previous = data.get("previous_profile")
                if (not isinstance(previous, Mapping)
                        or previous.get("profile_hash") == current.get("profile_hash")
                        or previous.get("status") in {"stale", "rolled_back"}
                        or previous.get("lifecycle_status") in {"stale", "rolled_back"}):
                    return [], ""
                validate_profile(dict(previous), require_deployable=True)
                profiles = [dict(previous)]
        elif isinstance(data, Mapping) and isinstance(data.get("profiles"), list):
            profiles = [dict(item) for item in data["profiles"]
                        if item.get("status") not in {"stale", "rolled_back"}
                        and item.get("lifecycle_status") not in {"stale", "rolled_back"}]
        elif isinstance(data, Mapping):
            profiles = [dict(data)]
        else:
            raise RoutingProfileError("routing profile artifact must be an object")
        for profile in profiles:
            validate_profile(profile, require_deployable=True)
        pack_hash = hashlib.sha256(_canonical(profiles).encode("utf-8")).hexdigest()
        return profiles, pack_hash

    def behavioral_advice(self, *, context: Mapping[str, Any],
                          eligible_routes: list[Any],
                          static_policy: Mapping[str, Any]) -> dict[str, Any]:
        """Return bounded advice only for host-declared eligible routes."""
        try:
            profiles, profile_hash = self._load_profiles()
        except (RoutingProfileError, OSError, TypeError, ValueError) as exc:
            return {"status": "unavailable", "reason": str(exc),
                    "adjustments": [], "dp_revision": None}
        allowed = self._eligible_route_keys(eligible_routes)
        adjustments: list[dict[str, Any]] = []
        sources: list[str] = []
        for raw in profiles:
            try:
                profile = BehavioralRoutingProfile.from_dict(raw, require_deployable=True)
                matches = applicable_adjustments(profile, context)
            except RoutingProfileError:
                continue
            for adjustment in matches:
                key = (str(adjustment.get("route_type") or "*"),
                       str(adjustment.get("route") or ""))
                if adjustment.get("disposition") not in {"prefer", "suppress"}:
                    continue
                if key not in allowed and ("*", key[1]) not in allowed:
                    continue
                adjustments.append({**adjustment, "profile_id": raw.get("profile_id"),
                                    "profile_hash": raw.get("profile_hash")})
                sources.append(str(raw.get("profile_id") or raw.get("profile_hash")))
        body = {
            "status": "ok",
            "static_policy_hash": static_policy.get("policy_hash", static_policy.get("static_policy_hash")),
            "eligible_choice_hash": static_policy.get("eligible_choice_hash"),
            "context_identity": static_policy.get("context_identity"),
            "profile_pack_hash": profile_hash,
            "adjustments": adjustments,
            "sources": sorted(set(sources)),
            "expires_at": min((str(profile.get("expires_at")) for profile in profiles
                                if profile.get("expires_at")), default=None),
            "dp_revision": self._profile_revision(),
        }
        body["semantic_hash"] = hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()
        body["advice_id"] = f"advice-{body['semantic_hash'][:20]}"
        return body

    def _run_slow_loop(self, events: list[Any], task_context: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
        """Internal authenticated-cycle primitive; never exposed as an MCP tool."""
        from .routing_lifecycle import RoutingLifecycleStore
        profile_path = self.profile_path or (trusted_state_root() / "active-routing-profile.json")
        lifecycle = RoutingLifecycleStore(profile_path.parent / "routing-lifecycle.json")
        parsed_events = [EventFactory.from_dict(dict(event)) if isinstance(event, Mapping) else event
                         for event in events]
        report = BoundedLearningController(lifecycle=lifecycle).run(parsed_events, task_context)
        return {"status": report.status, "promoted": report.promoted,
                "candidate_count": len(report.candidates), "rejected": report.rejected}

    def _load_persisted_events(self, event_ids: list[Any], *,
                              namespace_id: str | None = None,
                              application_instance_id: str | None = None) -> list[Any]:
        """Load only authenticated, already-persisted event records."""
        loaded = []
        for event_id in event_ids:
            if namespace_id and application_instance_id:
                row = self._sink._conn.execute(
                    "SELECT e.payload FROM events e JOIN authenticated_events a "
                    "ON a.namespace_id=e.namespace_id AND a.application_instance_id=e.application_instance_id AND a.event_id=e.event_id "
                    "WHERE e.namespace_id = ? AND e.application_instance_id = ? AND e.event_id = ?",
                    (str(namespace_id), str(application_instance_id), str(event_id)),
                ).fetchone()
            else:
                row = self._sink._conn.execute(
                    "SELECT e.payload FROM events e JOIN authenticated_events a ON a.event_id=e.event_id "
                    "WHERE e.event_id = ? LIMIT 1", (str(event_id),)
                ).fetchone()
            if row is None:
                raise ValueError(f"experiment references an event not present in authenticated store: {event_id!r}")
            loaded.append(EventFactory.from_dict(json.loads(row[0])))
        return loaded

    def run_registered_slow_loop(self, experiment_id: str,
                                 authorization: Mapping[str, Any]) -> dict[str, Any]:
        """Run a protected experiment record; promotion requires host authorization."""
        root = trusted_state_root()
        registry_path = root / "experiments.json"
        if not registry_path.exists():
            raise ValueError("experiment registry is unavailable")
        try:
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("experiment registry is unreadable") from exc
        record = registry.get(str(experiment_id)) if isinstance(registry, Mapping) else None
        if not isinstance(record, Mapping) or record.get("experiment_id") != experiment_id:
            raise ValueError("unknown or mismatched experiment record")
        expected = str(record.get("promotion_authorization_id") or "")
        if not expected or authorization.get("authorization_id") != expected:
            raise ValueError("experiment promotion authorization is required")
        if not self._promotion_secret:
            raise ValueError("experiment promotion requires a separate promotion secret")
        signature = str(authorization.get("signature") or "")
        body = {key: value for key, value in authorization.items() if key != "signature"}
        expected_signature = hmac.new(self._promotion_secret, _canonical(body).encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected_signature):
            raise ValueError("experiment promotion authorization is invalid")
        event_ids = record.get("event_ids")
        task_context = record.get("task_context")
        if not isinstance(event_ids, list) or not isinstance(task_context, Mapping):
            raise ValueError("experiment record lacks authenticated event IDs or context")
        events = self._load_persisted_events(
            event_ids, namespace_id=record.get("namespace_id"),
            application_instance_id=record.get("application_instance_id"))
        return self._run_slow_loop(events, task_context)

    def purge_expired_events(self, *, retention_days: int) -> dict[str, Any]:
        """Host-only retention operation; deliberately not an MCP tool."""
        return self._sink.purge_expired(retention_days=retention_days)

    def validate_routing_profile(self, profile: Mapping[str, Any]) -> dict[str, Any]:
        validate_profile(profile, require_deployable=True)
        return {"valid": True, "profile_id": profile["profile_id"],
                "profile_hash": profile["profile_hash"], "scope_mode": profile["scope_mode"]}

    def _profile_revision(self) -> int | None:
        path = self.profile_path
        if path is None:
            root = trusted_state_root()
            path = root / "active-routing-profile.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return int(data.get("revision")) if data.get("revision") is not None else None
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None


def create_server(service: DigitalPsychologyService | None = None):
    if FastMCP is None:
        raise RuntimeError("MCP transport dependency is not installed")
    service = service or DigitalPsychologyService()
    server = FastMCP(
        "digital-psychology",
        instructions=("Behavioral telemetry ingestion and bounded adaptive advice. "
                      "CFW remains the runtime authority."),
    )

    @server.tool()
    def ingest_behavior_event(event: dict[str, Any]) -> dict[str, Any]:
        """Ingest one schema-validated, origin-scoped behavioral event."""
        return service.ingest_behavior_event(event)

    @server.tool()
    def behavioral_advice(context: dict[str, Any], eligible_routes: list[Any],
                          static_policy: dict[str, Any]) -> dict[str, Any]:
        """Return bounded advice for routes already legal under CFW policy."""
        return service.behavioral_advice(context=context, eligible_routes=eligible_routes,
                                         static_policy=static_policy)

    @server.tool()
    def validate_routing_profile(profile: dict[str, Any]) -> dict[str, Any]:
        """Validate a deployable profile and its exact scope contract."""
        return service.validate_routing_profile(profile)

    @server.tool()
    def run_registered_slow_loop(experiment_id: str, authorization: dict[str, Any]) -> dict[str, Any]:
        """Run a protected experiment record after host authorization."""
        return service.run_registered_slow_loop(experiment_id, authorization)

    return server


def main() -> None:
    create_server().run("stdio")


if __name__ == "__main__":
    main()
