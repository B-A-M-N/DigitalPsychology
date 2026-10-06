"""Translate the neutral CFW telemetry envelope into DP's event contract."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .compatibility import CFW_RUNTIME_EVENT_SCHEMA_VERSION
from .versions import BEHAVIOR_EVENT_SCHEMA_VERSION


def from_cfw_runtime_event(value: Mapping[str, Any]):
    """Validate a CFW runtime event and return its DP event representation.

    CFW owns the source schema and validates events before persistence. This
    boundary pins the accepted source version, translates only the schema
    version, and then validates the complete envelope against DP's schema.
    """
    from .feedback_loop import EventError, EventFactory, EventSchema

    if not isinstance(value, Mapping):
        raise EventError("CFW runtime event must be a mapping")
    if value.get("schema_version") != CFW_RUNTIME_EVENT_SCHEMA_VERSION:
        raise EventError("CFW runtime event schema version is incompatible")
    translated = dict(value)
    translated["schema_version"] = BEHAVIOR_EVENT_SCHEMA_VERSION
    source_payload = translated.pop("payload", None)
    if source_payload is not None:
        if not isinstance(source_payload, Mapping):
            raise EventError("CFW runtime event payload must be a mapping")
        dp_payload_fields = set(
            EventSchema.load()["properties"]["payload"]["properties"])
        translated_payload = {
            key: item for key, item in source_payload.items()
            if key in dp_payload_fields and key != "runtime_metadata"
        }
        opaque_metadata = {
            key: item for key, item in source_payload.items()
            if key not in dp_payload_fields or key == "runtime_metadata"
        }
        if opaque_metadata:
            translated_payload["runtime_metadata"] = {
                "cfw_payload": opaque_metadata,
            }
        translated["payload"] = translated_payload
    return EventFactory.from_dict(translated)


def load_cfw_runtime_events(path: Path):
    """Load a CFW NDJSON event stream through the explicit schema adapter."""
    from .feedback_loop import EventError

    events = []
    seen: set[tuple[Any, ...]] = set()
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            raise EventError(f"{path.name}:{lineno}: invalid JSON: {exc}") from exc
        try:
            event = from_cfw_runtime_event(data)
        except EventError as exc:
            raise EventError(f"{path.name}:{lineno}: {exc}") from exc
        identity = (event.namespace_id, event.application_instance_id,
                    event.agent_instance_id, event.session_id, event.task_id,
                    event.attempt_id, event.behavioral_subject, event.event_id)
        if identity in seen:
            raise EventError(
                f"{path.name}:{lineno}: duplicate composite event identity {identity!r}")
        seen.add(identity)
        events.append(event)
    return events
