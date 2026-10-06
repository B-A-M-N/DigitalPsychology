from __future__ import annotations

import pytest

from lib.compatibility import CFW_RUNTIME_EVENT_SCHEMA_VERSION
from lib.feedback_loop import EventError
from lib.runtime_event_adapter import from_cfw_runtime_event
from lib.telemetry.ingestion import BehaviorEventIngestor
from lib.versions import BEHAVIOR_EVENT_SCHEMA_VERSION


def _cfw_event(**overrides):
    return {
        "schema_version": CFW_RUNTIME_EVENT_SCHEMA_VERSION,
        "event_id": "evt-1",
        "timestamp": "2026-10-04T12:00:00Z",
        "namespace_id": "tenant",
        "application_id": "cfw-test",
        "application_instance_id": "cfw-test-instance",
        "agent_instance_id": "agent-instance",
        "session_id": "session-1",
        "task_id": "task-1",
        "attempt_id": "attempt-1",
        "segment_id": "segment-0",
        "agent_id": "agent",
        "behavioral_subject": "agent:agent",
        "actor_id": "runtime",
        "category": "action",
        "event_type": "action_authorized",
        "payload": {"result": "allowed"},
        **overrides,
    }


def test_cfw_runtime_event_translates_through_dp_schema():
    event = from_cfw_runtime_event(_cfw_event())

    assert event.event_id == "evt-1"
    assert event.task_id == "task-1"
    assert event.payload == {
        "runtime_metadata": {"cfw_payload": {"result": "allowed"}}
    }


def test_cfw_runtime_event_adapter_rejects_unnegotiated_schema_version():
    with pytest.raises(EventError, match="schema version is incompatible"):
        from_cfw_runtime_event(_cfw_event(schema_version="1.0.1"))


def test_cfw_runtime_event_adapter_revalidates_translated_envelope():
    with pytest.raises(EventError):
        from_cfw_runtime_event(_cfw_event(event_id=""))


def test_behavior_ingestor_accepts_cfw_event_after_attesting_source_envelope():
    class Attestor:
        source_event = None

        def attest(self, event, _attestation):
            self.source_event = dict(event)
            return True

    class Sink:
        event = None

        def emit_authenticated(self, event, *, authenticated):
            assert authenticated is True
            self.event = event

    attestor = Attestor()
    sink = Sink()
    result = BehaviorEventIngestor(sink=sink, attestation=attestor).ingest(
        _cfw_event())

    assert attestor.source_event["schema_version"] == CFW_RUNTIME_EVENT_SCHEMA_VERSION
    assert sink.event.to_dict()["schema_version"] == BEHAVIOR_EVENT_SCHEMA_VERSION
    assert result["schema_version"] == BEHAVIOR_EVENT_SCHEMA_VERSION
