"""Translate neutral CFW runtime events into DP's behavior-event contract."""
from __future__ import annotations

import json
import math
from collections.abc import Mapping
from functools import lru_cache
from typing import Any, Callable

from jsonschema import Draft202012Validator

from ..events import SCHEMA_VERSION
from ..feedback_loop import EventError, EventFactory, EventSchema

CFW_RUNTIME_EVENT_SCHEMA_VERSION = "1.0.0"


@lru_cache(maxsize=128)
def _dp_payload_field_validator(name: str) -> Draft202012Validator | None:
    properties = EventSchema.load()["properties"]["payload"]["properties"]
    property_schema = properties.get(name)
    if property_schema is None:
        return None
    return Draft202012Validator({
        "type": "object",
        "properties": {name: property_schema},
        "required": [name],
        "additionalProperties": False,
    })


def _validate_runtime_metadata(value: Any, *, depth: int = 0) -> None:
    if depth > 8:
        raise ValueError("CFW runtime metadata exceeds its nesting bound")
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, str):
        if len(value) > 4096:
            raise ValueError("CFW runtime metadata string exceeds its bound")
        return
    if isinstance(value, (int, float)):
        if abs(value) > 1e12 or (isinstance(value, float) and not math.isfinite(value)):
            raise ValueError("CFW runtime metadata number exceeds its bound")
        return
    if isinstance(value, list):
        if len(value) > 256:
            raise ValueError("CFW runtime metadata list exceeds its bound")
        for item in value:
            _validate_runtime_metadata(item, depth=depth + 1)
        return
    if isinstance(value, Mapping):
        if len(value) > 64 or any(not isinstance(key, str) or len(key) > 256
                                  for key in value):
            raise ValueError("CFW runtime metadata object exceeds its bound")
        for item in value.values():
            _validate_runtime_metadata(item, depth=depth + 1)
        return
    raise ValueError("CFW runtime metadata contains an unsupported value")


def translate_cfw_runtime_event(event: Mapping[str, Any]) -> dict[str, Any]:
    """Translate a neutral CFW envelope into DP's bounded event contract.

    DP-known payload fields are projected for existing event consumers. The
    complete CFW payload is retained under ``runtime_metadata`` so valid
    neutral fields remain available without widening DP's behavioral fields.
    """
    if not isinstance(event, Mapping):
        raise ValueError("CFW runtime event must be an object")
    if event.get("schema_version") != CFW_RUNTIME_EVENT_SCHEMA_VERSION:
        raise ValueError("unsupported CFW runtime-event schema version")
    try:
        encoded = json.dumps(dict(event), separators=(",", ":"), allow_nan=False).encode()
    except (TypeError, ValueError) as exc:
        raise ValueError("CFW runtime event must contain bounded JSON values") from exc
    if len(encoded) > 64 * 1024:
        raise ValueError("CFW runtime event exceeds its 64 KiB bound")

    translated = dict(event)
    translated["schema_version"] = SCHEMA_VERSION
    if "payload" in event:
        source_payload = event["payload"]
        if not isinstance(source_payload, Mapping):
            raise ValueError("CFW runtime event payload must be an object")
        metadata = dict(source_payload)
        _validate_runtime_metadata(metadata)
        projected = {}
        for name, value in metadata.items():
            validator = _dp_payload_field_validator(name)
            if validator is not None and validator.is_valid({name: value}):
                projected[name] = value
        projected["runtime_metadata"] = metadata
        translated["payload"] = projected
    try:
        return EventFactory.from_dict(translated).to_dict()
    except (EventError, ValueError) as exc:
        raise ValueError(f"CFW runtime event cannot be represented as a DP event: {exc}") from exc


class CFWTelemetryBridge:
    """Callable sink adapter for a DP event consumer such as event ingestion."""

    def __init__(self, sink: Callable[[dict[str, Any]], Any]) -> None:
        if not callable(sink):
            raise TypeError("CFW telemetry bridge requires a callable DP sink")
        self.sink = sink

    def __call__(self, event: dict[str, Any]) -> Any:
        return self.sink(translate_cfw_runtime_event(event))


__all__ = ["CFW_RUNTIME_EVENT_SCHEMA_VERSION", "CFWTelemetryBridge",
           "translate_cfw_runtime_event"]
