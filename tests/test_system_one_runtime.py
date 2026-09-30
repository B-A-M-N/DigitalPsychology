from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from lib.system_one_backends import JevCompatibleHTTPBackend
from lib.system_one_calibration import (
    CalibrationRecord, calibration_metrics, calibration_record_from_events,
)
from lib.system_one_contracts import (
    PROTOCOL_HASH, SystemOneFrame, SystemOneIdentity, SystemOneJudgment,
)
from lib.system_one_runtime import CircuitBreaker, SystemOneRuntime


def frame(*, agent="agent-1", sequence=1, request_id=None, state_digest="b" * 64):
    return SystemOneFrame(
        identity=SystemOneIdentity(
            namespace_id="tenant", application_id="app", application_instance_id="app-1",
            agent_id="agent", agent_instance_id=agent, session_id="session-1",
            task_id="task-1", attempt_id="attempt-1", interaction_id="interaction-1"),
        request_id=request_id or f"request-{sequence}", sequence=sequence,
        state_digest=state_digest, policy_hash="a" * 64, backend_id="test-backend",
        model_id="test-model", calibration_hash="", questions=("progress_relation",),
        objective="Deliver the requested change.", criteria=(("C1", "Required outcome"),),
        recent_activity=("action:write:ambiguous",),
        convergence=(("support_actions_since_goal_progress", 0),), csw_facts=(),
        candidate=(("binding_id", "write"),))


def judgment(request, *, request_id=None, sequence=None, label="goal_progress"):
    return SystemOneJudgment(
        identity=request.identity, request_id=request_id or request.request_id,
        sequence=sequence or request.sequence, state_digest=request.state_digest,
        policy_hash=request.policy_hash, backend_id=request.backend_id,
        model_id=request.model_id, calibration_hash=request.calibration_hash,
        progress_relation=label, confidence=0.97,
        protocol_hash=PROTOCOL_HASH)


class FixedBackend:
    backend_id = "test-backend"
    model_id = "test-model"

    def __init__(self, responder=None):
        self.calls = 0
        self.responder = responder

    def judge(self, value):
        self.calls += 1
        if self.responder:
            return self.responder(value)
        return judgment(value)


def test_response_contract_rejects_unknown_fields_and_invalid_labels():
    value = judgment(frame()).to_dict()
    with pytest.raises(ValueError, match="unknown fields"):
        SystemOneJudgment.from_dict({**value, "narrative": "should never enter protocol"})
    with pytest.raises(ValueError, match="invalid progress_relation"):
        SystemOneJudgment.from_dict({**value, "progress_relation": "trust me"})


def test_http_backend_uses_one_loopback_transport_call_and_caps_timeout():
    calls = []

    def transport(request, timeout):
        calls.append((request, timeout))
        sent = json.loads(request.data.decode("utf-8"))
        incoming = SystemOneFrame(
            identity=SystemOneIdentity(**sent["identity"]), request_id=sent["request_id"],
            sequence=sent["sequence"], state_digest=sent["state_digest"],
            policy_hash=sent["policy_hash"], backend_id=sent["backend_id"],
            model_id=sent["model_id"], calibration_hash=sent["calibration_hash"],
            questions=tuple(sent["questions"]), objective=sent["objective"],
            criteria=tuple((row["criterion_id"], row["claim"]) for row in sent["criteria"]),
            recent_activity=tuple(sent["recent_activity"]),
            convergence=tuple(sorted(sent["convergence"].items())),
            csw_facts=tuple(sent["csw_facts"]),
            candidate=tuple(sorted(sent["candidate"].items())),
            route_candidates=tuple(sent["route_candidates"]),
            protocol_version=sent["protocol_version"], protocol_hash=sent["protocol_hash"])
        return json.dumps(judgment(incoming).to_dict()).encode("utf-8")

    backend = JevCompatibleHTTPBackend(
        "http://127.0.0.1:8765/judge", "test-model", timeout_ms=250,
        transport=transport)
    response = backend.judge(frame())
    assert response.progress_relation == "goal_progress"
    assert len(calls) == 1
    assert calls[0][1] == 0.25

    failed_calls = []

    def fail_once(request, timeout):
        failed_calls.append(request)
        raise OSError("offline")

    failing = JevCompatibleHTTPBackend(
        "http://localhost:8765/judge", "test-model", transport=fail_once)
    with pytest.raises(OSError):
        failing.judge(frame())
    assert len(failed_calls) == 1


def test_runtime_cache_is_scoped_and_rebinds_correlation():
    backend = FixedBackend()
    runtime = SystemOneRuntime(backend, cache_size=4)
    first = runtime.judge(frame(sequence=1), mode="shadow")
    second = runtime.judge(frame(sequence=2), mode="shadow")
    other_agent = runtime.judge(frame(agent="agent-2", sequence=1), mode="shadow")
    assert first.judgment is not None
    assert second.cache_hit is True
    assert second.judgment.request_id == "request-2"
    assert second.judgment.sequence == 2
    assert other_agent.cache_hit is False
    assert backend.calls == 2


def test_runtime_falls_back_on_stale_or_malformed_response():
    stale_backend = FixedBackend(lambda value: judgment(value, request_id="old-request"))
    stale = SystemOneRuntime(stale_backend).judge(frame(), mode="shadow")
    assert stale.status == "fallback"
    assert stale.judgment is None

    malformed_backend = FixedBackend(lambda value: SystemOneJudgment(
        identity=value.identity, request_id=value.request_id, sequence=value.sequence,
        state_digest=value.state_digest, policy_hash=value.policy_hash,
        backend_id=value.backend_id, model_id=value.model_id,
        calibration_hash=value.calibration_hash, progress_relation="no_progress",
        confidence=2.0))
    malformed = SystemOneRuntime(malformed_backend).judge(frame(), mode="shadow")
    assert malformed.status == "fallback"
    assert malformed.judgment is None


def test_circuit_breaker_stops_calls_until_cooldown_without_retry():
    now = [0.0]
    calls = []

    class BrokenBackend:
        backend_id = "test-backend"
        model_id = "test-model"

        def judge(self, value):
            calls.append(value.request_id)
            raise OSError("offline")

    breaker = CircuitBreaker(threshold=2, cooldown_seconds=5,
                             clock=lambda: now[0])
    runtime = SystemOneRuntime(BrokenBackend(), breaker=breaker)
    assert runtime.judge(frame(sequence=1), mode="shadow").status == "fallback"
    assert runtime.judge(frame(sequence=2), mode="shadow").status == "fallback"
    assert runtime.judge(frame(sequence=3), mode="shadow").status == "circuit_open"
    assert calls == ["request-1", "request-2"]
    now[0] = 6.0
    assert runtime.judge(frame(sequence=4), mode="shadow").status == "fallback"
    assert calls == ["request-1", "request-2", "request-4"]


def event(event_type, payload, *, agent="agent-1"):
    return SimpleNamespace(
        event_type=event_type, payload=payload, namespace_id="tenant", application_id="app",
        application_instance_id="app-1", agent_id="agent", agent_instance_id=agent,
        session_id="session-1", task_id="task-1", attempt_id="attempt-1",
        interaction_id="interaction-1")


def test_calibration_uses_later_independent_outcome_only():
    prediction = event("system_one_decision", {
        "system_one_decision_id": "decision-1",
        "system_one_progress_relation": "goal_progress",
        "system_one_confidence": 0.9,
    })
    outcome = event("system_one_outcome", {
        "system_one_outcome_for": "decision-1",
        "system_one_outcome_progress_relation": "goal_progress",
        "system_one_outcome_authority": "validator",
        "system_one_outcome_evidence_ref": "evidence-1",
        "system_one_independent_outcome": True,
    })
    self_prediction_only = event("system_one_decision", {
        "system_one_decision_id": "decision-2",
        "system_one_progress_relation": "goal_progress",
        "system_one_confidence": 0.99,
    })
    events = (prediction, outcome, self_prediction_only)
    report = calibration_metrics(events)
    assert report["independent_outcomes"] == 1
    assert report["accuracy"] == 1.0
    record = calibration_record_from_events(
        events, backend_id="test-backend", model_id="test-model", minimum_outcomes=1)
    assert record.independent_outcomes == 1
    assert record.outcome_digest
    assert record.valid_for(backend_id="test-backend", model_id="test-model",
                            expected_hash=record.digest, minimum_outcomes=1)


def test_calibration_record_rejects_unpaired_or_untrusted_outcomes():
    unpaired = event("system_one_outcome", {
        "system_one_outcome_for": "missing",
        "system_one_outcome_progress_relation": "goal_progress",
        "system_one_outcome_authority": "validator",
        "system_one_outcome_evidence_ref": "evidence-1",
        "system_one_independent_outcome": True,
    })
    with pytest.raises(ValueError, match="insufficient independent"):
        calibration_record_from_events(
            (unpaired,), backend_id="test-backend", model_id="test-model",
            minimum_outcomes=1)

    with pytest.raises(ValueError, match="independent outcomes"):
        CalibrationRecord(backend_id="test-backend", model_id="test-model",
                          sample_count=0, independent_outcomes=1,
                          outcome_digest="a" * 64)
