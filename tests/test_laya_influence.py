from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from lib.behavior_state import (
    BehaviorState,
    CSWBehaviorSnapshot,
    FrameworkSignal,
    GoalItemSnapshot,
    GoalSetSnapshot,
)
from lib.control.engine import ControlDecisionEngine
from lib.control.semantic_influence import (
    SemanticInfluenceReceipt,
    compare_semantic_selection,
)
from lib.control.system_one import create_system_one_frame_factory
from lib.control_protocol import ControlSessionDescriptor, ControlTickRequest
from lib.fast_loop import BehavioralController
from lib.outcomes import (
    HostOutcomeFact,
    InterventionEvaluationFrame,
    InterventionEventContext,
    InterventionEvaluator,
)
from lib.runtime.contracts import RuntimeIdentity, RuntimeObservation
from lib.runtime.store import RuntimeStateRecord
from lib.service import DigitalPsychologyService
from lib.system_one_backends import DisabledSystemOneBackend
from lib.system_one_service import SystemOneService


QUESTION_ANSWER = SimpleNamespace(
    question_id="retry_relation",
    selected="equivalent_retry",
    calibrated_confidence=0.91,
)


def _judgment():
    return SimpleNamespace(request_id="laya-decision-1", answers=(QUESTION_ANSWER,))


def _selection(candidate_id: str, *, mode: str, frameworks: dict[str, str],
               preferred_stateworks: tuple[str, ...]):
    return SimpleNamespace(
        selected_candidate_id=candidate_id,
        state=SimpleNamespace(cognitive_mode=mode, framework_modes=frameworks),
        preferred_stateworks=preferred_stateworks,
    )


def _identity() -> RuntimeIdentity:
    return RuntimeIdentity(
        namespace_id="tenant-a", application_id="codex",
        application_instance_id="instance-1", agent_instance_id="agent-1",
        session_id="session-1", task_id="task-1", attempt_id="attempt-1",
        workflow_id="implementation", subject_ref="repo:example",
        segment_id="segment-0", agent_id="codex", behavioral_subject="task-1",
        goal_contract_hash="a" * 64,
    )


def test_counterfactual_receipt_records_semantic_selection_differences():
    counterfactual = _selection(
        "candidate-deterministic", mode="DIAGNOSE",
        frameworks={"owl": "elevated", "fuse": "normal"},
        preferred_stateworks=("inspect-flow",))
    actual = _selection(
        "candidate-laya", mode="RECOVER",
        frameworks={"owl": "reset", "fuse": "constrained"},
        preferred_stateworks=("repair-flow",))

    receipt = compare_semantic_selection(_judgment(), counterfactual, actual)

    assert isinstance(receipt, SemanticInfluenceReceipt)
    assert receipt.receipt_id.startswith("sir-")
    assert len(receipt.receipt_id) == 28
    assert receipt.provider_decision_id == "laya-decision-1"
    assert receipt.question_ids == ("retry_relation",)
    assert dict(receipt.selected_answers) == {"retry_relation": "equivalent_retry"}
    assert dict(receipt.calibrated_confidences) == {"retry_relation": 0.91}
    assert receipt.counterfactual_candidate_id == "candidate-deterministic"
    assert receipt.selected_candidate_id == "candidate-laya"
    assert receipt.changed_selection is True
    assert receipt.changed_mode is True
    assert receipt.changed_frameworks == ("fuse", "owl")
    assert receipt.changed_statework_preference is True
    assert receipt.influenced is True
    assert receipt.to_dict()["selected_candidate_id"] == "candidate-laya"


def _prepare_retry_decision(*, influenced: bool):
    identity = _identity()
    goals = GoalSetSnapshot(1, (GoalItemSnapshot(
        "criterion-visual", status="ACTIVE",
        claim="Render the requested page at the target viewport.",
        equivalent_retry_count=1),))
    descriptor = ControlSessionDescriptor(
        identity=identity, objective="Render the requested page",
        objective_criteria=("criterion-visual",), initial_goal_set=goals,
        available_frameworks=("owl", "anchor", "fuse", "ward", "flow", "sispis", "dox"),
        cfw_authority_identity={}, csw_authority_identity={},
        host_capabilities={
            "cfw_connected": False, "csw_connected": False,
            "policy_context": {"deliverable_kind": "visual_ui"},
        },
    )
    snapshot = CSWBehaviorSnapshot(revision=0)
    request = ControlTickRequest(
        control_session_id=identity.scope, tick_id="laya-influence-tick",
        expected_behavior_revision=0, cfw_lifecycle_revision=0,
        observation=RuntimeObservation(
            action_index=1, goal_set=goals, equivalent_retry_count=1),
        goal_set=goals,
        cfw_signals=(FrameworkSignal(
            source="owl", code="owl.approach_failed", severity="warning",
            criterion_id="criterion-visual"),),
        csw_snapshot=snapshot,
    )
    record = RuntimeStateRecord(
        state=BehaviorState(), action_index=0, application_receipts={})
    system_one = SystemOneService(DisabledSystemOneBackend(), mode="shadow")
    frame_factory = create_system_one_frame_factory(system_one)
    controller = BehavioralController()

    class DecisionService:
        def judge(self, _frame):
            return _judgment()

        def decide_behavior(self, context, *, judgment=None):
            raw = controller.decide(context, judgment=None).to_dict()
            if judgment is not None and influenced:
                raw["selected_candidate_id"] = "candidate-laya-influenced"
            return raw

    try:
        preparation = ControlDecisionEngine(
            decision_service=DecisionService(),
            system_one_frame_factory=frame_factory,
        ).prepare(descriptor, record, request)
    finally:
        system_one.close()

    return preparation, record.state


def test_unchanged_selection_keeps_audit_receipt_without_linking_intervention():
    counterfactual = _selection(
        "same-candidate", mode="RECOVER", frameworks={"owl": "reset"},
        preferred_stateworks=())
    actual = _selection(
        "same-candidate", mode="RECOVER", frameworks={"owl": "reset"},
        preferred_stateworks=())
    receipt = compare_semantic_selection(_judgment(), counterfactual, actual)

    assert receipt is not None
    assert receipt.influenced is False
    assert receipt.changed_selection is False
    assert receipt.changed_mode is False
    assert receipt.changed_frameworks == ()
    assert receipt.changed_statework_preference is False

    preparation, prior_state = _prepare_retry_decision(influenced=False)
    assert preparation.system_one_query_opportunity is True
    assert preparation.system_one_opportunity_question_id == "retry_relation"
    assert preparation.system_one_query_attempted is True
    assert preparation.semantic_influence_receipt is not None
    assert preparation.semantic_influence_receipt.influenced is False
    assert preparation.decision.state.active_interventions
    assert not prior_state.active_interventions
    assert all(item.semantic_influence_receipt_id is None
               and item.semantic_decision_refs == ()
               for item in preparation.decision.state.active_interventions)


@pytest.mark.parametrize(
    ("evidence_kind", "expected_outcome"),
    (("criterion_verified", "effective"), ("criterion_rejected", "ineffective")),
)
def test_influenced_new_intervention_links_outcome_and_serialized_events(
        tmp_path: Path, evidence_kind: str, expected_outcome: str):
    preparation, prior_state = _prepare_retry_decision(influenced=True)
    receipt = preparation.semantic_influence_receipt
    assert receipt is not None and receipt.influenced
    assert not prior_state.active_interventions
    linked = tuple(
        item for item in preparation.decision.state.active_interventions
        if item.semantic_influence_receipt_id == receipt.receipt_id)
    assert linked, "a Laya-influenced decision should link its newly created intervention"

    intervention = linked[0]
    assert intervention.semantic_decision_refs == (receipt.provider_decision_id,)
    assert intervention.semantic_influence_receipt_id == receipt.receipt_id
    horizon = int(intervention.evaluation_horizon_actions or
                  intervention.expires_after_actions)
    action_index = intervention.baseline_action_index + horizon
    evidence = HostOutcomeFact(
        evidence_ref=f"{expected_outcome}-criterion-evidence", scope=intervention.scope,
        source="host_validator", kind=evidence_kind, observed_revision=100,
        criterion_id="criterion-visual", action_index=action_index,
    )
    evaluation = InterventionEvaluationFrame(
        scope=intervention.scope, observed_revision=intervention.start_revision + 1,
        action_index=action_index, evaluation_complete=True,
        applied_intervention_ids=(intervention.intervention_id,),
        evidence_refs=(evidence.evidence_ref,),
    )

    class EvidenceAuthority:
        def resolve_outcome_fact(self, evidence_ref: str, *, scope: str):
            assert scope == intervention.scope
            assert evidence_ref == evidence.evidence_ref
            return evidence

    outcome = InterventionEvaluator().assess(
        intervention, evaluation, authority=EvidenceAuthority())
    assert outcome.outcome == expected_outcome
    assert outcome.semantic_decision_refs == (receipt.provider_decision_id,)
    assert outcome.semantic_influence_receipt_id == receipt.receipt_id

    event_service = DigitalPsychologyService(event_db=tmp_path / "events.sqlite3")
    event_context = InterventionEventContext(
        namespace_id="tenant-a", application_id="codex",
        application_instance_id="instance-1", agent_instance_id="agent-1",
        session_id="session-1", task_id="task-1", attempt_id="attempt-1",
        segment_id="segment-0", agent_id="codex", behavioral_subject="task-1",
        goal_contract_hash="a" * 64,
    )
    try:
        directives = tuple(
            item for item in preparation.decision.directives
            if item.intervention_id == intervention.intervention_id)
        assert directives
        application_event = event_service._intervention_events.prepare_application(
            intervention, directives,
            tuple(item.directive_id for item in directives),
            event_context=event_context)
        outcome_event = event_service._intervention_events.prepare_outcome(
            outcome, event_context=event_context)
    finally:
        event_service.close()

    serialized_application = json.loads(json.dumps(application_event))
    serialized_outcome = json.loads(json.dumps(outcome_event))
    assert serialized_application["event_type"] == "behavior_intervention_applied"
    assert serialized_application["payload"]["semantic_decision_refs"] == [
        receipt.provider_decision_id]
    assert serialized_application["payload"]["semantic_influence_receipt_id"] == (
        receipt.receipt_id)
    assert serialized_outcome["event_type"] == "behavior_intervention_assessed"
    assert serialized_outcome["payload"]["behavior_intervention_status"] == expected_outcome
    assert serialized_outcome["payload"]["semantic_decision_refs"] == [
        receipt.provider_decision_id]
    assert serialized_outcome["payload"]["semantic_influence_receipt_id"] == (
        receipt.receipt_id)
