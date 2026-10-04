"""Shared classification for authoritative, criterion-level progress."""
from __future__ import annotations

from ..behavior_state import GoalSetSnapshot


def material_progress_delta(previous: GoalSetSnapshot | None,
                            current: GoalSetSnapshot | None) -> tuple[str, ...]:
    """Return criteria whose material progress revision advanced.

    Ordinary progress counters and goal-set revisions do not count. A missing
    prior criterion starts at revision zero, matching the persisted contract.
    """
    if current is None:
        return ()
    prior_revisions = ({item.criterion_id: item.material_progress_revision
                        for item in previous.items} if previous is not None else {})
    return tuple(sorted(
        item.criterion_id for item in current.items
        if item.material_progress_revision > prior_revisions.get(item.criterion_id, 0)
    ))


def normalized_behavior_codes(active_trigger_codes=(), cfw_signal_codes=()) -> frozenset[str]:
    """Combine host and CFW signals into shared retry/drift behavior codes."""
    raw = {str(code) for code in (*active_trigger_codes, *cfw_signal_codes)
           if isinstance(code, str) and code}
    normalized = set(raw)
    if raw & {"owl.approach_failed", "fuse.retry_bound_exceeded"}:
        normalized.add("equivalent_retry")
    if "owl.constraint_drift" in raw:
        normalized.add("goal_drift")
    return frozenset(normalized)


__all__ = ["material_progress_delta", "normalized_behavior_codes"]
