"""Durable telemetry for committed control-boundary transitions."""
from __future__ import annotations

from dataclasses import dataclass

from .behavioral_trace import JUSTIFICATION_CLASSES


@dataclass(frozen=True)
class ControlTransitionTrace:
    control_session_id: str
    boundary_sequence: int
    host_action_index: int
    boundary_kind: str
    behavior_revision: int
    prior_mode: str
    resulting_mode: str
    transition_causes: tuple[str, ...]
    justification_class: str
    semantic_query_attempted: bool
    semantic_judgment_returned: bool
    semantic_judgment_used: bool
    semantic_query_failed: bool
    goal_drift_detected: bool
    goal_drift_intervention_applied: bool
    cfw_rejection_stage: str | None
    cfw_preflight_decision: str | None = None
    cfw_final_decision: str | None = None

    def __post_init__(self) -> None:
        for name in ("control_session_id", "boundary_kind", "prior_mode",
                     "resulting_mode", "justification_class"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value or len(value) > 256:
                raise ValueError(f"control transition {name} must be bounded text")
        for name in ("boundary_sequence", "host_action_index", "behavior_revision"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"control transition {name} must be non-negative")
        causes = tuple(self.transition_causes)
        if (not 1 <= len(causes) <= 32
                or any(not isinstance(item, str) or not item or len(item) > 256
                       for item in causes)):
            raise ValueError("control transition causes must be bounded text")
        for name in ("semantic_query_attempted", "semantic_judgment_returned",
                     "semantic_judgment_used", "semantic_query_failed",
                     "goal_drift_detected", "goal_drift_intervention_applied"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"control transition {name} must be a boolean")
        if self.semantic_judgment_returned and not self.semantic_query_attempted:
            raise ValueError("a semantic judgment cannot return without a query")
        if self.semantic_judgment_used and not self.semantic_judgment_returned:
            raise ValueError("a semantic judgment cannot be used without being returned")
        if self.semantic_query_failed and not self.semantic_query_attempted:
            raise ValueError("a semantic query cannot fail without being attempted")
        if self.cfw_rejection_stage not in {
                None, "cfw_preflight", "cfw_final_gate"}:
            raise ValueError("control transition CFW rejection stage is invalid")
        if self.justification_class not in JUSTIFICATION_CLASSES:
            raise ValueError("control transition justification class is invalid")
        for name in ("cfw_preflight_decision", "cfw_final_decision"):
            decision = getattr(self, name)
            if decision is not None and decision not in {
                    "allow", "block", "require_confirmation", "require_reinspection",
                    "require_recovery", "require_changed_hypothesis",
                    "require_goal_reanchor"}:
                raise ValueError(f"control transition {name} is invalid")
        object.__setattr__(self, "transition_causes", causes)

    def to_dict(self) -> dict[str, object]:
        return {
            "control_session_id": self.control_session_id,
            "boundary_sequence": self.boundary_sequence,
            "host_action_index": self.host_action_index,
            "boundary_kind": self.boundary_kind,
            "behavior_revision": self.behavior_revision,
            "prior_mode": self.prior_mode,
            "resulting_mode": self.resulting_mode,
            "transition_causes": list(self.transition_causes),
            "justification_class": self.justification_class,
            "semantic_query_attempted": self.semantic_query_attempted,
            "semantic_judgment_returned": self.semantic_judgment_returned,
            "semantic_judgment_used": self.semantic_judgment_used,
            "semantic_query_failed": self.semantic_query_failed,
            "goal_drift_detected": self.goal_drift_detected,
            "goal_drift_intervention_applied": self.goal_drift_intervention_applied,
            "cfw_rejection_stage": self.cfw_rejection_stage,
            "cfw_preflight_decision": self.cfw_preflight_decision,
            "cfw_final_decision": self.cfw_final_decision,
        }


__all__ = ["ControlTransitionTrace"]
