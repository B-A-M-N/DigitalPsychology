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
                              outcome_evidence_connected: bool,
                              host_has_stable_objective_context: bool = False
                              ) -> BehaviorProjection:
    counts = goal_set.required_counts if goal_set is not None else {
        "total": 0, "verified": 0, "remaining_required": 0,
    }
    lines = ["===== CURRENT BEHAVIOR GUIDANCE ====="]
    if objective and not host_has_stable_objective_context:
        lines.append(f"User objective: {objective.strip()}")
    lines.append(
        f"Required work: {counts['verified']} of {counts['total']} criteria verified; "
        f"{counts['remaining_required']} remain.")
    if state.focus_criteria:
        lines.append("Current focus: " + ", ".join(state.focus_criteria) + ".")
    lines.append(
        "Keep work tied to required criteria. Make progress on the current focus, "
        "then return to any remaining required criteria.")

    lines.append(f"Current operating mode: {state.cognitive_mode.lower().replace('_', ' ')}.")
    exploration_guidance = {
        "disabled": "Do not explore unrelated alternatives; continue with the current goal-linked path.",
        "low": "Keep exploration small and use it only to resolve a needed uncertainty.",
        "normal": "Explore when it can reduce uncertainty or unblock a required criterion.",
        "bounded": "Compare only a few goal-linked alternatives before choosing a next step.",
    }
    lines.append(exploration_guidance[state.exploration_budget])
    context_guidance = {
        "standard": "Use the relevant task context when planning the next step.",
        "compact": "Keep working context concise and retain only details needed for the next step.",
        "criterion_focused": "Center working context on the current focus criteria and their evidence.",
    }
    lines.append(context_guidance[state.context_strategy])
    response_guidance = {
        "direct": "Keep the response direct and focused on the requested result.",
        "detailed": "Explain the result and the evidence behind it with useful detail.",
        "structured": "Organize the response into clear sections or steps when useful.",
    }
    lines.append(response_guidance[state.response_strategy])

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
        "model_controls": {
            "cognitive_mode": state.cognitive_mode,
            "exploration_budget": state.exploration_budget,
            "context_strategy": state.context_strategy,
            "response_strategy": state.response_strategy,
            "verification_intensity": state.verification_intensity,
            "retry_policy": state.retry_policy,
            "recovery_level": state.recovery_level,
        },
        "goal_set_revision": goal_set.revision if goal_set is not None else None,
        "goal_set_counts": dict(counts),
        "attention_lease": (state.attention_lease.to_dict()
                            if state.attention_lease else None),
        "mode_lease": (state.mode_lease.to_dict() if state.mode_lease else None),
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
    if context.count(opening) == 1:
        start = context.index(opening)
        end = context.find(closing, start)
        if (end >= 0
                and context[start:end].rstrip("\n") == projection.text
                and context.count(closing) == 1):
            return context
    while opening in context:
        start = context.index(opening)
        end = context.find(closing, start)
        if end < 0:
            context = context[:start].rstrip()
        else:
            context = (context[:start] + context[end + len(closing):]).strip()
    guidance = f"{projection.text}\n{closing}"
    return f"{context.rstrip()}\n\n{guidance}" if context.strip() else guidance


def model_projection_is_current(context: str, projection: ModelProjection) -> bool:
    """Return whether the context already has this exact single guidance slot."""
    opening = "===== CURRENT BEHAVIOR GUIDANCE ====="
    closing = "===== END CURRENT BEHAVIOR GUIDANCE ====="
    if context.count(opening) != 1 or context.count(closing) != 1:
        return False
    start = context.index(opening)
    end = context.find(closing, start)
    return end >= 0 and context[start:end].rstrip("\n") == projection.text


def cached_replace_model_projection(context: str, projection: ModelProjection,
                                   last_digest: str | None
                                   ) -> tuple[str, str]:
    """Reuse an unchanged host projection and return its cached digest."""
    if (last_digest == projection.semantic_digest
            and model_projection_is_current(context, projection)):
        return context, last_digest
    return replace_model_projection(context, projection), projection.semantic_digest


def project_behavior_context(state: BehaviorState, snapshot: CSWBehaviorSnapshot, *,
                             objective: str, goal_set: GoalSetSnapshot | None,
                             cfw_connected: bool, csw_connected: bool,
                             outcome_evidence_connected: bool,
                             host_has_stable_objective_context: bool = False) -> str:
    """Compatibility helper returning only the model-visible projection."""
    return build_behavior_projection(
        state, snapshot, objective=objective, goal_set=goal_set,
        cfw_connected=cfw_connected, csw_connected=csw_connected,
        outcome_evidence_connected=outcome_evidence_connected,
        host_has_stable_objective_context=host_has_stable_objective_context).model.text


__all__ = ["AuditProjection", "BehaviorProjection", "ModelProjection",
           "build_behavior_projection", "project_behavior_context",
           "replace_model_projection", "model_projection_is_current",
           "cached_replace_model_projection"]
