"""Durable telemetry for committed control-boundary transitions."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

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
    semantic_question_ids: tuple[str, ...]
    semantic_influence_receipt_id: str | None
    semantic_influenced_decision: bool
    semantic_candidate_change: bool
    semantic_mode_change: bool
    semantic_framework_changes: int
    semantic_statework_change: bool
    semantic_influenced_interventions: int
    semantic_effective_interventions: int
    semantic_ineffective_interventions: int
    goal_drift_detected: bool
    goal_drift_intervention_applied: bool
    cfw_rejection_stage: str | None
    model_projection_digest: str | None = None
    ward_hard_safety: bool = False
    cfw_preflight_decision: str | None = None
    cfw_final_decision: str | None = None
    cfw_gate_receipts: Mapping[str, Any] | None = None

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
                     "semantic_influenced_decision", "semantic_candidate_change",
                     "semantic_mode_change", "semantic_statework_change",
                     "goal_drift_detected", "goal_drift_intervention_applied",
                     "ward_hard_safety"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"control transition {name} must be a boolean")
        if self.semantic_judgment_returned and not self.semantic_query_attempted:
            raise ValueError("a semantic judgment cannot return without a query")
        if self.semantic_judgment_used and not self.semantic_judgment_returned:
            raise ValueError("a semantic judgment cannot be used without being returned")
        if self.semantic_query_failed and not self.semantic_query_attempted:
            raise ValueError("a semantic query cannot fail without being attempted")
        questions = tuple(self.semantic_question_ids)
        if (len(questions) > 8 or len(questions) != len(set(questions))
                or any(not isinstance(item, str) or not item or len(item) > 64
                       for item in questions)):
            raise ValueError("control transition semantic questions are malformed")
        if self.semantic_influence_receipt_id is not None and (
                not isinstance(self.semantic_influence_receipt_id, str)
                or not self.semantic_influence_receipt_id
                or len(self.semantic_influence_receipt_id) > 128):
            raise ValueError("control transition influence receipt ID is malformed")
        for name in ("semantic_framework_changes", "semantic_influenced_interventions",
                     "semantic_effective_interventions",
                     "semantic_ineffective_interventions"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"control transition {name} must be non-negative")
        if self.cfw_rejection_stage not in {
                None, "cfw_preflight", "cfw_final_gate"}:
            raise ValueError("control transition CFW rejection stage is invalid")
        if self.justification_class not in JUSTIFICATION_CLASSES:
            raise ValueError("control transition justification class is invalid")
        if (self.model_projection_digest is not None
                and (not isinstance(self.model_projection_digest, str)
                     or len(self.model_projection_digest) != 64)):
            raise ValueError("control transition projection digest is invalid")
        for name in ("cfw_preflight_decision", "cfw_final_decision"):
            decision = getattr(self, name)
            if decision is not None and decision not in {
                    "allow", "block", "require_confirmation", "require_reinspection",
                    "require_recovery", "require_changed_hypothesis",
                    "require_goal_reanchor"}:
                raise ValueError(f"control transition {name} is invalid")
        if self.cfw_gate_receipts is not None:
            if set(self.cfw_gate_receipts) not in ({"preflight"}, {"preflight", "final"}):
                raise ValueError("control transition gate receipts are invalid")
            object.__setattr__(self, "cfw_gate_receipts", dict(self.cfw_gate_receipts))
        object.__setattr__(self, "transition_causes", causes)
        object.__setattr__(self, "semantic_question_ids", questions)

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
            "semantic_question_ids": list(self.semantic_question_ids),
            "semantic_influence_receipt_id": self.semantic_influence_receipt_id,
            "semantic_influenced_decision": self.semantic_influenced_decision,
            "semantic_candidate_change": self.semantic_candidate_change,
            "semantic_mode_change": self.semantic_mode_change,
            "semantic_framework_changes": self.semantic_framework_changes,
            "semantic_statework_change": self.semantic_statework_change,
            "semantic_influenced_interventions": self.semantic_influenced_interventions,
            "semantic_effective_interventions": self.semantic_effective_interventions,
            "semantic_ineffective_interventions": self.semantic_ineffective_interventions,
            "goal_drift_detected": self.goal_drift_detected,
            "goal_drift_intervention_applied": self.goal_drift_intervention_applied,
            "cfw_rejection_stage": self.cfw_rejection_stage,
            "model_projection_digest": self.model_projection_digest,
            "ward_hard_safety": self.ward_hard_safety,
            "cfw_preflight_decision": self.cfw_preflight_decision,
            "cfw_final_decision": self.cfw_final_decision,
            "cfw_gate_receipts": (dict(self.cfw_gate_receipts)
                                  if self.cfw_gate_receipts is not None else None),
        }


__all__ = ["ControlTransitionTrace"]
