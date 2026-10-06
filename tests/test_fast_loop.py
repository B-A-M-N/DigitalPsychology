from __future__ import annotations

import json
from pathlib import Path
import time
from types import SimpleNamespace

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from lib.attention import schedule_attention
from lib.behavior_state import (MAX_ACTIVE_INTERVENTIONS, BehaviorIntervention,
                                BehaviorState, CSWBehaviorSnapshot, FrameworkSignal,
                                GoalItemSnapshot, GoalSetSnapshot, InterventionTarget,
                                ModeLease, StateWorkOption)
from lib.fast_loop import BehavioralController, FastLoopInput
from lib.mcp_server import DigitalPsychologyService


def test_repeated_equivalent_work_creates_bounded_recovery_and_legal_statework_preference():
    controller = BehavioralController()
    state = BehaviorState()
    csw = CSWBehaviorSnapshot(
        revision=7,
        eligible_stateworks=(
            StateWorkOption("repair-flow", behavior_tags=("recovery", "repair")),
            StateWorkOption("delivery-flow", behavior_tags=("delivery",)),
        ),
    )

    decision = controller.decide(FastLoopInput(
        scope="tenant/app/agent/session/task/attempt",
        state=state,
        csw_snapshot=csw,
        objective_criteria=("criterion-2",),
        equivalent_retry_count=2,
    ))

    assert decision.expected_revision == 0
    assert decision.state.revision == 1
    assert decision.state.cognitive_mode == "RECOVER"
    assert decision.state.framework_modes["ward"] == "mandatory"
    assert decision.state.framework_modes["fuse"] == "constrained"
    assert decision.preferred_stateworks == ("repair-flow",)
    assert {item.operation for item in decision.directives} >= {
        "set_mode", "set_framework", "set_control", "prefer_statework",
    }
    assert {item.cause for item in decision.state.active_interventions} == {
        "equivalent_retry",
    }
    assert any(target.target == "statework:repair-flow"
               for target in decision.state.active_interventions[0].targets)


def test_cfw_only_retry_and_drift_codes_drive_fast_loop_conditions():
    """OWL/FUSE codes reach the same retry/drift decisions as normalized host codes."""
    from lib.control.detectors import evaluate_conditions
    from lib.control.semantic_assessment import SemanticAssessment

    controller = BehavioralController()
    goals = GoalSetSnapshot(revision=0, items=(
        GoalItemSnapshot("criterion-1"), GoalItemSnapshot("criterion-2")))
    from lib.behavior_state import AttentionLease
    common = dict(
        scope="tenant/app/agent/session/task/attempt", state=BehaviorState(),
        csw_snapshot=CSWBehaviorSnapshot(revision=0),
        objective_criteria=("criterion-1", "criterion-2"), goal_set=goals,
        attention_lease=AttentionLease(
            lease_id="retry-focus", criterion_ids=("criterion-1",),
            issued_revision=0, goal_set_revision=0, start_action=0))
    retry = FastLoopInput(**common, cfw_signals=(FrameworkSignal(
        source="owl", code="owl.approach_failed", criterion_id="criterion-1"),))
    retry_conditions = evaluate_conditions(
        retry, SemanticAssessment(), retry_threshold=2, support_action_threshold=4,
        semantic_threshold=0.8)
    assert retry_conditions.retry is True
    assert "equivalent_retry" in retry_conditions.trigger_codes
    retry_decision = controller.decide(retry)
    assert any(item.cause == "equivalent_retry"
               for item in retry_decision.state.active_interventions)

    drift = FastLoopInput(**common, cfw_signals=(FrameworkSignal(
        source="owl", code="owl.constraint_drift", criterion_id="criterion-1"),))
    drift_conditions = evaluate_conditions(
        drift, SemanticAssessment(), retry_threshold=2, support_action_threshold=4,
        semantic_threshold=0.8)
    assert drift_conditions.drift is True
    assert "goal_drift" in drift_conditions.trigger_codes
    drift_decision = controller.decide(drift)
    assert any(item.cause == "goal_drift"
               for item in drift_decision.state.active_interventions)


def test_mode_lease_blocks_weak_mode_oscillation_but_hard_contradiction_interrupts():
    controller = BehavioralController()
    initial = BehaviorState(
        cognitive_mode="VERIFY",
        mode_lease=ModeLease(
            mode="VERIFY", entered_action=0, minimum_actions=2, max_actions=6,
            cause="artifact_changed", exit_condition="verification_result"),
    )

    weak = controller.decide(FastLoopInput(
        scope="tenant/app/agent/session/task/attempt",
        state=initial, csw_snapshot=CSWBehaviorSnapshot(revision=1),
        objective_criteria=("criterion-1",), actions_since_goal_progress=3,
        active_trigger_codes=("goal_drift",), host_action_index=1,
    ))

    assert weak.state.cognitive_mode == "VERIFY"
    assert weak.state.mode_lease == initial.mode_lease
    assert not any(item.operation == "set_mode" for item in weak.directives)

    interrupted = controller.decide(FastLoopInput(
        scope="tenant/app/agent/session/task/attempt",
        state=weak.state, csw_snapshot=CSWBehaviorSnapshot(revision=2),
        objective_criteria=("criterion-1",),
        active_trigger_codes=("operator_contradiction",), host_action_index=1,
    ))

    assert interrupted.state.cognitive_mode == "REINSPECT"
    assert interrupted.state.mode_lease is not None
    assert interrupted.state.mode_lease.mode == "REINSPECT"


def test_unverified_progress_flag_cannot_assign_intervention_credit():
    controller = BehavioralController()
    initial = BehaviorState()
    first = controller.decide(FastLoopInput(
        scope="tenant/app/agent/session/task/attempt",
        state=initial,
        csw_snapshot=CSWBehaviorSnapshot(revision=1),
        equivalent_retry_count=2,
    ))

    second = controller.decide(FastLoopInput(
        scope="tenant/app/agent/session/task/attempt",
        state=first.state,
        csw_snapshot=CSWBehaviorSnapshot(revision=2),
        progress_made=True,
        artifact_changed=True,
        action_completed=True,
        evidence_refs=("host-evidence-9",),
    ))

    assert second.state.cognitive_mode == "VERIFY"
    assert second.state.framework_modes["owl"] == "normal"
    assert second.state.framework_modes["anchor"] == "passive"
    assert second.state.framework_modes["fuse"] == "normal"
    assert any(item.outcome == "withdrawn"
               and item.attribution == "insufficient"
               for item in second.intervention_outcomes)
    assert any(item.operation == "set_framework" and item.value == "normal"
               for item in second.directives)
    assert not any(item.cause == "equivalent_retry"
                   for item in second.state.active_interventions)


def test_expired_intervention_stops_repeated_autonomous_recovery():
    controller = BehavioralController(intervention_expiry_actions=1)
    first = controller.decide(FastLoopInput(
        scope="tenant/app/agent/session/task/attempt",
        state=BehaviorState(), csw_snapshot=CSWBehaviorSnapshot(revision=1),
        equivalent_retry_count=2,
    ))
    second = controller.decide(FastLoopInput(
        scope="tenant/app/agent/session/task/attempt",
        state=first.state, csw_snapshot=CSWBehaviorSnapshot(revision=2),
        equivalent_retry_count=2, action_completed=True,
    ))

    assert second.state.cognitive_mode == "BLOCKED"
    assert any(item.outcome == "expired" for item in second.intervention_outcomes)
    assert second.state.intervention_history[-1].cause == "equivalent_retry"


def test_regressing_intervention_is_recorded_and_stays_blocked_until_trigger_clears():
    controller = BehavioralController()
    first = controller.decide(FastLoopInput(
        scope="tenant/app/agent/session/task/attempt",
        state=BehaviorState(), csw_snapshot=CSWBehaviorSnapshot(revision=1),
        equivalent_retry_count=2,
    ))
    intervention = first.state.active_interventions[0]
    from lib.behavior_state import InterventionOutcome
    assessment = InterventionOutcome(
        intervention_id=intervention.intervention_id,
        cause=intervention.cause,
        outcome="regressed", observed_revision=2,
        evidence_refs=("regression-evidence",), scope=intervention.scope,
        attribution="supported", goal_progress=False,
    )
    second = controller.decide(FastLoopInput(
        scope="tenant/app/agent/session/task/attempt",
        state=first.state, csw_snapshot=CSWBehaviorSnapshot(revision=2),
        equivalent_retry_count=2, regression_observed=True,
        action_completed=True, intervention_assessments=(assessment,),
    ))
    third = controller.decide(FastLoopInput(
        scope="tenant/app/agent/session/task/attempt",
        state=second.state, csw_snapshot=CSWBehaviorSnapshot(revision=3),
        equivalent_retry_count=2,
    ))

    assert second.state.cognitive_mode == "BLOCKED"
    assert second.intervention_outcomes[0].outcome == "regressed"
    assert second.state.intervention_history[0].evidence_refs == ("regression-evidence",)
    assert third.state.cognitive_mode == "BLOCKED"

    cleared = controller.decide(FastLoopInput(
        scope="tenant/app/agent/session/task/attempt",
        state=third.state, csw_snapshot=CSWBehaviorSnapshot(revision=4),
    ))
    assert cleared.state.cognitive_mode == "DELIVER"
    assert any(item.value == "DELIVER" for item in cleared.directives)


def test_effective_fast_loop_intervention_is_recorded_then_restored():
    from lib.behavior_state import InterventionOutcome

    controller = BehavioralController()
    first = controller.decide(FastLoopInput(
        scope="tenant/app/agent/session/task/attempt",
        state=BehaviorState(), csw_snapshot=CSWBehaviorSnapshot(revision=1),
        objective_criteria=("criterion-2",),
        criterion_status={"criterion-2": "unmet"},
        host_action_index=4, equivalent_retry_count=2,
    ))
    intervention = first.state.active_interventions[0]
    assessment = InterventionOutcome(
        intervention_id=intervention.intervention_id,
        cause=intervention.cause,
        outcome="effective", observed_revision=2,
        evidence_refs=("validator-criterion-2",), scope=intervention.scope,
        attribution="supported", criterion_ids_progressed=("criterion-2",),
        goal_progress=True, artifact_changed=True, repeated_approach=False,
    )

    second = controller.decide(FastLoopInput(
        scope=intervention.scope, state=first.state,
        csw_snapshot=CSWBehaviorSnapshot(revision=2), equivalent_retry_count=2,
        intervention_assessments=(assessment,),
    ))

    assert second.state.cognitive_mode == "DELIVER"
    assert second.state.intervention_history[-1].outcome == "effective"
    assert not any(item.cause == "equivalent_retry"
                   for item in second.state.active_interventions)
    assert any(item.operation == "set_mode" and item.value == "DELIVER"
                   for item in second.directives)
    assert second.state.framework_modes["owl"] == "normal"
    assert second.state.framework_modes["anchor"] == "passive"
    assert second.state.framework_modes["fuse"] == "normal"


def test_failed_intervention_does_not_poison_a_new_approach_epoch():
    from lib.behavior_state import InterventionOutcome
    from lib.condition_identity import identify_condition

    scope = "tenant/app/agent/session/task/attempt"
    snapshot = CSWBehaviorSnapshot(revision=1)
    old_condition = identify_condition(
        cause="equivalent_retry", target_criteria=("criterion-1",),
        goal_set=None, csw_snapshot=snapshot, evidence_refs=(),
        approach_epoch="hypothesis-1")
    history = InterventionOutcome(
        intervention_id="intv-old", cause="equivalent_retry",
        outcome="ineffective", observed_revision=1, scope=scope,
        condition_id=old_condition.condition_id,
        condition_basis_digest=old_condition.condition_basis_digest,
        target_criteria=("criterion-1",))
    state = BehaviorState(revision=1, intervention_history=(history,))

    decision = BehavioralController().decide(FastLoopInput(
        scope=scope, state=state, csw_snapshot=snapshot,
        objective_criteria=("criterion-1",), equivalent_retry_count=2,
        approach_epoch="hypothesis-2"))

    assert decision.state.cognitive_mode == "RECOVER"
    assert decision.state.active_interventions[0].condition_id != old_condition.condition_id


def test_unrelated_goal_progress_does_not_change_condition_identity():
    from lib.condition_identity import identify_condition

    workflow = CSWBehaviorSnapshot(revision=3)
    before = GoalSetSnapshot(4, (
        GoalItemSnapshot("criterion-1", status="ACTIVE"),
        GoalItemSnapshot("criterion-2", status="READY"),
    ))
    after_unrelated_progress = GoalSetSnapshot(5, (
        GoalItemSnapshot("criterion-1", status="ACTIVE"),
        GoalItemSnapshot("criterion-2", status="VERIFIED"),
    ))

    first = identify_condition(
        cause="equivalent_retry", target_criteria=("criterion-1",),
        goal_set=before, csw_snapshot=workflow, evidence_refs=(),
        approach_epoch="hypothesis-1")
    unrelated_change = identify_condition(
        cause="equivalent_retry", target_criteria=("criterion-1",),
        goal_set=after_unrelated_progress, csw_snapshot=workflow, evidence_refs=(),
        approach_epoch="hypothesis-1")

    assert unrelated_change == first


def test_relevant_workflow_evidence_and_hypothesis_changes_reset_condition_identity():
    from lib.condition_identity import identify_condition

    goals = GoalSetSnapshot(4, (
        GoalItemSnapshot("criterion-1", status="ACTIVE"),
        GoalItemSnapshot("criterion-2", status="READY"),
    ))
    workflow = CSWBehaviorSnapshot(
        revision=3, active_stateworks=("repair",),
        workflow_revisions={"repair": 7})

    def identify(*, snapshot=workflow, evidence=("evidence-current",),
                 hypothesis="hypothesis-current"):
        return identify_condition(
            cause="equivalent_retry",
            target_criteria=("criterion-1",),
            goal_set=goals,
            csw_snapshot=snapshot,
            evidence_refs=evidence,
            approach_epoch=hypothesis,
        )

    original = identify()
    changed_workflow = identify(snapshot=CSWBehaviorSnapshot(
        revision=4, active_stateworks=("repair",),
        workflow_revisions={"repair": 8}))
    changed_evidence = identify(evidence=("evidence-revised",))
    changed_hypothesis = identify(hypothesis="hypothesis-revised")

    assert changed_workflow.condition_id != original.condition_id
    assert changed_evidence.condition_id != original.condition_id
    assert changed_hypothesis.condition_id != original.condition_id


def test_cfw_cause_id_is_part_of_the_recovery_epoch_and_intervention():
    from lib.condition_identity import identify_condition
    from lib.behavior_state import AttentionLease

    scope = "tenant/app/agent/session/task/attempt"
    snapshot = CSWBehaviorSnapshot(revision=2)
    goals = GoalSetSnapshot(0, (GoalItemSnapshot("criterion-1"),))
    signal = FrameworkSignal(
        "owl", "owl.approach_failed", criterion_id="criterion-1",
        cause_id="cfw-approach-cause-7")
    decision = BehavioralController().decide(FastLoopInput(
        scope=scope, state=BehaviorState(), csw_snapshot=snapshot,
        objective_criteria=("criterion-1",), goal_set=goals,
        attention_lease=AttentionLease(
            lease_id="retry-focus", criterion_ids=("criterion-1",),
            issued_revision=0, goal_set_revision=0, start_action=0),
        cfw_signals=(signal,)))
    intervention = next(item for item in decision.state.active_interventions
                        if item.cause == "equivalent_retry")
    expected = identify_condition(
        cause="equivalent_retry", target_criteria=("criterion-1",),
        goal_set=goals, csw_snapshot=snapshot, evidence_refs=(),
        upstream_cause_ids=("cfw-approach-cause-7",))

    assert intervention.condition_id == expected.condition_id
    assert intervention.recovery_epoch_id == expected.condition_id


def test_repeated_cfw_emission_for_the_same_cause_keeps_one_recovery_epoch():
    from lib.behavior_state import AttentionLease

    scope = "tenant/app/agent/session/task/attempt"
    snapshot = CSWBehaviorSnapshot(revision=2)
    goals = GoalSetSnapshot(0, (GoalItemSnapshot("criterion-1"),))
    attention = AttentionLease(
        lease_id="retry-focus", criterion_ids=("criterion-1",),
        issued_revision=0, goal_set_revision=0, start_action=0)
    signal = FrameworkSignal(
        "owl", "owl.approach_failed", criterion_id="criterion-1",
        evidence_refs=("cfw:approach-7:failed",),
        cause_id="cfw-approach-cause-7")
    controller = BehavioralController()
    first = controller.decide(FastLoopInput(
        scope=scope, state=BehaviorState(), csw_snapshot=snapshot,
        objective_criteria=("criterion-1",), goal_set=goals,
        attention_lease=attention,
        cfw_signals=(signal,)))
    second = controller.decide(FastLoopInput(
        scope=scope, state=first.state, csw_snapshot=snapshot,
        objective_criteria=("criterion-1",), goal_set=goals,
        attention_lease=attention,
        cfw_signals=(signal,)))

    first_recovery = next(item for item in first.state.active_interventions
                          if item.cause == "equivalent_retry")
    second_recovery = next(item for item in second.state.active_interventions
                           if item.cause == "equivalent_retry")
    assert second_recovery.recovery_epoch_id == first_recovery.recovery_epoch_id
    assert second_recovery.intervention_id == first_recovery.intervention_id


def test_intervention_identity_is_scoped_per_agent_session():
    controller = BehavioralController()
    state = BehaviorState()
    signal = FrameworkSignal("owl", "stale_assumption")
    first = controller.decide(FastLoopInput(
        scope="tenant/app/agent-a/session/task/attempt",
        state=state, csw_snapshot=CSWBehaviorSnapshot(revision=1),
        cfw_signals=(signal,),
    ))
    second = controller.decide(FastLoopInput(
        scope="tenant/app/agent-b/session/task/attempt",
        state=state, csw_snapshot=CSWBehaviorSnapshot(revision=1),
        cfw_signals=(signal,),
    ))

    assert first.decision_id != second.decision_id
    assert first.state.active_interventions[0].intervention_id != \
        second.state.active_interventions[0].intervention_id


def test_state_and_wire_mapping_preserve_intervention_lifecycle():
    controller = BehavioralController()
    decision = controller.decide(FastLoopInput(
        scope="tenant/app/agent/session/task/attempt",
        state=BehaviorState(framework_modes={"ward": "off", "owl": "normal"}),
        csw_snapshot=CSWBehaviorSnapshot(revision=3),
        cfw_signals=(FrameworkSignal("owl", "stale_assumption"),),
    ))

    restored = BehaviorState.from_mapping(decision.state.to_dict())
    assert restored == decision.state
    assert restored.framework_modes["ward"] == "mandatory"
    assert restored.active_interventions == decision.state.active_interventions
    assert restored.intervention_history == decision.state.intervention_history


def test_service_returns_schema_valid_host_decision_without_exposing_agent_tool(tmp_path):
    service = DigitalPsychologyService(event_db=tmp_path / "events.sqlite3")
    try:
        result = service.decide_behavior(FastLoopInput(
            scope="tenant/app/agent/session/task/attempt",
            state=BehaviorState(),
            csw_snapshot=CSWBehaviorSnapshot(revision=0),
            cfw_signals=(FrameworkSignal("owl", "stale_assumption"),),
        ))
    finally:
        service.close()

    schemas = Path(__file__).parents[1] / "lib" / "schemas"
    state_schema = json.loads((schemas / "behavior-state-v1.schema.json").read_text())
    decision_schema = json.loads((schemas / "behavioral-decision-v1.schema.json").read_text())
    registry = Registry().with_resource(
        state_schema["$id"], Resource.from_contents(state_schema))
    Draft202012Validator(decision_schema, registry=registry).validate(result)
    assert result["state"]["cognitive_mode"] == "REINSPECT"


def test_shadow_system_one_result_cannot_change_behavior(tmp_path):
    service = DigitalPsychologyService(event_db=tmp_path / "events.sqlite3")

    class Judgment:
        def answer(self, question_id):
            return SimpleNamespace(
                selected="yes", raw_confidence=0.99,
                calibrated_confidence=None)

    try:
        result = service.decide_behavior(
            FastLoopInput(
                scope="tenant/app/agent/session/task/attempt",
                state=BehaviorState(),
                csw_snapshot=CSWBehaviorSnapshot(revision=0),
            ),
            judgment=SimpleNamespace(status="shadow", judgment=Judgment()),
        )
    finally:
        service.close()

    assert result["state"]["cognitive_mode"] == "DELIVER"


def test_closing_intervention_cannot_restore_focus_over_host_attention_lease():
    controller = BehavioralController()
    goals = GoalSetSnapshot(1, (GoalItemSnapshot("criterion-visual", status="READY"),))
    schedule = schedule_attention(goals, BehaviorState(), action_index=0)
    first = controller.decide(FastLoopInput(
        scope="tenant/app/agent/session/task/attempt",
        state=BehaviorState(), csw_snapshot=CSWBehaviorSnapshot(revision=1),
        objective_criteria=("criterion-visual",), goal_set=goals,
        attention_lease=schedule.lease, attention_last_seen=schedule.last_seen,
        equivalent_retry_count=2,
    ))
    assert first.state.focus_criteria == ("criterion-visual",)
    assert any(target.target == "focus_criteria"
               for item in first.state.active_interventions for target in item.targets)

    refreshed = schedule_attention(goals, first.state, action_index=1)
    cleared = controller.decide(FastLoopInput(
        scope="tenant/app/agent/session/task/attempt",
        state=first.state, csw_snapshot=CSWBehaviorSnapshot(revision=2),
        objective_criteria=("criterion-visual",), goal_set=goals,
        attention_lease=refreshed.lease,
        attention_last_seen=refreshed.last_seen,
        action_completed=True,
    ))

    focus_directives = [item for item in cleared.directives
                        if item.target == "focus_criteria"]
    assert not focus_directives
    assert cleared.state.focus_criteria == ("criterion-visual",)


def test_1000_intervention_soak_keeps_history_and_hot_path_bounded():
    controller = BehavioralController(intervention_expiry_actions=1)
    state = BehaviorState()
    elapsed = []
    saw_outcome = False
    interventions_created = 0
    for index in range(2000):
        prior_ids = {item.intervention_id for item in state.active_interventions}
        started = time.perf_counter()
        decision = controller.decide(FastLoopInput(
            scope="tenant/app/agent/session/task/attempt",
            state=state,
            csw_snapshot=CSWBehaviorSnapshot(revision=index),
            objective_criteria=("criterion-1",),
            equivalent_retry_count=2 if index % 2 == 0 else 0,
            action_completed=True,
            host_action_index=index,
        ))
        elapsed.append(time.perf_counter() - started)
        state = decision.state
        interventions_created += sum(
            item.intervention_id not in prior_ids
            for item in state.active_interventions)
        saw_outcome = saw_outcome or bool(state.intervention_history)
        assert len(state.active_interventions) <= 16
        assert len(state.intervention_history) <= 32
        assert len(state.attention_last_seen) <= 256

    elapsed.sort()
    assert saw_outcome
    assert interventions_created == 1000
    assert elapsed[-1] < 1.0
    assert elapsed[1899] < 0.2


def test_intervention_capacity_preserves_every_live_owner_and_skips_statework():
    controller = BehavioralController()
    original = BehaviorState(active_interventions=tuple(
        BehaviorIntervention(
            intervention_id=f"live-{index}", cause="equivalent_retry",
            expected_effect="Keep the existing causal record intact.",
            start_revision=0, scope="tenant/app/agent/session/task/attempt",
            expires_after_actions=6, actions_elapsed=0,
            targets=(InterventionTarget(
                target="retry_policy", previous_value="normal",
                applied_value="materially_different_only"),),
            expires_at_action=100, target_criteria=("criterion-1",),
            condition_id=f"condition-{index}",
            condition_basis_digest=f"digest-{index}",
        ) for index in range(MAX_ACTIVE_INTERVENTIONS)))
    decision = controller.decide(FastLoopInput(
        scope="tenant/app/agent/session/task/attempt", state=original,
        csw_snapshot=CSWBehaviorSnapshot(
            revision=1, eligible_stateworks=(StateWorkOption("repair-flow"),)),
        objective_criteria=("criterion-1",), equivalent_retry_count=3,
        host_action_index=1,
    ))

    assert decision.reason == "intervention_capacity_exhausted"
    assert tuple(item.intervention_id for item in decision.state.active_interventions) == \
        tuple(item.intervention_id for item in original.active_interventions)
    assert decision.preferred_stateworks == ()
    assert not any(item.operation == "prefer_statework" for item in decision.directives)
    assert all(item.targets == original.active_interventions[index].targets
               for index, item in enumerate(decision.state.active_interventions))


def test_statework_directive_inherits_its_new_intervention_owner_window():
    from types import SimpleNamespace

    from lib.condition_identity import identify_condition
    from lib.control.candidate_generator import CandidateOption
    from lib.control.directive_builder import DirectiveBuilder
    from lib.control.interventions import InterventionStateBuilder
    from lib.control.policy_selector import BehaviorCandidate

    scope = "tenant/app/agent/session/task/attempt"
    context = FastLoopInput(
        scope=scope, state=BehaviorState(),
        csw_snapshot=CSWBehaviorSnapshot(revision=1),
        objective_criteria=("criterion-1",), host_action_index=4,
    )
    after = BehaviorState(cognitive_mode="RECOVER",
                          retry_policy="materially_different_only")
    candidate = BehaviorCandidate.from_state(
        after, focus_criteria=("criterion-1",),
        statework_preferences=("repair-flow",), reason_codes=("deterministic_rule",),
        cause="equivalent_retry", expected_effect="Reassess the failed approach.")
    option = CandidateOption(
        candidate=candidate, role="RECOVER",
        changes=(
            ("mode", "RECOVER", "equivalent_retry", "Reassess the failed approach."),
            ("retry_policy", "materially_different_only", "progress_observed",
             "Preserve the verified progress window."),
        ), deterministic_support=0.86)
    pool = SimpleNamespace(option_for=lambda _candidate_id: option, fallback=lambda: None)
    selection = SimpleNamespace(abstained=False, candidate=candidate)
    conditions = context.csw_snapshot
    builder = DirectiveBuilder(
        intervention_state_builder=InterventionStateBuilder(
            expiry_actions=3, digest=lambda value: __import__("hashlib").sha256(
                repr(value).encode()).hexdigest()),
        intervention_expiry_actions=3,
        digest=lambda value: __import__("hashlib").sha256(
            repr(value).encode()).hexdigest(),
    )

    decision = builder.build(
        context=context, original=context.state, working=context.state,
        next_revision=1, next_focus=("criterion-1",),
        attention_ids=("criterion-1",), interventions=[], outcomes=[], directives=[],
        selection=selection, candidate_pool=pool, current_condition_ids={},
        previously_effective=set(), triggers=set(),
        condition_for=lambda cause, target_criteria: identify_condition(
            cause=cause, target_criteria=target_criteria, goal_set=None,
            csw_snapshot=conditions, evidence_refs=()),
        applies_to_attention=lambda _criteria: True,
    )

    owners = {item.intervention_id: item for item in decision.state.active_interventions}
    statework = next(item for item in decision.directives
                     if item.operation == "prefer_statework")
    owner = owners[statework.intervention_id]
    assert owner.expires_after_actions == 3
    assert statework.expires_after_actions == owner.expires_after_actions
    assert statework.expires_at_action == owner.expires_at_action
    assert statework.intervention_id == owner.intervention_id


def test_full_intervention_target_capacity_skips_statework_preference():
    from types import SimpleNamespace

    from lib.behavior_state import MAX_TARGETS_PER_INTERVENTION
    from lib.condition_identity import identify_condition
    from lib.control.candidate_generator import CandidateOption
    from lib.control.directive_builder import DirectiveBuilder
    from lib.control.interventions import InterventionStateBuilder
    from lib.control.policy_selector import BehaviorCandidate

    scope = "tenant/app/agent/session/task/attempt"
    csw = CSWBehaviorSnapshot(
        revision=1, eligible_stateworks=(StateWorkOption("repair-flow"),))
    context = FastLoopInput(
        scope=scope, state=BehaviorState(), csw_snapshot=csw,
        objective_criteria=("criterion-1",), host_action_index=4)
    candidate = BehaviorCandidate.from_state(
        BehaviorState(cognitive_mode="RECOVER"), focus_criteria=("criterion-1",),
        statework_preferences=("repair-flow",), reason_codes=("deterministic_rule",),
        cause="equivalent_retry", expected_effect="Reassess the failed approach.")
    changes = (
        ("mode", "RECOVER", "equivalent_retry", "Reassess the failed approach."),
        ("framework:owl", "elevated", "equivalent_retry", "Inspect the failed approach."),
        ("framework:anchor", "recovery", "equivalent_retry", "Rebuild the approach."),
        ("framework:fuse", "constrained", "equivalent_retry", "Bound further retries."),
        ("framework:flow", "elevated", "equivalent_retry", "Watch for operational drag."),
        ("framework:sispis", "constrained", "equivalent_retry", "Keep output calibrated."),
        ("framework:dox", "elevated", "equivalent_retry", "Keep the task contract visible."),
        ("verification_intensity", "high", "equivalent_retry", "Verify the next attempt."),
        ("exploration_budget", "low", "equivalent_retry", "Limit the search."),
        ("retry_policy", "materially_different_only", "equivalent_retry",
         "Do not repeat the same approach."),
        ("recovery_level", "1", "equivalent_retry", "Use one recovery step."),
        ("context_strategy", "compact", "equivalent_retry", "Focus on the criterion."),
    )
    assert len(changes) == MAX_TARGETS_PER_INTERVENTION
    option = CandidateOption(candidate=candidate, role="RECOVER", changes=changes,
                             deterministic_support=0.86)
    pool = SimpleNamespace(option_for=lambda _candidate_id: option, fallback=lambda: None)
    selection = SimpleNamespace(abstained=False, candidate=candidate)
    digest = lambda value: __import__("hashlib").sha256(
        repr(value).encode()).hexdigest()
    builder = DirectiveBuilder(
        intervention_state_builder=InterventionStateBuilder(
            expiry_actions=3, digest=digest),
        intervention_expiry_actions=3, digest=digest)

    decision = builder.build(
        context=context, original=context.state, working=context.state,
        next_revision=1, next_focus=("criterion-1",),
        attention_ids=("criterion-1",), interventions=[], outcomes=[], directives=[],
        selection=selection, candidate_pool=pool, current_condition_ids={},
        previously_effective=set(), triggers=set(),
        condition_for=lambda cause, target_criteria: identify_condition(
            cause=cause, target_criteria=target_criteria, goal_set=None,
            csw_snapshot=csw, evidence_refs=()),
        applies_to_attention=lambda _criteria: True,
    )

    assert decision.reason == "intervention_target_capacity_exhausted"
    assert len(decision.state.active_interventions) == 1
    owner = decision.state.active_interventions[0]
    assert len(owner.targets) == MAX_TARGETS_PER_INTERVENTION
    assert not any(target.target.startswith("statework:") for target in owner.targets)
    assert not decision.preferred_stateworks
    assert not any(item.operation == "prefer_statework" for item in decision.directives)
