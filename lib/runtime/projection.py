"""Pure prompt projection of DP behavior and authoritative host snapshots."""
from __future__ import annotations

import json

from ..behavior_state import BehaviorState, CSWBehaviorSnapshot, GoalSetSnapshot


def project_behavior_context(state: BehaviorState, snapshot: CSWBehaviorSnapshot, *,
                             objective: str, goal_set: GoalSetSnapshot | None,
                             cfw_connected: bool, csw_connected: bool,
                             outcome_evidence_connected: bool) -> str:
    runnable = goal_set.runnable_items if goal_set is not None else ()
    focused = set(state.focus_criteria)
    next_ready = sorted(
        (item for item in runnable if item.criterion_id not in focused),
        key=lambda item: (-item.priority, item.criterion_id))[:2]
    oldest_starved = sorted(
        runnable,
        key=lambda item: (state.attention_last_seen.get(item.criterion_id, -1),
                          item.criterion_id))[:1]
    payload = {
        "revision": state.revision,
        "bound_authorities": {
            "cfw": "connected" if cfw_connected else "unavailable",
            "csw": "connected" if csw_connected else "unavailable",
            "outcome_evidence": ("connected" if outcome_evidence_connected
                                 else "unavailable"),
        },
        "objective": objective,
        "task_set_summary": (goal_set.required_counts if goal_set is not None else {
            "total": 0, "verified": 0, "ready": 0, "blocked": 0,
            "dependency_blocked": 0, "deferred": 0, "remaining_required": 0,
        }),
        "attention_window": list(state.focus_criteria),
        "attention_lease": (state.attention_lease.to_dict()
                            if state.attention_lease is not None else None),
        "next_ready": [item.criterion_id for item in next_ready],
        "oldest_starved": (oldest_starved[0].criterion_id if oldest_starved else None),
        "cognitive_mode": state.cognitive_mode,
        "framework_modes": dict(state.framework_modes) if cfw_connected else {},
        "unapplied_framework_modes": ({} if cfw_connected
                                      else dict(state.framework_modes)),
        "controls": {
            "verification_intensity": state.verification_intensity,
            "exploration_budget": state.exploration_budget,
            "retry_policy": state.retry_policy,
            "recovery_level": state.recovery_level,
            "context_strategy": state.context_strategy,
            "response_strategy": state.response_strategy,
        },
        "csw_active_stateworks": list(snapshot.active_stateworks),
        "csw_selection_revision": snapshot.revision,
        "csw_snapshot_id": snapshot.snapshot_id,
        "csw_workflow_states": dict(snapshot.workflow_states),
        "csw_workflow_revisions": dict(snapshot.workflow_revisions),
        "csw_eligible_steps": {key: list(value)
                                for key, value in snapshot.eligible_steps.items()},
        "csw_eligible_stateworks": [item.to_dict()
                                    for item in snapshot.eligible_stateworks],
        "active_interventions": [{
            "intervention_id": item.intervention_id,
            "cause": item.cause,
            "expected_effect": item.expected_effect,
            "actions_remaining": max(0, item.expires_after_actions - item.actions_elapsed),
            "target_criteria": list(item.target_criteria),
        } for item in state.active_interventions],
    }
    return ("===== DIGITAL PSYCHOLOGY BEHAVIOR STATE =====\n"
            "Host-selected behavioral guidance. Bound authorities and available "
            "components are listed in the state projection.\n"
            + json.dumps(payload, sort_keys=True, separators=(",", ":")))


__all__ = ["project_behavior_context"]
