"""Deterministic, starvation-bounded attention scheduling for DP runtime sessions."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Mapping

from .behavior_state import AttentionLease, BehaviorState, GoalItemSnapshot, GoalSetSnapshot


ATTENTION_WINDOW_SIZE = 3
ATTENTION_LEASE_ACTIONS = 3
MAX_GOAL_ITEMS = 256


def fairness_bound_actions(runnable_count: int) -> int:
    """Return the round-robin service bound for the current runnable set."""
    if type(runnable_count) is not int or not 0 <= runnable_count <= MAX_GOAL_ITEMS:
        raise ValueError("runnable criterion count is out of bounds")
    if runnable_count == 0:
        return 0
    return ((runnable_count + ATTENTION_WINDOW_SIZE - 1)
            // ATTENTION_WINDOW_SIZE) * ATTENTION_LEASE_ACTIONS


def projected_starvation_risk(goal_set: GoalSetSnapshot, *,
                              candidate_focus: tuple[str, ...],
                              last_seen: Mapping[str, int],
                              action_index: int,
                              lease_actions: int) -> float | None:
    """Measure candidate lease proximity to the real round-robin age bound.

    This is a normalized fairness pressure score, not a probability. A large
    runnable set does not raise it by itself; only the ages of unattended
    criteria and the proposed lease duration affect the result.
    """
    if (type(action_index) is not int or action_index < 0
            or type(lease_actions) is not int or not 0 <= lease_actions <= 8):
        raise ValueError("projected starvation inputs are outside their bounds")
    runnable = goal_set.runnable_items
    if not runnable:
        return None
    runnable_ids = {item.criterion_id for item in runnable}
    focus = set(candidate_focus)
    if not focus.issubset(runnable_ids):
        raise ValueError("candidate attention focus escapes runnable criteria")
    fairness_bound = fairness_bound_actions(len(runnable))
    if fairness_bound <= 0:
        return None
    projected_max_age = max((
        action_index - last_seen.get(item.criterion_id, action_index)
        + lease_actions
        for item in runnable if item.criterion_id not in focus
    ), default=0)
    return min(1.0, max(0.0, projected_max_age / fairness_bound))


# Compatibility ceiling for callers auditing the largest supported task set.
MAX_FAIRNESS_AGE_ACTIONS = fairness_bound_actions(MAX_GOAL_ITEMS)


@dataclass(frozen=True)
class AttentionSchedule:
    lease: AttentionLease | None
    last_seen: Mapping[str, int]
    runnable_count: int
    oldest_starved_criterion: str | None
    fairness_bound_actions: int
    starvation_prevented: tuple[str, ...] = ()


def schedule_attention(goal_set: GoalSetSnapshot, state: BehaviorState, *,
                       action_index: int) -> AttentionSchedule:
    """Return a bounded lease while aging every runnable required criterion.

    Oldest last-attention time is the primary sort key. Priority and unblock
    value only break ties, so priority cannot starve lower-ranked required work.
    The lease is reused only while its authoritative task-set revision and
    selected runnable items remain unchanged.
    """
    runnable = goal_set.runnable_items
    prior = state.attention_lease
    runnable_by_id = {item.criterion_id: item for item in runnable}
    runnable_by_id_snapshot = runnable_by_id
    fairness_bound = fairness_bound_actions(len(runnable))
    last_seen = dict(state.attention_last_seen)
    for item in runnable:
        # New host criteria start aging at their first observed revision.
        last_seen.setdefault(item.criterion_id, max(0, action_index - 1))
    projected_starvation: tuple[str, ...] = ()
    prior_focus_is_runnable = bool(
        prior is not None and prior.goal_set_revision <= goal_set.revision
        and set(prior.criterion_ids).issubset(runnable_by_id_snapshot))
    prior_is_current = bool(
        prior_focus_is_runnable and prior is not None
        and prior.goal_set_revision == goal_set.revision)
    if (prior_is_current and prior is not None
            and action_index - prior.start_action < prior.max_actions):
        ages = _ages(runnable, last_seen, action_index)
        elapsed = action_index - prior.start_action
        remaining_lease = max(0, prior.max_actions - elapsed)
        projected_starvation = tuple(sorted(
            item.criterion_id for item in runnable
            if item.criterion_id not in prior.criterion_ids
            and ages[item.criterion_id] + remaining_lease > fairness_bound))
        if not projected_starvation:
            oldest = _oldest(runnable, ages)
            return AttentionSchedule(prior, last_seen, len(runnable), oldest,
                                     fairness_bound)

    if not runnable:
        return AttentionSchedule(None, last_seen, 0, None, 0)

    # Productive focus can continue briefly when the host reports a new
    # material-progress revision.  Micro-progress and file changes alone do
    # not qualify.  Every extension renews the material-progress baseline.
    if prior_focus_is_runnable and prior is not None:
        elapsed = action_index - prior.start_action
        continued = max((prior.continuous_attention_leases.get(item, 0)
                         for item in prior.criterion_ids), default=0)
        made_material_progress = any(
            runnable_by_id_snapshot[item].material_progress_revision
            > prior.material_progress_revisions.get(item, 0)
            for item in prior.criterion_ids)
        if (elapsed >= prior.max_actions and made_material_progress
                and continued < 2):
            ages = _ages(runnable, last_seen, action_index)
            outside = tuple(item for item in runnable
                            if item.criterion_id not in prior.criterion_ids)
            projected_ages = {
                item.criterion_id: ages[item.criterion_id] + ATTENTION_LEASE_ACTIONS
                for item in outside
            }
            fairness_violations = tuple(sorted(
                criterion_id for criterion_id, age in projected_ages.items()
                if age > fairness_bound))
            fairness_approaching = tuple(sorted(
                criterion_id for criterion_id, age in projected_ages.items()
                if age >= fairness_bound))
            may_extend = not fairness_violations and (
                continued == 0 or not fairness_approaching)
            if may_extend:
                for criterion_id in prior.criterion_ids:
                    last_seen[criterion_id] = action_index
                lease_id = "attn-" + hashlib.sha256(json.dumps({
                    "goal_set_revision": goal_set.revision,
                    "action_index": action_index,
                    "state_revision": state.revision,
                    "criteria": prior.criterion_ids,
                    "continuous": continued + 1,
                    "progress": {
                        item: runnable_by_id_snapshot[item].material_progress_revision
                        for item in prior.criterion_ids
                    },
                }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:24]
                lease = AttentionLease(
                    lease_id=lease_id,
                    criterion_ids=prior.criterion_ids,
                    issued_revision=state.revision + 1,
                    goal_set_revision=goal_set.revision,
                    start_action=action_index,
                    max_actions=ATTENTION_LEASE_ACTIONS,
                    reason="material_progress_extension",
                    material_progress_revisions={
                        item: runnable_by_id_snapshot[item].material_progress_revision
                        for item in prior.criterion_ids
                    },
                    continuous_attention_leases={
                        item: prior.continuous_attention_leases.get(item, 0) + 1
                        for item in prior.criterion_ids
                    },
                )
                ages = _ages(runnable, last_seen, action_index)
                return AttentionSchedule(lease, last_seen, len(runnable),
                                         _oldest(runnable, ages), fairness_bound)
            projected_starvation = (fairness_violations or fairness_approaching)

    dependents: dict[str, int] = {item.criterion_id: 0 for item in runnable}
    for item in goal_set.items:
        if item.status not in {"VERIFIED", "BLOCKED", "DEFERRED_BY_OPERATOR"}:
            for dependency in item.dependencies:
                if dependency in dependents:
                    dependents[dependency] += 1

    ages = _ages(runnable, last_seen, action_index)
    ordered = sorted(
        runnable,
        key=lambda item: (
            last_seen.get(item.criterion_id, -1),
            -item.priority,
            -dependents[item.criterion_id],
            item.equivalent_retry_count,
            item.criterion_id,
        ),
    )
    selected = tuple(item.criterion_id for item in ordered[:ATTENTION_WINDOW_SIZE])
    lease_id = "attn-" + hashlib.sha256(json.dumps({
        "goal_set_revision": goal_set.revision,
        "action_index": action_index,
        "state_revision": state.revision,
        "criteria": selected,
    }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:24]
    lease = AttentionLease(
        lease_id=lease_id,
        criterion_ids=selected,
        issued_revision=state.revision + 1,
        goal_set_revision=goal_set.revision,
        start_action=action_index,
        max_actions=ATTENTION_LEASE_ACTIONS,
        material_progress_revisions={
            item: runnable_by_id_snapshot[item].material_progress_revision
            for item in selected
        },
        continuous_attention_leases={item: 0 for item in selected},
    )
    for criterion_id in selected:
        last_seen[criterion_id] = action_index
    ages = _ages(runnable, last_seen, action_index)
    return AttentionSchedule(lease, last_seen, len(runnable), _oldest(runnable, ages),
                             fairness_bound, projected_starvation)


def _ages(items: tuple[GoalItemSnapshot, ...], last_seen: Mapping[str, int],
          action_index: int) -> dict[str, int]:
    return {item.criterion_id: action_index - last_seen.get(item.criterion_id, -1)
            for item in items}


def _oldest(items: tuple[GoalItemSnapshot, ...], ages: Mapping[str, int]) -> str | None:
    if not items:
        return None
    return min(items, key=lambda item: (-ages[item.criterion_id], item.criterion_id)).criterion_id


__all__ = ["ATTENTION_LEASE_ACTIONS", "ATTENTION_WINDOW_SIZE",
           "MAX_FAIRNESS_AGE_ACTIONS", "AttentionSchedule",
           "fairness_bound_actions", "projected_starvation_risk",
           "schedule_attention"]
