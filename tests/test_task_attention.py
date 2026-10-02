from __future__ import annotations

from lib.attention import schedule_attention
from lib.behavior_state import (BehaviorState, CSWBehaviorSnapshot,
                                GoalItemSnapshot, GoalSetSnapshot)
from lib.fast_loop import BehavioralController, FastLoopInput


def _goal_set(*, retry_first: bool = False) -> GoalSetSnapshot:
    return GoalSetSnapshot(
        revision=7,
        items=tuple(GoalItemSnapshot(
            criterion_id=f"criterion-{index}",
            priority=100 if index == 1 else 0,
            status="READY",
            actions_since_progress=0,
            equivalent_retry_count=2 if retry_first and index == 1 else 0,
        ) for index in range(1, 13)),
    )


def test_twelve_required_criteria_receive_fair_bounded_attention():
    goal_set = _goal_set(retry_first=True)
    criteria = tuple(item.criterion_id for item in goal_set.items)
    statuses = {criterion_id: "unmet" for criterion_id in criteria}
    state = BehaviorState()
    controller = BehavioralController()
    seen: dict[str, int] = {}
    focus_at_fourth_action: tuple[str, ...] = ()

    for action_index in range(1, 13):
        schedule = schedule_attention(goal_set, state, action_index=action_index)
        assert schedule.lease is not None
        decision = controller.decide(FastLoopInput(
            scope="dp-task-set-fairness",
            state=state,
            csw_snapshot=CSWBehaviorSnapshot(revision=action_index),
            objective_criteria=criteria,
            goal_set=goal_set,
            attention_lease=schedule.lease,
            attention_last_seen=schedule.last_seen,
            criterion_status=statuses,
            # Task-global counters are deliberately hostile. The complete
            # task-set facts must keep them from contaminating other criteria.
            actions_since_goal_progress=999,
            equivalent_retry_count=999,
            host_action_index=action_index,
            action_completed=True,
        ))
        state = decision.state
        assert state.cognitive_mode != "BLOCKED"
        assert len(state.focus_criteria) <= 3
        for criterion_id in state.focus_criteria:
            seen.setdefault(criterion_id, action_index)
        if action_index == 1:
            assert state.cognitive_mode == "RECOVER"
            assert "criterion-1" in state.focus_criteria
        if action_index == 4:
            focus_at_fourth_action = state.focus_criteria

    assert set(seen) == set(criteria)
    assert max(seen.values()) <= 10
    assert "criterion-1" not in focus_at_fourth_action
    assert state.intervention_history
    assert state.intervention_history[0].cause == "equivalent_retry"


def test_attention_lease_rotates_when_host_marks_focused_item_verified():
    first = GoalSetSnapshot(revision=1, items=tuple(
        GoalItemSnapshot(f"criterion-{i}") for i in range(1, 7)))
    schedule = schedule_attention(first, BehaviorState(), action_index=1)
    assert schedule.lease is not None

    next_set = GoalSetSnapshot(revision=2, items=(
        GoalItemSnapshot("criterion-1", status="VERIFIED"),
        *(GoalItemSnapshot(f"criterion-{i}") for i in range(2, 7)),
    ))
    state = BehaviorState(
        revision=1,
        focus_criteria=schedule.lease.criterion_ids,
        attention_lease=schedule.lease,
        attention_last_seen=schedule.last_seen,
    )
    refreshed = schedule_attention(next_set, state, action_index=2)
    assert refreshed.lease is not None
    assert "criterion-1" not in refreshed.lease.criterion_ids
    assert refreshed.lease.goal_set_revision == 2


def test_all_256_required_criteria_receive_attention_within_fairness_bound():
    from dataclasses import replace
    from lib.attention import (MAX_FAIRNESS_AGE_ACTIONS,
                               fairness_bound_actions)

    goal_set = GoalSetSnapshot(1, tuple(
        GoalItemSnapshot(f"criterion-{index:03d}") for index in range(256)))
    state = BehaviorState()
    seen: dict[str, int] = {}
    bound = fairness_bound_actions(256)
    assert bound == MAX_FAIRNESS_AGE_ACTIONS
    for action_index in range(1, bound + 1):
        schedule = schedule_attention(goal_set, state, action_index=action_index)
        assert schedule.lease is not None
        for criterion_id in schedule.lease.criterion_ids:
            seen.setdefault(criterion_id, action_index)
        state = replace(
            state, revision=state.revision + 1,
            focus_criteria=schedule.lease.criterion_ids,
            attention_lease=schedule.lease,
            attention_last_seen=schedule.last_seen,
        )

    assert len(seen) == 256
    assert max(seen.values()) <= fairness_bound_actions(len(goal_set.runnable_items))


def test_fairness_bound_tracks_the_current_runnable_set():
    from lib.attention import fairness_bound_actions

    assert fairness_bound_actions(0) == 0
    assert fairness_bound_actions(1) == 3
    assert fairness_bound_actions(4) == 6
    assert fairness_bound_actions(20) == 21
    assert fairness_bound_actions(256) == 258


def test_goal_set_rejects_unknown_dependencies_and_criterion_overflow():
    try:
        GoalSetSnapshot(1, (GoalItemSnapshot("criterion-1", dependencies=("missing",)),))
    except ValueError as exc:
        assert "unknown dependency" in str(exc)
    else:
        raise AssertionError("unknown goal dependency was accepted")

    try:
        GoalSetSnapshot(1, tuple(GoalItemSnapshot(f"criterion-{i}")
                                 for i in range(257)))
    except ValueError as exc:
        assert "256" in str(exc)
    else:
        raise AssertionError("oversized goal set was accepted")
