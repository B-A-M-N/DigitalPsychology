"""Bounded, authenticated ingestion of behavioral telemetry."""
from __future__ import annotations

import time
from typing import Any, Mapping

from ..feedback_loop import EventError, EventFactory, SQLiteSink
from ..compatibility import CFW_RUNTIME_EVENT_SCHEMA_VERSION
from ..runtime_event_adapter import from_cfw_runtime_event
from ..security.attestation import EventAttestationAuthority


class BehaviorEventIngestor:
    """Validate event envelopes, enforce backpressure, and persist events."""

    def __init__(self, *, sink: SQLiteSink,
                 attestation: EventAttestationAuthority,
                 max_pending_events_per_origin: int = 256,
                 max_ingest_seconds_per_origin: float = 0.25,
                 backpressure_recovery_seconds: float = 5.0) -> None:
        if (max_pending_events_per_origin < 1 or max_ingest_seconds_per_origin <= 0
                or backpressure_recovery_seconds <= 0):
            raise ValueError("per-origin ingestion bounds must be positive")
        self.sink = sink
        self.attestation = attestation
        self.max_pending_events_per_origin = int(max_pending_events_per_origin)
        self.max_ingest_seconds_per_origin = float(max_ingest_seconds_per_origin)
        self.backpressure_recovery_seconds = float(backpressure_recovery_seconds)
        self._pending_origins: dict[tuple[str, str], list[float]] = {}

    def ingest(self, event: Mapping[str, Any]) -> dict[str, Any]:
        try:
            if not isinstance(event, Mapping):
                raise ValueError("behavior event transport must be an object")
            if "event" in event or "attestation" in event:
                if set(event) != {"event", "attestation"}:
                    raise ValueError("authenticated behavior event envelope has unknown fields")
                raw_event = event.get("event")
                attestation = event.get("attestation")
                if not isinstance(raw_event, Mapping):
                    raise ValueError("authenticated behavior event requires an event object")
                data = dict(raw_event)
            else:
                # Compatibility adapter for the 2.3 transport that embedded
                # attestation beside semantic event fields.
                data = dict(event)
                attestation = data.pop("host_attestation", None)
            authenticated = self.attestation.attest(
                data, attestation if isinstance(attestation, Mapping) else None)
            if data.get("schema_version") == CFW_RUNTIME_EVENT_SCHEMA_VERSION:
                parsed = from_cfw_runtime_event(data)
            else:
                parsed = EventFactory.from_dict(data)
            origin = (parsed.namespace_id, parsed.application_instance_id)
            now = time.monotonic()
            recent = [stamp for stamp in self._pending_origins.get(origin, ())
                      if now - stamp < self.backpressure_recovery_seconds]
            if recent:
                self._pending_origins[origin] = recent
            else:
                self._pending_origins.pop(origin, None)
            if len(recent) >= self.max_pending_events_per_origin:
                raise ValueError("per-origin behavioral ingestion backpressure is active")
            started = time.monotonic()
            self.sink.emit_authenticated(parsed, authenticated=authenticated)
            elapsed = time.monotonic() - started
            if elapsed > self.max_ingest_seconds_per_origin:
                self._pending_origins.setdefault(origin, []).append(time.monotonic())
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


__all__ = ["BehaviorEventIngestor"]
