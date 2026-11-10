"""Intake tests for the owner advisory question families.

Covers the three advisory families added in slice A:

- ``decomposition_preference`` (VOI 0.68, dim ``decomposition_fit``,
  owner ``godkiller``)
- ``integration_preference`` (VOI 0.68, dim ``integration_fit``,
  owner ``terraformer``)
- ``test_strategy_preference`` (VOI 0.72, dim ``test_strategy_fit``,
  owner ``testworks``)

Owner map: a StateWork names its owner with the leading token of its
StateWork ID (``testworks-regression`` is owned by ``testworks``), with
the ``domain`` field as a fallback.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from lib.behavior_state import (
    BehaviorState,
    CSWBehaviorSnapshot,
    GoalItemSnapshot,
    GoalSetSnapshot,
    StateWorkOption,
)
from lib.control.engine import ControlDecisionEngine
from lib.control.fast_loop_input import FastLoopInput
from lib.control.policy_ambiguity import PolicyAmbiguity
from lib.control.semantic_query_planner import SemanticQueryPlan, SemanticQueryPlanner
from lib.control.statework_selector import statework_ambiguity
from lib.control_protocol import ControlSessionDescriptor, ControlTickRequest
from lib.fast_loop import BehavioralController
from lib.runtime.contracts import RuntimeIdentity, RuntimeObservation
from lib.runtime.store import RuntimeStateRecord
from lib.system_one_contracts import (
    ActivityFact,
    CriterionState,
    QuestionSpec,
    SemanticState,
    SystemOneFrame,
    SystemOneIdentity,
    canonical_hash,
)


def _ambiguity(*dimensions, gap=0.1):
    return PolicyAmbiguity("alpha", "beta", gap, dimensions)


# --- planner: new families -------------------------------------------------


def test_decomposition_preference_resolves_owner_fit_despite_pair_gap():
    plan = SemanticQueryPlanner().plan(
        RuntimeObservation(action_index=1),
        policy_ambiguity=_ambiguity("decomposition_fit", gap=0.5))

    assert plan.question_id == "decomposition_preference"
    assert plan.value_of_information == pytest.approx(0.68)


def test_integration_preference_resolves_owner_fit_despite_pair_gap():
    plan = SemanticQueryPlanner().plan(
        RuntimeObservation(action_index=1),
        policy_ambiguity=_ambiguity("integration_fit", gap=0.5))

    assert plan.question_id == "integration_preference"
    assert plan.value_of_information == pytest.approx(0.68)


def test_strategy_preference_resolves_owner_fit_despite_pair_gap():
    plan = SemanticQueryPlanner().plan(
        RuntimeObservation(action_index=1),
        policy_ambiguity=_ambiguity("test_strategy_fit", gap=0.5))

    assert plan.question_id == "test_strategy_preference"
    assert plan.value_of_information == pytest.approx(0.72)


def test_pair_gap_skip_blocks_route_and_originals_but_not_advisory():
    planner = SemanticQueryPlanner()
    route = planner.plan(
        RuntimeObservation(action_index=1),
        policy_ambiguity=_ambiguity("statework_fit", gap=0.6))

    assert not route.should_query
    assert "margin is too large" in route.reason

    advisory = planner.plan(
        RuntimeObservation(action_index=1),
        policy_ambiguity=_ambiguity("decomposition_fit", gap=0.6))

    assert advisory.question_id == "decomposition_preference"


def test_statework_fit_takes_priority_over_advisory_dims():
    plan = SemanticQueryPlanner().plan(
        RuntimeObservation(action_index=1),
        policy_ambiguity=_ambiguity("statework_fit", "decomposition_fit", gap=0.0))

    assert plan.question_id == "route_preference"


def test_advisory_priority_follows_fixed_family_order():
    plan = SemanticQueryPlanner().plan(
        RuntimeObservation(action_index=1),
        policy_ambiguity=_ambiguity("integration_fit", "decomposition_fit",
                                    "test_strategy_fit", gap=0.0))

    assert plan.question_id == "test_strategy_preference"


def test_minimum_voi_gate_blocks_every_family():
    planner = SemanticQueryPlanner(minimum_value_of_information=0.73)

    for dimensions in (("route_preference",), ("statework_fit",),
                       ("decomposition_fit",), ("integration_fit",),
                       ("test_strategy_fit",)):
        dimension = dimensions[0]
        question_dims = ("statework_fit",) if dimension == "route_preference" else dimensions
        plan = planner.plan(
            RuntimeObservation(action_index=1),
            policy_ambiguity=_ambiguity(*question_dims, gap=0.0))

        assert not plan.should_query, dimension


def test_new_question_ids_are_plannable_but_unknown_is_not():
    for question_id, voi in (("decomposition_preference", 0.68),
                             ("integration_preference", 0.68),
                             ("test_strategy_preference", 0.72)):
        plan = SemanticQueryPlan(question_id, "bounded reason", voi)

        assert plan.should_query

    with pytest.raises(ValueError, match="unsupported question"):
        SemanticQueryPlan("mystery_preference", "bounded reason", 0.5)


# --- statework_ambiguity: owner dims ----------------------------------------


def _option(statework_id, domain=""):
    return StateWorkOption(statework_id, behavior_tags=("recovery",),
                           purpose="Candidate", domain=domain)


def test_unowned_stateworks_carry_statework_fit_only():
    ambiguity = statework_ambiguity(
        "RECOVER", (_option("repair"), _option("verify")))

    assert ambiguity is not None
    assert ambiguity.unresolved_dimensions == ("statework_fit",)


def test_owner_dims_append_in_fixed_order_after_statework_fit():
    ambiguity = statework_ambiguity(
        "RECOVER", (_option("godkiller-plan"), _option("terraformer-link"),
                    _option("testworks-cases"), _option("repair")))

    assert ambiguity is not None
    assert ambiguity.unresolved_dimensions == (
        "statework_fit", "test_strategy_fit", "decomposition_fit",
        "integration_fit")


def test_single_owner_appends_single_dim():
    ambiguity = statework_ambiguity(
        "RECOVER", (_option("testworks-cases"), _option("repair")))

    assert ambiguity is not None
    assert ambiguity.unresolved_dimensions == (
        "statework_fit", "test_strategy_fit")


def test_active_owner_is_not_an_ambiguous_owner():
    ambiguity = statework_ambiguity(
        "RECOVER", (_option("testworks-cases"), _option("repair"),
                    _option("verify")),
        active=("testworks-cases",))

    assert ambiguity is not None
    assert ambiguity.unresolved_dimensions == ("statework_fit",)


def test_domain_names_owner_as_fallback():
    ambiguity = statework_ambiguity(
        "RECOVER", (_option("plain-id", domain="godkiller"),
                    _option("repair")))

    assert ambiguity is not None
    assert ambiguity.unresolved_dimensions == (
        "statework_fit", "decomposition_fit")


# --- policy_ambiguity: new dims ---------------------------------------------


def test_policy_ambiguity_accepts_all_eight_dimensions():
    ambiguity = PolicyAmbiguity(
        "alpha", "beta", 0.0,
        ("action_relation", "progress_relation", "retry_relation",
         "goal_drift", "statework_fit", "decomposition_fit",
         "integration_fit", "test_strategy_fit"))

    assert len(ambiguity.unresolved_dimensions) == 8


def test_policy_ambiguity_rejects_unknown_dimensions():
    with pytest.raises(ValueError, match="unresolved dimensions"):
        PolicyAmbiguity("alpha", "beta", 0.0, ("mystery_fit",))


# --- fast-loop input: opportunity + advisory families ------------------------


def _fast_loop_input(**kwargs):
    kwargs.setdefault("scope", "scope-a")
    kwargs.setdefault("state", BehaviorState())
    kwargs.setdefault("csw_snapshot", CSWBehaviorSnapshot(revision=0))
    kwargs.setdefault("objective_criteria", ("criterion-1",))
    return FastLoopInput(**kwargs)


def test_opportunity_set_accepts_advisory_ids():
    for question_id in ("decomposition_preference", "integration_preference",
                        "test_strategy_preference"):
        context = _fast_loop_input(
            semantic_opportunity_question_id=question_id)

        assert context.semantic_opportunity_question_id == question_id


def test_opportunity_set_still_rejects_unknown_ids():
    with pytest.raises(ValueError, match="unsupported host semantic query"):
        _fast_loop_input(semantic_opportunity_question_id="mystery-family")


def test_advisory_families_default_empty_and_follow_policy_context():
    assert _fast_loop_input().advisory_semantic_families == ()

    context = _fast_loop_input(policy_context={
        "advisory_semantic_families": ("decomposition", "test_strategy")})

    assert context.advisory_semantic_families == (
        "decomposition", "test_strategy")


def test_advisory_families_reject_unknown_duplicate_and_mismatch():
    with pytest.raises(ValueError, match="advisory"):
        _fast_loop_input(policy_context={
            "advisory_semantic_families": ("mystery-family",)})

    with pytest.raises(ValueError, match="advisory"):
        _fast_loop_input(policy_context={
            "advisory_semantic_families": ("decomposition",
                                           "decomposition")})

    with pytest.raises(ValueError, match="advisory"):
        _fast_loop_input(policy_context={
            "advisory_semantic_families": ("decomposition_preference",)})

    with pytest.raises(ValueError, match="advisory"):
        _fast_loop_input(
            advisory_semantic_families=("integration",),
            policy_context={"advisory_semantic_families": (
                "decomposition",)})


# --- engine: both opportunity allowlists ------------------------------------


class _DecisionService:
    def __init__(self):
        self.controller = BehavioralController()
        self.seen_context = None

    def decide_behavior(self, context, *, judgment=None):
        self.seen_context = context
        return self.controller.decide(context, judgment=judgment).to_dict()

    def judge(self, frame):
        return None


class _PlanFactory:
    """Stub frame factory exposing a fixed query plan and frame."""

    def __init__(self, question_id=None, frame=None):
        self.last_query_plan = (
            SimpleNamespace(question_id=question_id)
            if question_id is not None else None)
        self._frame = frame

    def __call__(self, *args):
        return self._frame


def _identity():
    return RuntimeIdentity(
        namespace_id="tenant-a", application_id="codex",
        application_instance_id="codex-local", agent_instance_id="agent-1",
        session_id="session-1", task_id="task-1", attempt_id="attempt-1",
        workflow_id="workflow-1", subject_ref="repo:example",
        segment_id="segment-0", agent_id="codex", behavioral_subject="task-1")


def _descriptor(identity, goals):
    return ControlSessionDescriptor(
        identity=identity, objective="Render the requested page",
        objective_criteria=("criterion-visual",), initial_goal_set=goals,
        available_frameworks=("owl", "anchor", "fuse", "ward", "flow",
                              "sispis", "dox"),
        cfw_authority_identity={
            "adapter_kind": "cfw-component-runtime", "authority_id": "test-cfw",
            "adapter_protocol_version": "1.2.0",
            "behavior_projection_version": "2.1.0",
            "scope_hash": "a" * 64, "capability_hash": "b" * 64},
        csw_authority_identity={},
        host_capabilities={"cfw_connected": True, "csw_connected": False})


def _system_one_frame(identity, question_id):
    system_identity = SystemOneIdentity(
        namespace_id=identity.namespace_id,
        application_id=identity.application_id,
        application_instance_id=identity.application_instance_id,
        agent_id=identity.agent_id,
        agent_instance_id=identity.agent_instance_id,
        session_id=identity.session_id,
        task_id=identity.task_id,
        attempt_id=identity.attempt_id,
    )
    activity = ActivityFact(
        sequence=1, activity_type="action", source="host_observed",
        host_declared_relation="ambiguous", target_kind="workspace",
        proposed_purpose="advance the requested result",
        affected_criterion_ids=("criterion-visual",),
    )
    state = SemanticState(
        task={"objective": "Render the requested page", "work_mode": "delivery",
              "deliverable_kind": "code_behavior", "task_complexity": "standard",
              "phase": "implementation", "active_segment": identity.segment_id,
              "active_statework": None, "active_flow": None, "non_goals": [],
              "domain_tags": ["software"]},
        situation={"current_state": "IMPLEMENTING", "blockers": [],
                   "actions_since_goal_progress": 0,
                   "actions_since_artifact_change": 0,
                   "verification_actions_since_artifact_change": 0,
                   "recovery_epochs": 0, "repeated_branch_count": 0},
        criteria=(CriterionState("criterion-visual", "Render the requested page",
                                 "functional"),),
        activity=(activity,), workflows=(), convergence=(), candidate=activity,
        legal_routes=("implement", "verify"),
    )
    questions = (QuestionSpec(
        question_id=question_id, family=question_id, question_type="choice",
        instructions="Select the preferred advisory option.",
        criteria=(("alpha", "First option"), ("beta", "Second option"))),)
    state_digest = canonical_hash(state.to_dict())
    question_digest = canonical_hash([item.to_dict() for item in questions])
    policy_hash = "c" * 64
    request_digest = canonical_hash({
        "identity": system_identity.to_dict(), "sequence": 1,
        "state_digest": state_digest,
        "question_bundle_digest": question_digest,
        "policy_hash": policy_hash,
    })
    return SystemOneFrame(
        identity=system_identity, request_id="host-frame-1", sequence=1,
        state_digest=state_digest, question_bundle_digest=question_digest,
        request_digest=request_digest, policy_hash=policy_hash,
        backend_id="host-test", model_id="none", model_revision="none",
        model_content_hash="a" * 64, provider_protocol_hash="b" * 64,
        calibration_bundle_hash="", state=state, questions=questions,
    )


def _prepare(factory):
    identity = _identity()
    goals = GoalSetSnapshot(1, (GoalItemSnapshot("criterion-visual",
                                                 status="ACTIVE"),))
    descriptor = _descriptor(identity, goals)
    state = BehaviorState(revision=1, cognitive_mode="DELIVER",
                          focus_criteria=("criterion-visual",))
    observation = RuntimeObservation(
        action_index=0, action_completed=False, goal_set=goals,
        criterion_status={"criterion-visual": "in_progress"})
    request = ControlTickRequest(
        control_session_id=identity.scope, tick_id="advisory-boundary",
        expected_behavior_revision=1, cfw_lifecycle_revision=-1,
        observation=observation, goal_set=goals, cfw_signals=(),
        csw_snapshot=CSWBehaviorSnapshot(revision=0),
        boundary_kind="pre_model_call", boundary_sequence=1)
    service = _DecisionService()
    preparation = ControlDecisionEngine(
        decision_service=service,
        system_one_frame_factory=factory).prepare(
            descriptor, RuntimeStateRecord(state, action_index=0,
                                           application_receipts={}), request)
    return preparation, service


def test_engine_surfaces_planned_advisory_opportunity_to_both_channels():
    preparation, service = _prepare(
        _PlanFactory(question_id="test_strategy_preference"))

    assert preparation.system_one_query_opportunity is True
    assert (preparation.system_one_opportunity_question_id
            == "test_strategy_preference")
    assert (service.seen_context.semantic_opportunity_question_id
            == "test_strategy_preference")


@pytest.mark.parametrize("question_id", ["decomposition_preference",
                                         "integration_preference"])
def test_engine_surfaces_each_planned_advisory_family(question_id):
    preparation, service = _prepare(_PlanFactory(question_id=question_id))

    assert preparation.system_one_opportunity_question_id == question_id
    assert service.seen_context.semantic_opportunity_question_id == question_id


def test_engine_rejects_unknown_planned_question_on_both_channels():
    preparation, service = _prepare(_PlanFactory(question_id="mystery-family"))

    assert preparation.system_one_query_opportunity is False
    assert preparation.system_one_opportunity_question_id is None
    assert service.seen_context.semantic_opportunity_question_id is None


def test_engine_frame_fallback_only_surfaces_known_advisory_ids():
    identity = _identity()

    known, _ = _prepare(_PlanFactory(
        frame=_system_one_frame(identity, "integration_preference")))
    assert known.system_one_query_opportunity is True
    assert known.system_one_opportunity_question_id == "integration_preference"

    unknown, _ = _prepare(_PlanFactory(
        frame=_system_one_frame(identity, "mystery-family")))
    assert unknown.system_one_query_opportunity is False
    assert unknown.system_one_opportunity_question_id is None
