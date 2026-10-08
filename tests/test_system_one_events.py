from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sqlite3
from dataclasses import replace
from types import SimpleNamespace

import pytest

from lib.feedback_loop import Event, EventFactory, SQLiteSink
from lib.behavior_state import BehaviorState
from lib.control.service import ControlPlaneService
from lib.protocol_artifact import QUESTION_REGISTRY
from lib.system_one_calibration import calibration_metrics, task_instance_hash
from lib.system_one_cli import (laya_corpus_status, laya_corpus_status_main,
                                load_authenticated_system_one_events)
from lib.system_one_contracts import (ActivityFact, CriterionState, QuestionAnswer,
                                      QuestionProbability, QuestionSpec, SemanticState,
                                      SystemOneFrame, SystemOneIdentity,
                                      SystemOneJudgment, canonical_hash)
from lib.system_one_events import (SystemOneOutcomeLabeler,
                                   prepare_system_one_decision_event)
from lib.runtime.store import BehaviorStateStore
from lib.service import DigitalPsychologyService
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
        goal_contract_hash=None, scope="scope-1")


def test_committed_system_one_decision_event_contains_pinned_answers():
    frame, judgment = _frame_and_judgment()
    descriptor = SimpleNamespace(identity=_runtime_identity())
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
        "event_id": "dp-s1-route", "timestamp": "", "decision_id": "request-1",
        "question_ids": ["route_preference"], "tick_id": "tick-1",
        "criterion_ids": [], "candidate_relation_certainty": None,
        "candidate_host_relation": None, "candidate_criterion_ids": [],
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
            "system_one_criterion_ids": ["task-plan_complete"],
            "system_one_work_mode": "delivery",
            "system_one_deliverable_kind": "code_behavior",
            "system_one_answers": {"progress_relation": {
                "question_id": "progress_relation", "selected": "goal_progress",
                "raw_probabilities": probabilities,
                "calibrated_probabilities": None, "calibration_hash": None}},
        })


def _evidence_receipt(decision, evidence_ref, label, **overrides):
    digest = evidence_ref.rsplit("#sha256=", 1)[-1]
    receipt = {
        "schema_version": "host-evaluator-receipt/v2",
        "decision_event_id": decision.event_id,
        "question_id": "progress_relation",
        "ground_truth_label": label,
        "evidence_ref": evidence_ref,
        "evidence_digest": digest,
        "evidence_authority": "validator",
        "task_family": "semantic_progress_relation",
        "source_action_receipt_id": evidence_ref.rpartition("#sha256=")[0].removeprefix(
            "host-action://"),
        "source_action_result_digest": digest,
        "evaluator_observation_digest": "9" * 64,
        "evidence_observed_at": (
            datetime.now(timezone.utc) - timedelta(seconds=20)
        ).isoformat().replace("+00:00", "Z"),
        "task_scope": decision.task_id,
        "control_scope": decision.scope,
        "criterion_ids": ["task-plan_complete"],
        "evaluator_contract_hash": "c" * 64,
        "task_instance_hash": task_instance_hash(decision.task_id),
        "fixture_set_hash": "f" * 64,
    }
    receipt.update(overrides)
    return receipt


def test_outcome_labeler_persists_only_later_authenticated_evidence(tmp_path, capsys):
    sink = SQLiteSink(tmp_path / "events.sqlite3")
    try:
        decision = _stored_decision_event()
        sink.emit_authenticated(decision, authenticated=True)
        labeler = SystemOneOutcomeLabeler(sink)
        evidence_ref = "host-action://action-1#sha256=" + "d" * 64
        evidence_receipt = _evidence_receipt(
            decision, evidence_ref, "goal_progress")
        outcome_id = labeler.record(
            decision_event_id=decision.event_id,
            namespace_id=decision.namespace_id,
            application_instance_id=decision.application_instance_id,
            question_id="progress_relation", label="goal_progress",
            authority="validator", evidence_ref=evidence_ref,
            evidence_receipt=evidence_receipt)
        assert labeler.record(
            decision_event_id=decision.event_id,
            namespace_id=decision.namespace_id,
            application_instance_id=decision.application_instance_id,
            question_id="progress_relation", label="goal_progress",
            authority="validator", evidence_ref=evidence_ref,
            evidence_receipt=evidence_receipt) == outcome_id
        outcome = sink.load_authenticated_event(
            outcome_id, namespace_id="tenant", application_instance_id="app-1")
        assert outcome.event_type == "system_one_outcome"
        assert outcome.category == "validation"
        assert outcome.payload["system_one_independent_outcome"] is True
        assert calibration_metrics([decision, outcome])["independent_outcomes"] == 1
        assert sink.authenticated_event_ids(
            [decision.event_id, outcome_id, "untrusted-id"],
            namespace_id="tenant", application_instance_id="app-1") == {
                decision.event_id, outcome_id}
        assert sink.authenticated_event_type_counts(
            scope="scope-1", task_id="task", namespace_id="tenant",
            application_instance_id="app-1",
            event_types=("system_one_decision", "system_one_outcome")) == {
                "system_one_decision": 1, "system_one_outcome": 1}
        loaded = load_authenticated_system_one_events(sink.path)
        assert {item.event_id for item in loaded} == {decision.event_id, outcome_id}
        status = laya_corpus_status(sink.path)
        assert status["event_database_path"] == str(sink.path.resolve())
        assert status["read_only"] is True
        assert status["event_type_counts"]["system_one_decision"] == {
            "total": 1, "authenticated": 1}
        assert status["event_type_counts"]["system_one_outcome"] == {
            "total": 1, "authenticated": 1}
        assert status["question_families"]["progress_relation"] == {
            "authenticated_decisions": 1,
            "authenticated_outcomes": 1,
            "paired_independent_outcomes": 1,
            "independent_trajectories": 1,
            "task_instances": 1,
            "independent_fixtures": 1,
        }
        assert laya_corpus_status_main(["--event-db", str(sink.path)]) == 0
        cli_output = capsys.readouterr().out
        assert "\"paired_independent_outcomes\": 1" in cli_output
        assert "validator-evidence-1" not in cli_output
        assert "system_one_answers" not in cli_output
        with pytest.raises(ValueError, match="different independent outcome"):
            labeler.record(
                decision_event_id=decision.event_id,
                namespace_id=decision.namespace_id,
            application_instance_id=decision.application_instance_id,
            question_id="progress_relation", label="no_progress",
            authority="validator",
            evidence_ref="host-action://action-2#sha256=" + "e" * 64,
            evidence_receipt=_evidence_receipt(
                decision, "host-action://action-2#sha256=" + "e" * 64,
                "no_progress"))
        with pytest.raises(ValueError, match="independent evidence authority"):
            labeler.record(
                decision_event_id=decision.event_id,
                namespace_id=decision.namespace_id,
                application_instance_id=decision.application_instance_id,
                question_id="progress_relation", label="goal_progress",
                authority="digitalpsychology_runtime", evidence_ref="agent-claim")
    finally:
        sink.close()


def test_committed_system_one_outbox_retries_after_sqlite_delivery_failure(tmp_path):
    decision = _stored_decision_event()
    behavior_path = tmp_path / "behavior.sqlite3"
    event_path = tmp_path / "events.sqlite3"
    store = BehaviorStateStore(behavior_path)
    store.save(
        decision.scope, expected_revision=0, expected_action_index=-1,
        action_index=1, state=BehaviorState(revision=1),
        outbox_events=(decision.to_dict(),))

    first_dp = DigitalPsychologyService(event_db=event_path)
    with sqlite3.connect(event_path) as database:
        database.execute(
            "CREATE TRIGGER fail_system_one_delivery "
            "BEFORE INSERT ON events WHEN NEW.event_type='system_one_decision' "
            "BEGIN SELECT RAISE(ABORT, 'simulated transient SQLite failure'); END")
    first_control = ControlPlaneService(decision_service=first_dp,
                                        state_store=store)
    try:
        first_control._dispatch_outbox(decision.scope)
        assert store.load(decision.scope).state.revision == 1
        failed = store.outbox_delivery_status(
            decision.scope, authenticated_event_ids=())
        assert failed["committed_count"] == failed["enqueued_count"] == 1
        assert failed["delivered_count"] == failed["authenticated_count"] == 0
        assert failed["pending_count"] == 1
        assert failed["last_dispatch_failure"]["error_class"] == "EventError"
        assert store.outbox_delivery_records(decision.scope)[0]["attempt_count"] == 1
        corpus = laya_corpus_status(event_path, behavior_path)["runtime_diagnostics"]
        assert corpus["delivery_history_available"] is True
        assert corpus["outbox_history_available"] is True
        assert corpus["pending_outbox_events"] == 1
        assert corpus["last_dispatch_failure"]["error_class"] == "EventError"
        assert "committed_event_delivery_pending" in corpus["diagnostics"]
    finally:
        first_control.close()

    with sqlite3.connect(event_path) as database:
        database.execute("DROP TRIGGER fail_system_one_delivery")
    restarted_store = BehaviorStateStore(behavior_path)
    restarted_dp = DigitalPsychologyService(event_db=event_path)
    restarted_control = ControlPlaneService(
        decision_service=restarted_dp, state_store=restarted_store)
    try:
        restarted_control._dispatch_outbox(decision.scope)
        restarted_control._dispatch_outbox(decision.scope)
        authenticated = restarted_dp.authenticated_event_ids(
            (decision.event_id,), namespace_id=decision.namespace_id,
            application_instance_id=decision.application_instance_id)
        delivered = restarted_store.outbox_delivery_status(
            decision.scope, authenticated_event_ids=authenticated)
        assert delivered["pending_count"] == 0
        assert delivered["delivered_count"] == delivered["authenticated_count"] == 1
        assert delivered["last_dispatch_failure"]["error_class"] == "EventError"
        records = restarted_store.outbox_delivery_records(decision.scope)
        assert records[0]["attempt_count"] == 2
        assert records[0]["delivered_at"]
        assert records[0]["destination_path"] == str(event_path.resolve())
        corpus = laya_corpus_status(event_path, behavior_path)["runtime_diagnostics"]
        assert corpus["pending_outbox_events"] == 0
        assert corpus["delivery_by_event_type"]["system_one_decision"][
            "authenticated_in_selected_store"] == 1
        assert corpus["delivered_to_another_database"] == 0
        with sqlite3.connect(event_path) as database:
            assert database.execute(
                "SELECT COUNT(*) FROM events WHERE event_id=?",
                (decision.event_id,)).fetchone()[0] == 1
            assert database.execute(
                "SELECT COUNT(*) FROM authenticated_events WHERE event_id=?",
                (decision.event_id,)).fetchone()[0] == 1
    finally:
        restarted_control.close()


def test_corpus_status_retains_query_attempt_without_committed_tick_after_restart(
        tmp_path):
    event_path = tmp_path / "events.sqlite3"
    behavior_path = tmp_path / "behavior.sqlite3"
    sink = SQLiteSink(event_path)
    try:
        scope = "scope-query-without-tick"
        store = BehaviorStateStore(behavior_path)
        store.begin_control_tick_attempt(
            scope, "tick-no-commit", attempt_id="a" * 32,
            boundary_sequence=7, request_digest="b" * 64)
        store.update_control_tick_attempt(
            "a" * 32, status="inference_complete", query_opportunity=True,
            question_ids=("progress_relation",), query_attempted=True,
            judgment_returned=True, query_failed=False)

        # Reopening the state store represents a fresh host process. The tick
        # attempt remains visible even though control_ticks has no commit row.
        restarted = BehaviorStateStore(behavior_path)
        status = laya_corpus_status(event_path, behavior_path)
        runtime = status["runtime_diagnostics"]
        assert runtime["query_attempt_history_available"] is True
        assert runtime["control_tick_attempts"] == 1
        assert runtime["system_one_query_opportunities"] == 1
        assert runtime["system_one_query_attempts"] == 1
        assert runtime["system_one_judgments_returned"] == 1
        assert runtime["query_without_committed_tick_count"] == 1
        assert runtime["query_attempts_without_committed_tick"][0]["tick_id"] == (
            "tick-no-commit")
        assert "system_one_query_without_committed_tick" in runtime["diagnostics"]
        assert runtime["query_history_started_at"]
        assert restarted.system_one_tick_attempts(scope=scope)[0]["status"] == (
            "inference_complete")
    finally:
        sink.close()


def test_corpus_status_distinguishes_no_query_unauthenticated_and_missing_outcome(
        tmp_path):
    event_path = tmp_path / "events.sqlite3"
    behavior_path = tmp_path / "behavior.sqlite3"
    sink = SQLiteSink(event_path)
    try:
        sink.emit(_stored_decision_event())
        authenticated_decision = replace(
            _stored_decision_event(), event_id="dp-s1-authenticated-without-outcome")
        sink.emit_authenticated(authenticated_decision, authenticated=True)

        runtime = laya_corpus_status(
            event_path, BehaviorStateStore(behavior_path).path)["runtime_diagnostics"]
        assert runtime["query_attempt_history_available"] is True
        assert runtime["query_tick_history_available"] is True
        assert runtime["control_tick_attempts"] == 0
        assert runtime["system_one_query_attempts"] == 0
        assert "no_control_tick_attempt_recorded_since_audit_start" in runtime[
            "diagnostics"]
        assert runtime["events_present_without_authentication"] == 1
        assert runtime["unpaired_authenticated_decisions"]["progress_relation"] == 1
        assert "event_present_without_authentication" in runtime["diagnostics"]
        assert "authenticated_decision_missing_outcome" in runtime["diagnostics"]
    finally:
        sink.close()


def test_corpus_status_identifies_delivery_to_another_or_missing_event_store(
        tmp_path):
    source_path = tmp_path / "source-events.sqlite3"
    selected_path = tmp_path / "selected-events.sqlite3"
    behavior_path = tmp_path / "behavior.sqlite3"
    source_sink = SQLiteSink(source_path)
    selected_sink = SQLiteSink(selected_path)
    try:
        decision = _stored_decision_event()
        source_sink.emit_authenticated(decision, authenticated=True)
        store = BehaviorStateStore(behavior_path)
        store.save(
            decision.scope, expected_revision=0, expected_action_index=-1,
            action_index=0, state=BehaviorState(revision=1),
            outbox_events=(decision.to_dict(),))
        store.record_outbox_destination(decision.scope, decision.event_id,
                                        str(source_path))
        store.record_outbox_dispatch_attempt(decision.scope, decision.event_id)
        store.acknowledge_outbox_event(decision.scope, decision.event_id)

        diagnostics = laya_corpus_status(
            selected_path, behavior_path)["runtime_diagnostics"]
        assert diagnostics["delivered_to_another_database"] == 1
        assert "event_delivery_not_in_selected_store" in diagnostics["diagnostics"]

        # Model a durable delivery receipt whose declared target matches the
        # selected DB, while the event itself is absent there.
        missing_decision = replace(
            decision, event_id="dp-s1-missing-in-selected", scope="scope-missing")
        store.save(
            "scope-missing", expected_revision=0, expected_action_index=-1,
            action_index=0, state=BehaviorState(revision=1),
            outbox_events=(missing_decision.to_dict(),))
        store.record_outbox_destination("scope-missing", missing_decision.event_id,
                                        str(selected_path))
        store.record_outbox_dispatch_attempt("scope-missing", missing_decision.event_id)
        store.acknowledge_outbox_event("scope-missing", missing_decision.event_id)
        missing = laya_corpus_status(
            selected_path, behavior_path)["runtime_diagnostics"]
        assert missing["delivered_missing_from_selected_database"] == 1
        assert "delivery_receipt_without_selected_event" in missing["diagnostics"]
    finally:
        source_sink.close()
        selected_sink.close()


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


@pytest.mark.parametrize("invalid_receipt,question_id,evidence_ref", [
    ({"evidence_digest": "0" * 64}, "progress_relation",
     "host-action://action#sha256=" + "1" * 64),
    ({"task_scope": "another-task"}, "progress_relation",
     "host-action://action#sha256=" + "1" * 64),
    ({"evidence_observed_at": "2000-01-01T00:00:00Z"}, "progress_relation",
     "host-action://action#sha256=" + "1" * 64),
    ({}, "retry_relation", "host-action://action#sha256=" + "1" * 64),
    ({"criterion_ids": ["another-task-other-criterion"]}, "progress_relation",
     "host-action://action#sha256=" + "1" * 64),
    ({"source_action_receipt_id": "different-action"}, "progress_relation",
     "host-action://action#sha256=" + "1" * 64),
    ({"source_action_result_digest": "2" * 64}, "progress_relation",
     "host-action://action#sha256=" + "1" * 64),
    ({"evaluator_observation_digest": "invalid"}, "progress_relation",
     "host-action://action#sha256=" + "1" * 64),
])
def test_outcome_labeler_rejects_unresolved_or_mismatched_evidence(
        tmp_path, invalid_receipt, question_id, evidence_ref):
    sink = SQLiteSink(tmp_path / "events.sqlite3")
    try:
        decision = _stored_decision_event()
        sink.emit_authenticated(decision, authenticated=True)
        base = _evidence_receipt(decision, evidence_ref, "goal_progress")
        base.update(invalid_receipt)
        with pytest.raises(ValueError):
            SystemOneOutcomeLabeler(sink).record(
                decision_event_id=decision.event_id,
                namespace_id=decision.namespace_id,
                application_instance_id=decision.application_instance_id,
                question_id=question_id, label="goal_progress",
                authority="validator", evidence_ref=evidence_ref,
                evidence_receipt=base, expected_scope=decision.scope)
    finally:
        sink.close()
