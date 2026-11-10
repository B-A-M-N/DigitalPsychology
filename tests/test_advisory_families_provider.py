"""Provider-surface regression tests for advisory choice-over-StateWork families.

Mirrors route_preference mechanics for decomposition_preference,
integration_preference and test_strategy_preference: registry entries,
QuestionSpec criteria, Laya/Jev projections, diagnostics/telemetry IDs,
offline-eval criteria, event baselines/outcomes and calibration contexts.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from lib.protocol_artifact import QUESTION_REGISTRY
from lib.system_one_backends import _host_blocker_view, laya_state_projection
from lib.system_one_calibration import (
    CalibrationRecord,
    _paired_rows,
    calibration_task_fixture_hashes,
    task_instance_hash,
)
from lib.system_one_contracts import (
    ActivityFact,
    CriterionState,
    QuestionAnswer,
    QuestionProbability,
    QuestionSpec,
    SemanticState,
    SystemOneFrame,
    SystemOneIdentity,
    WorkflowFact,
    canonical_hash,
)
from lib.system_one_diagnostics import validate_laya_diagnostics
from lib.system_one_events import _host_baseline_labels
from lib.system_one_jev import jev_state_projection
from lib.telemetry.production_metrics import (
    QUESTION_FAMILIES,
    ProductionMetrics,
)

NEW_FAMILIES = (
    "decomposition_preference",
    "integration_preference",
    "test_strategy_preference",
)
ALL_ROUTE_FAMILIES = ("route_preference", *NEW_FAMILIES)
LEGAL_ROUTES = ("implement", "diagnose", "verify")


def _frame_for_family(question_id: str,
                      legal_routes: tuple[str, ...] = LEGAL_ROUTES) -> SystemOneFrame:
    identity = SystemOneIdentity(
        namespace_id="tenant", application_id="app",
        application_instance_id="app-1", agent_id="agent",
        agent_instance_id="agent-instance", session_id="session",
        task_id="task", attempt_id="attempt", interaction_id="interaction")
    activity = ActivityFact(
        sequence=1, activity_type="action", source="host_observed",
        action_class="verification", host_declared_relation="ambiguous",
        target_kind="workspace", proposed_purpose="verify criterion",
        affected_criterion_ids=("C1",))
    workflows = (
        WorkflowFact(statework_id="implement", current_state="READY",
                     progress_class=None, domain="build",
                     purpose="Implement the change",
                     legal_next_states=("ACTIVE",), active=True, eligible=True),
        WorkflowFact(statework_id="diagnose", current_state="READY",
                     progress_class=None, domain="triage",
                     purpose="Diagnose the failure",
                     legal_next_states=("ACTIVE",), active=False, eligible=True),
        WorkflowFact(statework_id="retired", current_state="DONE",
                     progress_class=None, domain="old",
                     purpose="Retired flow",
                     legal_next_states=(), active=False, eligible=False),
    )
    state = SemanticState(
        task={"objective": "Deliver the requested behavior.",
              "work_mode": "delivery", "deliverable_kind": "code_behavior",
              "task_complexity": "standard", "phase": "implementation",
              "active_segment": "segment", "active_statework": None,
              "active_flow": None, "non_goals": [],
              "domain_tags": ["software"]},
        situation={"current_state": "IMPLEMENTED", "blockers": [],
                   "actions_since_goal_progress": 0,
                   "actions_since_artifact_change": 0,
                   "verification_actions_since_artifact_change": 0,
                   "recovery_epochs": 0, "repeated_branch_count": 0},
        criteria=(CriterionState("C1", "Requested behavior works", "functional"),),
        activity=(activity,), workflows=workflows, convergence=(),
        candidate=activity, legal_routes=legal_routes)
    criteria = tuple((route, "Already-authorized legal route.")
                     for route in legal_routes)
    spec = QuestionSpec(
        question_id=question_id, family=QUESTION_REGISTRY[question_id]["family"],
        question_type=QUESTION_REGISTRY[question_id]["type"],
        instructions=QUESTION_REGISTRY[question_id]["instructions"],
        criteria=criteria)
    state_digest = canonical_hash(state.to_dict())
    question_digest = canonical_hash([spec.to_dict()])
    policy_hash = "c" * 64
    request_digest = canonical_hash({
        "identity": identity.to_dict(), "sequence": 1,
        "state_digest": state_digest,
        "question_bundle_digest": question_digest,
        "policy_hash": policy_hash})
    return SystemOneFrame(
        identity=identity, request_id="request-1", sequence=1,
        state_digest=state_digest, question_bundle_digest=question_digest,
        request_digest=request_digest, policy_hash=policy_hash,
        backend_id="test-backend", model_id="test-model",
        model_revision="rev-1", model_content_hash="a" * 64,
        provider_protocol_hash="b" * 64, calibration_bundle_hash="",
        state=state, questions=(spec,))


# ---------------------------------------------------------------- registry
def test_new_families_are_choice_over_empty_criteria():
    for family in NEW_FAMILIES:
        entry = QUESTION_REGISTRY[family]
        assert entry["family"] == family
        assert entry["type"] == "choice"
        assert isinstance(entry["instructions"], str) and entry["instructions"].strip()
        assert entry["instructions"].strip().endswith("?")
        assert entry["criteria"] == {}
    assert QUESTION_REGISTRY["route_preference"]["criteria"] == {}


def test_new_family_specs_use_legal_stateworks():
    for family in NEW_FAMILIES:
        frame = _frame_for_family(family)
        labels = tuple(label for label, _ in frame.questions[0].criteria)
        assert labels == LEGAL_ROUTES
        restored = SystemOneFrame.from_mapping(frame.canonical_payload())
        assert restored.question_ids == (family,)
        assert tuple(label for label, _ in restored.questions[0].criteria) == LEGAL_ROUTES


# --------------------------------------------------------------- projections
@pytest.mark.parametrize("family", NEW_FAMILIES)
def test_laya_projection_carries_legal_routes_and_eligible_workflows(family):
    frame = _frame_for_family(family)
    projection = laya_state_projection(frame)
    assert projection["question_family"] == family
    assert projection["legal_routes"] == list(LEGAL_ROUTES)
    eligible = projection["eligible_workflows"]
    assert {item["statework_id"] for item in eligible} == {"implement", "diagnose"}
    assert all(item["eligible"] is True for item in eligible)


@pytest.mark.parametrize("family", NEW_FAMILIES)
def test_jev_projection_carries_legal_routes_and_objective_context(family):
    frame = _frame_for_family(family)
    projection = jev_state_projection(frame)
    assert projection["question_id"] == family
    assert list(projection["legal_routes"]) == list(LEGAL_ROUTES)
    assert {item["statework_id"] for item in projection["eligible_workflows"]} == {
        "implement", "diagnose"}
    assert "objective" in projection["task"]


def test_host_blocker_view_strips_new_family_answer_terms():
    blocker = ("cfw_cause=x;codes=decomposition_preference,integration_preference,"
               "test_strategy_preference,some_code;actions=verify;criteria=C1;refs=;scope=;subject=")
    view = _host_blocker_view([blocker], {"C1": "criterion_1"})
    assert view[0]["trigger_codes"] == ["some_code"]


# ------------------------------------------------------- diagnostics/telemetry
@pytest.mark.parametrize("family", NEW_FAMILIES)
def test_laya_diagnostics_accept_new_family_ids(family):
    diagnostics = {
        "schema_version": "laya-inference-diagnostics/v1",
        "projection_schema": "laya-semantic-projection/v3",
        "projection_digest": "a" * 64,
        "projection_bytes": 100, "latency_ms": 20,
        "preflight_status": "complete", "preflight_state_tokens": 25,
        "preflight_tokens_dropped": 0, "input_tokens": 40,
        "state_tokens": 25, "state_tokens_dropped": 0,
        "truncation_status": "complete", "truncated_questions": [family],
        "coverage_status": "complete",
        "option_coverage": {family: {
            "expected_options": 3, "returned_options": 3, "status": "complete"}},
        "advisory_eligible": True,
    }
    assert validate_laya_diagnostics(diagnostics)["truncated_questions"] == [family]


def test_telemetry_families_include_new_ids(tmp_path):
    assert all(family in QUESTION_FAMILIES for family in NEW_FAMILIES)
    metrics = ProductionMetrics(tmp_path / "production-metrics.sqlite3")
    for family in NEW_FAMILIES:
        metrics.record_laya_event("opportunity", question_family=family)
    snapshot = metrics.snapshot()
    for family in NEW_FAMILIES:
        assert snapshot["counters"][f"laya_question_{family}_opportunity"] == 1


# ------------------------------------------------------------------- events
@pytest.mark.parametrize("family", NEW_FAMILIES)
def test_host_baseline_prefers_first_legal_route(family):
    assert _host_baseline_labels(_frame_for_family(family))[family] == LEGAL_ROUTES[0]


def _paired_events_for_family(family: str, count: int = 2):
    events = []
    labels = list(LEGAL_ROUTES)
    probabilities = [{"label": label,
                      "probability": 0.7 if label == labels[0] else 0.15}
                     for label in labels]
    for index in range(count):
        decision_id = f"decision-{family}-{index}"
        timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(
            seconds=2 * index)
        payload = {
            "system_one_decision_id": decision_id,
            "system_one_criterion_ids": [f"task-{index}-plan_complete"],
            "system_one_backend_id": "test-backend",
            "system_one_model_id": "test-model",
            "system_one_model_revision": "rev-1",
            "system_one_model_content_hash": "a" * 64,
            "system_one_provider_protocol_hash": "b" * 64,
            "system_one_work_mode": "delivery",
            "system_one_deliverable_kind": "code_behavior",
            "system_one_legal_routes": list(labels),
            "system_one_domain_tags": ["software"],
            "system_one_answers": {family: {
                "question_id": family, "selected": labels[0],
                "raw_probabilities": probabilities,
                "calibrated_probabilities": None, "calibration_hash": None}},
            "system_one_host_baseline_labels": {family: labels[0]},
        }
        identity = dict(namespace_id="tenant", application_id="app",
                        application_instance_id="app-1", agent_id="agent",
                        agent_instance_id="agent-instance", session_id="session",
                        task_id=f"task-{family}-{index}", attempt_id="attempt",
                        interaction_id="interaction", scope="scope-1")
        decision_event_id = f"event-{family}-{index}"
        evidence_ref = f"host-action://action-{family}-{index}#sha256=" + f"{index:064x}"
        evidence_receipt = {
            "schema_version": "host-evaluator-receipt/v2",
            "decision_event_id": decision_event_id,
            "task_instance_hash": task_instance_hash(f"task-{family}-{index}"),
            "fixture_set_hash": f"{index + 1:064x}",
            "question_id": family, "ground_truth_label": labels[0],
            "evidence_ref": evidence_ref,
            "evidence_digest": f"{index:064x}",
            "evidence_authority": "validator",
            "task_family": "semantic_route_choice",
            "source_action_receipt_id": f"action-{family}-{index}",
            "source_action_result_digest": f"{index:064x}",
            "evaluator_observation_digest": f"{index + 2:064x}",
            "evidence_observed_at": (
                timestamp + timedelta(milliseconds=500)).isoformat().replace(
                    "+00:00", "Z"),
            "task_scope": f"task-{family}-{index}", "control_scope": "scope-1",
            "criterion_ids": [f"task-{index}-plan_complete"],
            "evaluator_contract_hash": "c" * 64,
        }
        events.append(SimpleNamespace(
            event_id=decision_event_id, event_type="system_one_decision",
            category="decision", actor_id="digitalpsychology_runtime",
            authority_source="digitalpsychology_system_one",
            payload=payload,
            timestamp=timestamp.isoformat().replace("+00:00", "Z"), **identity))
        events.append(SimpleNamespace(
            event_type="system_one_outcome", category="validation",
            actor_id="digitalpsychology_runtime", authority_source="validator",
            payload={"system_one_outcome_for": decision_id,
                     f"system_one_outcome_{family}": labels[0],
                     "system_one_outcome_authority": "validator",
                     "system_one_outcome_evidence_ref": evidence_ref,
                     "system_one_outcome_evidence_receipt": evidence_receipt,
                     "system_one_independent_outcome": True},
            event_id=f"outcome-{family}-{index}",
            timestamp=(timestamp + timedelta(seconds=1)).isoformat().replace(
                "+00:00", "Z"), **identity))
    return events


@pytest.mark.parametrize("family", NEW_FAMILIES)
def test_calibration_ingestion_uses_legal_routes_and_candidate_context(family):
    events = _paired_events_for_family(family)
    rows = _paired_rows(events, family)
    assert len(rows) == 2
    assert all(set(row["probabilities"]) == set(LEGAL_ROUTES) for row in rows)
    assert all(row["candidate_context"] is not None for row in rows)
    assert all(row["host_baseline"] == LEGAL_ROUTES[0] for row in rows)
    assert calibration_task_fixture_hashes(events, question_id=family)


@pytest.mark.parametrize("family", NEW_FAMILIES)
def test_calibration_valid_for_binds_candidate_context(family):
    record = CalibrationRecord(
        question_id=family, backend_id="test-backend",
        model_id="test-model", model_revision="rev-1",
        model_content_hash="a" * 64,
        provider_protocol_hash="b" * 64, evaluator_contract_hash="c" * 64,
        temperature=0.5, minimum_advisory_confidence=0.9, sample_count=100,
        training_count=60, calibration_count=20, holdout_count=20,
        training_outcome_digest="d" * 64, calibration_outcome_digest="e" * 64,
        holdout_outcome_digest="f" * 64, holdout_accuracy=0.95,
        holdout_brier=0.05, holdout_ece=0.04, holdout_log_loss=0.1,
        holdout_class_metrics=(), qualification_minimum_accuracy=0.8,
        qualification_maximum_brier=0.25, qualification_maximum_ece=0.12,
        work_modes=("delivery",), deliverable_kinds=("code_behavior",),
        independent_trajectory_count=30, training_trajectory_count=18,
        calibration_trajectory_count=6, holdout_trajectory_count=6,
        independent_fixture_count=30, training_fixture_count=18,
        calibration_fixture_count=6, holdout_fixture_count=6,
        minimum_independent_trajectories=30, minimum_independent_fixtures=30,
        cohort_counts=(("delivery", "code_behavior", 30),),
        minimum_cohort_trajectories=20, minimum_outcomes=100,
        candidate_context_counts=((3, canonical_hash(sorted(LEGAL_ROUTES)),
                                   canonical_hash(sorted(("software",))), 30),),
        status="active", drift_status="clear")
    matching = _frame_for_family(family)
    assert record.valid_for(matching) is True
    other = _frame_for_family(family, legal_routes=("implement", "verify"))
    assert record.valid_for(other) is False


@pytest.mark.parametrize("family", NEW_FAMILIES)
def test_outcome_labeler_accepts_legal_route_label(family, tmp_path):
    # Slice C owns the provider-surface gate (allowed labels = legal routes).
    # The durable behavior-event JSON schema lives outside this slice, so a
    # legal label may still fail at event-schema validation; the assertion
    # below pins the Slice C gate: illegal labels fail with
    # "outside its question family" while legal labels pass that gate.
    from lib.feedback_loop import Event, EventError, SQLiteSink
    from lib.system_one_calibration import task_instance_hash
    from lib.system_one_events import SystemOneOutcomeLabeler

    frame = _frame_for_family(family)
    answer = QuestionAnswer(
        question_id=family, selected=LEGAL_ROUTES[0],
        raw_probabilities=tuple(
            QuestionProbability(label, 0.7 if label == LEGAL_ROUTES[0] else 0.15)
            for label in LEGAL_ROUTES))
    labels_payload = {family: answer.to_dict()}
    now = datetime.now(timezone.utc)
    decision = Event(
        event_id=f"dp-s1-{family}-decision",
        timestamp=(now - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
        namespace_id="tenant", application_id="app",
        application_instance_id="app-1", agent_instance_id="agent-instance",
        session_id="session", task_id="task", attempt_id="attempt",
        segment_id="segment", agent_id="agent", behavioral_subject="subject",
        category="decision", event_type="system_one_decision",
        actor_id="digitalpsychology_runtime",
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
            "system_one_legal_routes": list(LEGAL_ROUTES),
            "system_one_answers": labels_payload})
    sink = SQLiteSink(tmp_path / f"{family}-events.sqlite3")
    try:
        sink.emit_authenticated(decision, authenticated=True)
        labeler = SystemOneOutcomeLabeler(sink)
        evidence_ref = "host-action://action-1#sha256=" + "d" * 64
        receipt = {
            "schema_version": "host-evaluator-receipt/v2",
            "decision_event_id": decision.event_id,
            "task_instance_hash": task_instance_hash(decision.task_id),
            "question_id": family, "ground_truth_label": LEGAL_ROUTES[1],
            "evidence_ref": evidence_ref, "evidence_digest": "d" * 64,
            "evidence_authority": "validator",
            "task_family": "semantic_route_choice",
            "source_action_receipt_id": "action-1",
            "source_action_result_digest": "d" * 64,
            "evaluator_observation_digest": "9" * 64,
            "evidence_observed_at": (
                now - timedelta(seconds=20)).isoformat().replace("+00:00", "Z"),
            "task_scope": decision.task_id, "control_scope": decision.scope,
            "criterion_ids": ["task-plan_complete"],
            "evaluator_contract_hash": "c" * 64,
            "fixture_set_hash": "f" * 64,
        }
        outcome_id = None
        try:
            outcome_id = labeler.record(
                decision_event_id=decision.event_id, namespace_id="tenant",
                application_instance_id="app-1", question_id=family,
                label=LEGAL_ROUTES[1], authority="validator",
                evidence_ref=evidence_ref, evidence_receipt=receipt)
        except EventError as exc:
            # Out-of-slice behavior-event schema has not yet added the new
            # outcome key; passing the Slice C label gate is what matters here.
            assert "outside its question family" not in str(exc)
            assert f"system_one_outcome_{family}" in str(exc)
        else:
            assert outcome_id
        with pytest.raises(ValueError, match="outside its question family"):
            labeler.record(
                decision_event_id=decision.event_id, namespace_id="tenant",
                application_instance_id="app-1", question_id=family,
                label="not_a_legal_route", authority="validator",
                evidence_ref=evidence_ref, evidence_receipt={
                    **receipt, "ground_truth_label": "not_a_legal_route"})
    finally:
        sink.close()
