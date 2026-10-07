from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from lib.feedback_loop import Event, EventFactory, SQLiteSink
from lib.protocol_artifact import QUESTION_REGISTRY
from lib.system_one_calibration import calibration_metrics
from lib.system_one_cli import load_authenticated_system_one_events
from lib.system_one_contracts import (ActivityFact, CriterionState, QuestionAnswer,
                                      QuestionProbability, QuestionSpec, SemanticState,
                                      SystemOneFrame, SystemOneIdentity,
                                      SystemOneJudgment, canonical_hash)
from lib.system_one_events import (SystemOneOutcomeLabeler,
                                   prepare_system_one_decision_event)
from lib.control.transaction_service import _digest, _system_one_decision_refs


def _frame_and_judgment():
    identity = SystemOneIdentity(
        namespace_id="tenant", application_id="app", application_instance_id="app-1",
        agent_id="agent", agent_instance_id="agent-instance", session_id="session",
        task_id="task", attempt_id="attempt", interaction_id="interaction")
    activity = ActivityFact(
        sequence=1, activity_type="action", source="host_observed",
        host_declared_relation="ambiguous", action_class="verification",
        target_kind="workspace", proposed_purpose="verify criterion",
        affected_criterion_ids=("C1",))
    state = SemanticState(
        task={"objective": "Deliver the requested behavior.", "work_mode": "delivery",
              "deliverable_kind": "code_behavior", "task_complexity": "standard",
              "phase": "implementation", "active_segment": "segment",
              "active_statework": None, "active_flow": None,
              "non_goals": [], "domain_tags": ["software"]},
        situation={"current_state": "IMPLEMENTED", "blockers": [],
                   "actions_since_goal_progress": 0,
                   "actions_since_artifact_change": 0,
                   "verification_actions_since_artifact_change": 0,
                   "recovery_epochs": 0, "repeated_branch_count": 0},
        criteria=(CriterionState("C1", "Requested behavior works", "functional"),),
        activity=(activity,), workflows=(), convergence=(), candidate=activity,
        legal_routes=("implement", "verify"))
    question = QUESTION_REGISTRY["progress_relation"]
    spec = QuestionSpec(
        question_id="progress_relation", family=question["family"],
        question_type=question["type"], instructions=question["instructions"],
        criteria=tuple(question["criteria"].items()))
    state_digest = canonical_hash(state.to_dict())
    question_digest = canonical_hash([spec.to_dict()])
    policy_hash = "c" * 64
    request_id = "sys1-test-request"
    request_digest = canonical_hash({
        "identity": identity.to_dict(), "sequence": 1,
        "state_digest": state_digest,
        "question_bundle_digest": question_digest,
        "policy_hash": policy_hash,
    })
    frame = SystemOneFrame(
        identity=identity, request_id=request_id, sequence=1,
        state_digest=state_digest, question_bundle_digest=question_digest,
        request_digest=request_digest, policy_hash=policy_hash,
        backend_id="test-backend", model_id="test-model", model_revision="rev-1",
        model_content_hash="a" * 64, provider_protocol_hash="b" * 64,
        calibration_bundle_hash="", state=state, questions=(spec,))
    labels = tuple(question["criteria"])
    probabilities = tuple(QuestionProbability(
        label, 0.8 if label == "goal_progress" else 0.2 / (len(labels) - 1))
                         for label in labels)
    answer = QuestionAnswer(
        question_id="progress_relation", selected="goal_progress",
        raw_probabilities=probabilities)
    judgment = SystemOneJudgment(
        identity=identity, request_id=frame.request_id, sequence=frame.sequence,
        state_digest=frame.state_digest,
        question_bundle_digest=frame.question_bundle_digest,
        request_digest=frame.request_digest, policy_hash=frame.policy_hash,
        backend_id=frame.backend_id, model_id=frame.model_id,
        model_revision=frame.model_revision,
        model_content_hash=frame.model_content_hash,
        provider_protocol_hash=frame.provider_protocol_hash,
        calibration_bundle_hash=frame.calibration_bundle_hash, answers=(answer,))
    return frame, judgment


def _runtime_identity():
    return SimpleNamespace(
        namespace_id="tenant", application_id="app",
        application_instance_id="app-1", agent_instance_id="agent-instance",
        session_id="session", task_id="task", attempt_id="attempt",
        segment_id="segment", agent_id="agent", behavioral_subject="subject",
        goal_contract_hash=None)


def test_committed_system_one_decision_event_contains_pinned_answers():
    frame, judgment = _frame_and_judgment()
    descriptor = SimpleNamespace(identity=_runtime_identity(), scope="scope-1")
    event = prepare_system_one_decision_event(descriptor, {
        "system_one": {
            "frame": frame.canonical_payload(),
            "result": {"status": "shadow", "judgment": judgment.to_dict()},
        },
        "semantic_influence_receipt": {
            "changed_selection": True, "changed_mode": False,
            "changed_frameworks": [], "changed_statework_preference": False,
        },
    })
    assert event["event_type"] == "system_one_decision"
    assert event["authority_source"] == "digitalpsychology_system_one"
    assert event["payload"]["system_one_decision_id"] == frame.request_id
    assert event["payload"]["system_one_answers"]["progress_relation"][
        "raw_probabilities"] == [item.to_dict()
                                  for item in judgment.answers[0].raw_probabilities]
    assert event["payload"]["system_one_domain_tags"] == ["software"]
    assert event["payload"]["system_one_semantic_influenced"] is True
    assert EventFactory.from_dict(event).event_type == "system_one_decision"


def test_host_decision_reference_binds_candidate_and_domain_context():
    refs = _system_one_decision_refs([{
        "event_id": "dp-s1-route",
        "event_type": "system_one_decision",
        "payload": {
            "system_one_decision_id": "request-1",
            "system_one_question_ids": ["route_preference"],
            "system_one_tick_id": "tick-1",
            "system_one_boundary_kind": "post_action",
            "system_one_boundary_sequence": 4,
            "system_one_action_index": 2,
            "system_one_legal_routes": ["infrae", "gitter"],
            "system_one_domain_tags": ["workflow routing", "repository state"],
            "system_one_semantic_influenced": True,
            "system_one_semantic_influence_receipt_id": "sir-1",
        },
    }])

    assert refs == [{
        "event_id": "dp-s1-route", "decision_id": "request-1",
        "question_ids": ["route_preference"], "tick_id": "tick-1",
        "boundary_kind": "post_action", "boundary_sequence": 4,
        "action_index": 2, "legal_candidate_count": 2,
        "candidate_set_digest": _digest(["gitter", "infrae"]),
        "domain_tag_digest": _digest(["repository state", "workflow routing"]),
        "influence_receipt_id": "sir-1", "influenced": True,
    }]


def _stored_decision_event():
    now = datetime.now(timezone.utc)
    labels = tuple(QUESTION_REGISTRY["progress_relation"]["criteria"])
    probabilities = [{"label": label,
                      "probability": 0.8 if label == "goal_progress"
                      else 0.2 / (len(labels) - 1)} for label in labels]
    return Event(
        event_id="dp-s1-test-decision", timestamp=(now - timedelta(minutes=1)).isoformat(),
        namespace_id="tenant", application_id="app", application_instance_id="app-1",
        agent_instance_id="agent-instance", session_id="session", task_id="task",
        attempt_id="attempt", segment_id="segment", agent_id="agent",
        behavioral_subject="subject", category="decision",
        event_type="system_one_decision", actor_id="digitalpsychology_runtime",
        authority_source="digitalpsychology_system_one", scope="scope-1",
        interaction_id="interaction",
        payload={
            "system_one_decision_id": "sys1-request-1",
            "system_one_status": "shadow",
            "system_one_backend_id": "test-backend",
            "system_one_model_id": "test-model",
            "system_one_model_revision": "rev-1",
            "system_one_model_content_hash": "a" * 64,
            "system_one_provider_protocol_hash": "b" * 64,
            "system_one_work_mode": "delivery",
            "system_one_deliverable_kind": "code_behavior",
            "system_one_answers": {"progress_relation": {
                "question_id": "progress_relation", "selected": "goal_progress",
                "raw_probabilities": probabilities,
                "calibrated_probabilities": None, "calibration_hash": None}},
        })


def test_outcome_labeler_persists_only_later_authenticated_evidence(tmp_path):
    sink = SQLiteSink(tmp_path / "events.sqlite3")
    try:
        decision = _stored_decision_event()
        sink.emit_authenticated(decision, authenticated=True)
        labeler = SystemOneOutcomeLabeler(sink)
        outcome_id = labeler.record(
            decision_event_id=decision.event_id,
            namespace_id=decision.namespace_id,
            application_instance_id=decision.application_instance_id,
            question_id="progress_relation", label="goal_progress",
            authority="validator", evidence_ref="validator-evidence-1")
        assert labeler.record(
            decision_event_id=decision.event_id,
            namespace_id=decision.namespace_id,
            application_instance_id=decision.application_instance_id,
            question_id="progress_relation", label="goal_progress",
            authority="validator", evidence_ref="validator-evidence-1") == outcome_id
        outcome = sink.load_authenticated_event(
            outcome_id, namespace_id="tenant", application_instance_id="app-1")
        assert outcome.event_type == "system_one_outcome"
        assert outcome.category == "validation"
        assert outcome.payload["system_one_independent_outcome"] is True
        assert calibration_metrics([decision, outcome])["independent_outcomes"] == 1
        loaded = load_authenticated_system_one_events(sink.path)
        assert {item.event_id for item in loaded} == {decision.event_id, outcome_id}
        with pytest.raises(ValueError, match="different independent outcome"):
            labeler.record(
                decision_event_id=decision.event_id,
                namespace_id=decision.namespace_id,
                application_instance_id=decision.application_instance_id,
                question_id="progress_relation", label="no_progress",
                authority="validator", evidence_ref="validator-evidence-2")
        with pytest.raises(ValueError, match="independent evidence authority"):
            labeler.record(
                decision_event_id=decision.event_id,
                namespace_id=decision.namespace_id,
                application_instance_id=decision.application_instance_id,
                question_id="progress_relation", label="goal_progress",
                authority="digitalpsychology_runtime", evidence_ref="agent-claim")
    finally:
        sink.close()


def test_aborted_or_uncommitted_system_one_decisions_are_not_persisted(tmp_path):
    sink = SQLiteSink(tmp_path / "events.sqlite3")
    try:
        labeler = SystemOneOutcomeLabeler(sink)
        with pytest.raises(ValueError, match="authenticated System-1 decision"):
            labeler.record(
                decision_event_id="prepared-but-uncommitted",
                namespace_id="tenant", application_instance_id="app-1",
                question_id="progress_relation", label="goal_progress",
                authority="validator", evidence_ref="validator-evidence")
    finally:
        sink.close()
