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
    fairness_bound = fairness_bound_actions(len(runnable))
    last_seen = dict(state.attention_last_seen)
    for item in runnable:
        # New host criteria start aging at their first observed revision.
        last_seen.setdefault(item.criterion_id, max(0, action_index - 1))
    projected_starvation: tuple[str, ...] = ()
    if (prior is not None
            and prior.goal_set_revision == goal_set.revision
            and action_index - prior.start_action < prior.max_actions
            and set(prior.criterion_ids).issubset(runnable_by_id)):
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
           "fairness_bound_actions", "schedule_attention"]
