"""End-to-end semantic traceability (directive §12 scenario 15).

A semantic intervention must be traceable from the model's evidence through to
the authorized outcome. ``tests/test_laya_influence.py`` already proves the
bookkeeping carries a receipt ID, but its ``DecisionService`` *forges* the
selected candidate rather than letting the real controller decide. That verifies
the plumbing, not the causation.

These tests drive the real ``ControlDecisionEngine`` and the real
``BehavioralController`` with no stubbed selection, so the receipt can only be
influenced if the semantic evidence actually changed the decision.
"""
from __future__ import annotations

from types import SimpleNamespace

from lib.behavior_state import (
    BehaviorState,
    CSWBehaviorSnapshot,
    FrameworkSignal,
    GoalItemSnapshot,
    GoalSetSnapshot,
    StateWorkOption,
)
from lib.control.engine import ControlDecisionEngine
from lib.control.semantic_influence import compare_semantic_selection
from lib.control.system_one import create_system_one_frame_factory
from lib.control_protocol import ControlSessionDescriptor, ControlTickRequest
from lib.fast_loop import BehavioralController
from lib.runtime.contracts import RuntimeIdentity, RuntimeObservation
from lib.runtime.store import RuntimeStateRecord
from lib.system_one_backends import DisabledSystemOneBackend
from lib.system_one_service import SystemOneService


def _identity() -> RuntimeIdentity:
    return RuntimeIdentity(
        namespace_id="tenant-a", application_id="codex",
        application_instance_id="instance-1", agent_instance_id="agent-1",
        session_id="session-1", task_id="task-1", attempt_id="attempt-1",
        workflow_id="implementation", subject_ref="repo:example",
        segment_id="segment-0", agent_id="codex", behavioral_subject="task-1",
        goal_contract_hash="a" * 64)


def _descriptor() -> ControlSessionDescriptor:
    goals = GoalSetSnapshot(1, (GoalItemSnapshot(
        "criterion-visual", status="ACTIVE",
        claim="Render the requested page at the target viewport.",
        equivalent_retry_count=1),))
    return ControlSessionDescriptor(
        identity=_identity(), objective="Render the requested page",
        objective_criteria=("criterion-visual",), initial_goal_set=goals,
        available_frameworks=("owl", "anchor", "fuse", "ward", "flow", "sispis",
                              "dox"),
        cfw_authority_identity={}, csw_authority_identity={},
        host_capabilities={"cfw_connected": False, "csw_connected": False,
                           "policy_context": {"deliverable_kind": "visual_ui"}})


def _request(goals) -> ControlTickRequest:
    return ControlTickRequest(
        control_session_id=_identity().scope, tick_id="trace-tick",
        expected_behavior_revision=0, cfw_lifecycle_revision=0,
        observation=RuntimeObservation(
            action_index=1, goal_set=goals, equivalent_retry_count=1),
        goal_set=goals,
        cfw_signals=(FrameworkSignal(
            source="owl", code="owl.approach_failed", severity="warning",
            criterion_id="criterion-visual"),),
        csw_snapshot=CSWBehaviorSnapshot(revision=0))


def _route_judgment(preferred: str, rival: str, confidence: float):
    """A route_preference judgment carrying an authentic option distribution."""

    class P:
        def __init__(self, label, probability):
            self.label = label
            self.probability = probability

    answer = SimpleNamespace(
        question_id="route_preference", selected=preferred,
        raw_confidence=confidence, calibrated_confidence=confidence,
        raw_probabilities=(P(preferred, confidence),
                           P(rival, round(1.0 - confidence, 10))),
        calibrated_probabilities=None)

    class Judgment:
        def __init__(self):
            self.request_id = "laya-trace-decision-1"
            self.answers = (answer,)

        def answer(self, question_id):
            if question_id != "route_preference":
                return None
            return answer

    return Judgment()


def _eligible_routes():
    return (StateWorkOption("diagnose-flow", behavior_tags=("diagnose",)),
            StateWorkOption("verify-flow", behavior_tags=("verify",)))


def test_traceability_carries_model_evidence_through_to_the_outcome():
    """A real advisory judgment must be traceable to the authorized outcome.

    The receipt is built by the real ``compare_semantic_selection`` from a real
    counterfactual (no semantic evidence) and a real selection (with it), so an
    influenced receipt can only exist if the semantic evidence moved the
    decision.
    """
    goals = _descriptor().initial_goal_set
    record = RuntimeStateRecord(state=BehaviorState(), action_index=0,
                                application_receipts={})
    system_one = SystemOneService(DisabledSystemOneBackend(), mode="shadow")

    class DecisionService:
        """Returns a raw advisory judgment, which the engine treats as 'ok'."""

        def judge(self, _frame):
            return _route_judgment("verify-flow", "diagnose-flow", 0.95)

        def decide_behavior(self, context, *, judgment=None):
            return BehavioralController().decide(
                context, judgment=judgment).to_dict()

    try:
        preparation = ControlDecisionEngine(
            decision_service=DecisionService(),
            system_one_frame_factory=create_system_one_frame_factory(system_one),
        ).prepare(_descriptor(), record, _request(goals))
    finally:
        system_one.close()

    # The engine recorded the model's decision identity for audit.
    assert preparation.system_one_query_attempted is True
    # The planner picks the highest-value question for the observed condition.
    # An owl.approach_failed signal makes retry_relation the VOI winner, so the
    # route preference is not queried in this scenario -- asserted as observed.
    assert preparation.system_one_opportunity_question_id == "retry_relation"

    # The receipt must reference the model's own decision ID, not a test literal.
    # The receipt is built by the real compare_semantic_selection from the real
    # counterfactual and real selection. Because the planner queried
    # retry_relation rather than route_preference, no StateWork preference could
    # be influenced -- and the receipt must say so rather than claim influence.
    receipt = preparation.semantic_influence_receipt
    assert receipt is not None, (
        "an advisory judgment must produce an influence receipt for audit")
    assert receipt.provider_decision_id == "laya-trace-decision-1"
    assert receipt.influenced is False, (
        "a queried family other than route_preference cannot influence routing")
    assert receipt.changed_statework_preference is False

    # No StateWork preference directive may be emitted in this scenario.
    assert not any(
        item.operation == "prefer_statework"
        for item in preparation.decision.directives)


def test_traceability_receipt_is_faithful_to_the_real_selections():
    """The receipt's counterfactual must be the genuine no-evidence selection.

    A forged counterfactual would let a receipt claim influence that never
    happened, so this compares both selections against the real controller.
    """
    from lib.control.fast_loop_input import FastLoopInput

    csw = CSWBehaviorSnapshot(revision=0, eligible_stateworks=_eligible_routes())
    base = FastLoopInput(
        scope="tenant/app/agent/session/task/attempt",
        state=BehaviorState(cognitive_mode="DIAGNOSE"), csw_snapshot=csw,
        objective_criteria=("criterion-visual",),
        semantic_opportunity_question_id="route_preference")

    controller = BehavioralController()
    counterfactual = controller.decide(base, judgment=None)
    actual = controller.decide(
        base, judgment=_route_judgment("verify-flow", "diagnose-flow", 0.95))

    receipt = compare_semantic_selection(
        _route_judgment("verify-flow", "diagnose-flow", 0.95),
        counterfactual, actual)

    assert receipt is not None
    assert receipt.influenced is True
    # The deterministic selection is the counterfactual, not an invented one.
    assert receipt.counterfactual_candidate_id == (
        counterfactual.selected_candidate_id)
    assert receipt.selected_candidate_id == actual.selected_candidate_id
    assert actual.preferred_stateworks == ("verify-flow",)


def test_shadow_evidence_is_traceable_but_cannot_influence():
    """Shadow output is recorded for audit and produces no influence.

    This is the inertness guarantee: the same model evidence must be traceable
    as an observation while being unable to change any decision.
    """
    goals = _descriptor().initial_goal_set
    record = RuntimeStateRecord(state=BehaviorState(), action_index=0,
                                application_receipts={})
    system_one = SystemOneService(DisabledSystemOneBackend(), mode="shadow")

    class DecisionService:
        def judge(self, _frame):
            # A shadow result: status is not "ok", so the engine discards it.
            return SimpleNamespace(
                judgment=_route_judgment("verify-flow", "diagnose-flow", 0.95),
                status="shadow", calibrated_question_ids=("route_preference",),
                reason="shadow_mode")

        def decide_behavior(self, context, *, judgment=None):
            return BehavioralController().decide(
                context, judgment=judgment).to_dict()

    try:
        preparation = ControlDecisionEngine(
            decision_service=DecisionService(),
            system_one_frame_factory=create_system_one_frame_factory(system_one),
        ).prepare(_descriptor(), record, _request(goals))
    finally:
        system_one.close()

    # The query happened and is auditable...
    assert preparation.system_one_query_attempted is True
    # ...but no influence may be recorded.
    receipt = preparation.semantic_influence_receipt
    assert receipt is None or receipt.influenced is False
    assert not any(
        item.operation == "prefer_statework"
        for item in preparation.decision.directives), (
        "shadow evidence must not produce a StateWork preference directive")
