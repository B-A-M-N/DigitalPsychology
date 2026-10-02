from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from lib.attention import schedule_attention
from lib.behavior_state import (BehaviorState, CSWBehaviorSnapshot, FrameworkSignal,
                                GoalItemSnapshot, GoalSetSnapshot, StateWorkOption)
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
