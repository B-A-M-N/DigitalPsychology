"""Stable model guidance separated from private runtime audit metadata."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any, Mapping

from ..behavior_state import BehaviorState, CSWBehaviorSnapshot, GoalSetSnapshot


@dataclass(frozen=True)
class ModelProjection:
    """Only task-relevant guidance suitable for the controlled model context."""

    text: str
    semantic_digest: str


@dataclass(frozen=True)
class AuditProjection:
    """Private reconciliation and observability data; never model-visible."""

    metadata: Mapping[str, Any]


@dataclass(frozen=True)
class BehaviorProjection:
    model: ModelProjection
    audit: AuditProjection


def build_behavior_projection(state: BehaviorState,
                              snapshot: CSWBehaviorSnapshot, *,
                              objective: str,
                              goal_set: GoalSetSnapshot | None,
                              cfw_connected: bool,
                              csw_connected: bool,
                              outcome_evidence_connected: bool
                              ) -> BehaviorProjection:
    counts = goal_set.required_counts if goal_set is not None else {
        "total": 0, "verified": 0, "remaining_required": 0,
    }
    lines = ["===== CURRENT BEHAVIOR GUIDANCE ====="]
    if objective:
        lines.append(f"User objective: {objective.strip()}")
    lines.append(
        f"Required work: {counts['verified']} of {counts['total']} criteria verified; "
        f"{counts['remaining_required']} remain.")
    if state.focus_criteria:
        lines.append("Current focus: " + ", ".join(state.focus_criteria) + ".")
    lines.append(
        "Keep work tied to required criteria. Make progress on the current focus, "
        "then return to any remaining required criteria.")

    if state.retry_policy in {"materially_different_only", "no_retry"}:
        lines.append(
            "After a failed attempt, change the approach before retrying; repeated "
            "activity without criterion progress is not progress.")
    if state.verification_intensity in {"high", "strict"}:
        lines.append("Check claimed progress against the acceptance criteria and host evidence.")
    if state.recovery_level:
        lines.append(
            "If the current approach is blocked, inspect the blocker and choose a "
            "materially different goal-directed step.")

    workflow_states = tuple(snapshot.workflow_states.values())
    if workflow_states:
        counts_by_state: dict[str, int] = {}
        for workflow_state in workflow_states:
            label = str(workflow_state)
            counts_by_state[label] = counts_by_state.get(label, 0) + 1
        concise = ", ".join(
            f"{name}: {number}" for name, number in sorted(counts_by_state.items())[:4])
        lines.append(f"Relevant workflow progress: {concise}.")

    text = "\n".join(lines)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    audit = {
        "behavior_revision": state.revision,
        "goal_set_revision": goal_set.revision if goal_set is not None else None,
        "goal_set_counts": dict(counts),
        "attention_lease": (state.attention_lease.to_dict()
                            if state.attention_lease else None),
        "attention_last_seen": dict(state.attention_last_seen),
        "active_interventions": [item.to_dict() for item in state.active_interventions],
        "csw_selection_revision": snapshot.revision,
        "csw_snapshot_id": snapshot.snapshot_id,
        "csw_active_stateworks": list(snapshot.active_stateworks),
        "csw_workflow_states": dict(snapshot.workflow_states),
        "csw_workflow_revisions": dict(snapshot.workflow_revisions),
        "csw_eligible_steps": {key: list(value)
                                for key, value in snapshot.eligible_steps.items()},
        "authority_availability": {
            "cfw": bool(cfw_connected), "csw": bool(csw_connected),
            "outcome_evidence": bool(outcome_evidence_connected),
        },
        "model_semantic_digest": digest,
    }
    return BehaviorProjection(ModelProjection(text=text, semantic_digest=digest),
                              AuditProjection(metadata=audit))


def replace_model_projection(context: str, projection: ModelProjection) -> str:
    """Replace a prior DP guidance slot instead of accumulating prompt text."""
    if not isinstance(context, str):
        raise TypeError("model context must be text")
    opening = "===== CURRENT BEHAVIOR GUIDANCE ====="
    closing = "===== END CURRENT BEHAVIOR GUIDANCE ====="
    while opening in context:
        start = context.index(opening)
        end = context.find(closing, start)
        if end < 0:
            context = context[:start].rstrip()
        else:
            context = (context[:start] + context[end + len(closing):]).strip()
    guidance = f"{projection.text}\n{closing}"
    return f"{context.rstrip()}\n\n{guidance}" if context.strip() else guidance


def project_behavior_context(state: BehaviorState, snapshot: CSWBehaviorSnapshot, *,
                             objective: str, goal_set: GoalSetSnapshot | None,
                             cfw_connected: bool, csw_connected: bool,
                             outcome_evidence_connected: bool) -> str:
    """Compatibility helper returning only the model-visible projection."""
    return build_behavior_projection(
        state, snapshot, objective=objective, goal_set=goal_set,
        cfw_connected=cfw_connected, csw_connected=csw_connected,
        outcome_evidence_connected=outcome_evidence_connected).model.text


__all__ = ["AuditProjection", "BehaviorProjection", "ModelProjection",
           "build_behavior_projection", "project_behavior_context",
           "replace_model_projection"]
