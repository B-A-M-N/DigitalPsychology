from __future__ import annotations

import pytest

from lib.events import SCHEMA_VERSION
from lib.telemetry.cfw_bridge import (CFWTelemetryBridge,
                                      translate_cfw_runtime_event)


def _runtime_event():
    return {
        "schema_version": "1.0.0",
        "event_id": "cfw-event-1",
        "timestamp": "2026-10-04T12:00:00Z",
        "namespace_id": "tenant-a",
        "application_id": "test-app",
        "application_instance_id": "test-app-1",
        "task_id": "task-1",
        "agent_id": "agent",
        "actor_id": "runtime",
        "agent_instance_id": "agent-1",
        "session_id": "session-1",
        "attempt_id": "attempt-1",
        "segment_id": "segment-1",
        "behavioral_subject": "repo:fixture",
        "category": "decision",
        "event_type": "policy_composed",
        "payload": {"summary": "bounded metadata"},
    }


def test_cfw_runtime_event_translates_at_the_dp_boundary():
    event = _runtime_event()
    event["payload"] = {
        "summary": "bounded metadata",
        "neutral_flag": True,
        "nested": {"samples": [1, "kept"]},
        "outcome": "neutral-runtime-value",
    }

    translated = translate_cfw_runtime_event(event)

    assert translated["schema_version"] == SCHEMA_VERSION
    assert translated["event_id"] == "cfw-event-1"
    assert translated["payload"]["summary"] == "bounded metadata"
    assert translated["payload"]["runtime_metadata"] == event["payload"]
    assert "outcome" not in translated["payload"]


def test_cfw_bridge_adapts_neutral_sink_events_before_dp_ingestion():
    received = []
    bridge = CFWTelemetryBridge(received.append)

    bridge(_runtime_event())

    assert len(received) == 1
    assert received[0]["schema_version"] == SCHEMA_VERSION


def test_cfw_bridge_rejects_unknown_neutral_contract_version():
    event = _runtime_event()
    event["schema_version"] = "2.7.0"

    with pytest.raises(ValueError, match="unsupported CFW runtime-event"):
        translate_cfw_runtime_event(event)


def test_cfw_bridge_rejects_neutral_payload_beyond_shared_bounds():
    event = _runtime_event()
    nested = {}
    for _ in range(10):
        nested = {"nested": nested}
    event["payload"] = nested

    with pytest.raises(ValueError, match="nesting bound"):
        translate_cfw_runtime_event(event)
