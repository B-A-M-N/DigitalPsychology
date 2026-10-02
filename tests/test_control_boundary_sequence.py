from __future__ import annotations

import pytest

from lib.behavior_state import (BehaviorIntervention, BehaviorState,
                                CSWBehaviorSnapshot, GoalItemSnapshot,
                                GoalSetSnapshot, InterventionTarget)
from lib.control.engine import ControlDecisionEngine
from lib.control_protocol import ControlSessionDescriptor, ControlTickRequest
from lib.fast_loop import BehavioralController
from lib.runtime.contracts import RuntimeIdentity, RuntimeObservation
from lib.runtime.store import BehaviorStateStore
from lib.runtime.store import RuntimeStateRecord


class _DecisionService:
    def __init__(self):
        self.controller = BehavioralController()

    def decide_behavior(self, context, *, judgment=None):
        return self.controller.decide(context, judgment=judgment).to_dict()


def test_aborted_control_boundaries_still_consume_their_sequence(tmp_path):
    store = BehaviorStateStore(tmp_path / "behavior.sqlite3")
    scope = "scope-a"
    store.register_control_session(scope, {})

    store.prepare_control_tick(
        scope, tick_id="tick-8", request_digest="digest-8",
        basis={"expected_behavior_revision": 0,
               "expected_action_index": -1,
               "boundary_sequence": 8},
        decision={"decision_id": "decision-8"},
    )
    assert store.latest_boundary_sequence(scope) == 8
    store.abort_control_tick(scope, "tick-8", decision_id="decision-8",
                             detail={"reason": "host compensation completed"})

    with pytest.raises(RuntimeError, match="boundary sequence is stale"):
        store.prepare_control_tick(
            scope, tick_id="tick-7", request_digest="digest-7",
            basis={"expected_behavior_revision": 0,
                   "expected_action_index": -1,
                   "boundary_sequence": 7},
            decision={"decision_id": "decision-7"},
        )


def test_non_action_control_boundary_does_not_age_an_intervention():
    identity = RuntimeIdentity(
        namespace_id="tenant-a", application_id="codex",
        application_instance_id="codex-local", agent_instance_id="agent-1",
        session_id="session-1", task_id="task-1", attempt_id="attempt-1",
        workflow_id="workflow-1", subject_ref="repo:example",
        segment_id="segment-0", agent_id="codex", behavioral_subject="task-1")
    goals = GoalSetSnapshot(1, (GoalItemSnapshot(
        "criterion-visual", status="ACTIVE", equivalent_retry_count=2),))
    intervention = BehaviorIntervention(
        intervention_id="intervention-1", cause="equivalent_retry",
        expected_effect="Use a materially different implementation approach.",
        start_revision=0, scope=identity.scope, expires_after_actions=2,
        actions_elapsed=0,
        targets=(InterventionTarget("mode", "DELIVER", "RECOVER"),),
        target_criteria=("criterion-visual",),
        baseline_criterion_status={"criterion-visual": "unmet"},
        baseline_action_index=0, evaluation_horizon_actions=2)
    state = BehaviorState(
        revision=1, cognitive_mode="RECOVER",
        focus_criteria=("criterion-visual",),
        active_interventions=(intervention,))
    descriptor = ControlSessionDescriptor(
        identity=identity, objective="Render the requested page",
        objective_criteria=("criterion-visual",), initial_goal_set=goals,
        available_frameworks=("owl", "anchor", "fuse", "ward", "flow", "sispis", "dox"),
        csw_authority_identity={"authority": "test"}, host_capabilities={})
    observation = RuntimeObservation(
        action_index=0, action_completed=False, goal_set=goals,
        active_trigger_codes=("equivalent_retry",), equivalent_retry_count=2,
        criterion_status={"criterion-visual": "in_progress"})
    request = ControlTickRequest(
        control_session_id=identity.scope, tick_id="pre-model-boundary",
        expected_behavior_revision=1, cfw_lifecycle_revision=-1,
        observation=observation, goal_set=goals, cfw_signals=(),
        csw_snapshot=CSWBehaviorSnapshot(revision=0),
        boundary_kind="pre_model_call", boundary_sequence=1)

    decision = ControlDecisionEngine(decision_service=_DecisionService()).prepare(
            descriptor,
            RuntimeStateRecord(state, action_index=0,
                               application_receipts={"intervention-1": ("dir-1",)}),
            request).decision

    active = {item.intervention_id: item
              for item in decision.state.active_interventions}
    assert active["intervention-1"].actions_elapsed == 0
