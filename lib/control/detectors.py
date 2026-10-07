"""Host-fact behavioral condition detection, separate from policy selection."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..telemetry.material_progress import normalized_behavior_codes


@dataclass(frozen=True)
class DetectedConditions:
    attention_ids: tuple[str, ...]
    focused_progress: bool
    runnable_count: int
    task_complete: bool
    contradiction: bool
    retry: bool
    drift: bool
    pressure: bool
    trigger_codes: tuple[str, ...]


def evaluate_conditions(context: Any, semantic: Any, *, retry_threshold: int,
                        support_action_threshold: int,
                        semantic_threshold: float) -> DetectedConditions:
    """Derive bounded triggers from host facts and calibrated semantic advice."""
    causes = context.behavioral_causes
    signal_codes = tuple(code for cause in causes for code in cause.codes)
    codes = set(normalized_behavior_codes(context.active_trigger_codes, signal_codes))
    attention_ids = (context.attention_lease.criterion_ids
                     if context.attention_lease is not None
                     else context.state.focus_criteria)
    goal_items = ({item.criterion_id: item for item in context.goal_set.items}
                  if context.goal_set is not None else {})
    focused_items = [goal_items[item] for item in attention_ids if item in goal_items]
    multi_item_task = (context.goal_set is not None
                       and sum(item.required for item in context.goal_set.items) > 1)
    if context.goal_set is not None:
        focused_retry_count = max(
            (item.equivalent_retry_count for item in focused_items), default=0)
        focused_stall_count = max(
            (item.actions_since_progress for item in focused_items), default=0)
        focused_progress = any(
            item.last_progress_action == context.host_action_index
            for item in focused_items)
        runnable_count = len(context.goal_set.runnable_items)
        required_counts = context.goal_set.required_counts
        task_complete = (required_counts["total"] > 0
                         and required_counts["remaining_required"] == 0)
    else:
        focused_retry_count = context.equivalent_retry_count
        focused_stall_count = context.actions_since_goal_progress
        focused_progress = context.progress_made
        runnable_count = len(context.objective_criteria)
        task_complete = False

    scoped_retry_signal = any(
        "equivalent_retry" in normalized_behavior_codes((), cause.codes)
        and (set(cause.criterion_ids).intersection(attention_ids)
             or not cause.criterion_ids and not multi_item_task)
        for cause in causes)
    scoped_support_signal = any(
        "support_work_budget_exceeded" in cause.codes
        and (set(cause.criterion_ids).intersection(attention_ids)
             or not cause.criterion_ids and not multi_item_task)
        for cause in causes)
    scoped_drift_signal = any(
        "goal_drift" in normalized_behavior_codes((), cause.codes)
        and (set(cause.criterion_ids).intersection(attention_ids)
             or not cause.criterion_ids and not multi_item_task)
        for cause in causes)
    unscoped_retry = ("equivalent_retry" in codes and not multi_item_task)
    unscoped_support = ("support_work_budget_exceeded" in context.active_trigger_codes
                        and not multi_item_task)

    contradiction = bool(codes & {"operator_contradiction", "stale_assumption"})
    retry = (focused_retry_count >= retry_threshold
             or (semantic.retry_relation == "equivalent_retry"
                 and semantic.retry_confidence >= semantic_threshold)
             or scoped_retry_signal or unscoped_retry)
    drift = (focused_stall_count >= support_action_threshold
             or scoped_support_signal or scoped_drift_signal or unscoped_support
             or ("goal_drift" in codes and not multi_item_task)
             or (semantic.goal_drift is True
                 and semantic.goal_drift_confidence >= semantic_threshold)) \
            and not focused_progress
    pressure = (context.context_pressure >= 0.85
                or "context_pressure_high" in codes)
    triggers: set[str] = set()
    if contradiction:
        triggers.add("operator_contradiction" if "operator_contradiction" in codes
                     else "stale_assumption")
    if retry:
        triggers.add("equivalent_retry")
    if drift:
        triggers.add("goal_drift")
    if pressure:
        triggers.add("context_pressure")
    return DetectedConditions(
        attention_ids=tuple(attention_ids), focused_progress=focused_progress,
        runnable_count=runnable_count, task_complete=task_complete,
        contradiction=contradiction, retry=retry, drift=drift, pressure=pressure,
        trigger_codes=tuple(sorted(triggers)),
    )


__all__ = ["DetectedConditions", "evaluate_conditions"]
