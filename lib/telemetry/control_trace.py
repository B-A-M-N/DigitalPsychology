"""Durable telemetry for committed control-boundary transitions."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ControlTransitionTrace:
    control_session_id: str
    boundary_sequence: int
    boundary_kind: str
    behavior_revision: int
    prior_mode: str
    resulting_mode: str
    transition_causes: tuple[str, ...]
    justification_class: str
    semantic_query_attempted: bool
    semantic_judgment_used: bool
    cfw_rejection_stage: str | None

    def __post_init__(self) -> None:
        for name in ("control_session_id", "boundary_kind", "prior_mode",
                     "resulting_mode", "justification_class"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value or len(value) > 256:
                raise ValueError(f"control transition {name} must be bounded text")
        for name in ("boundary_sequence", "behavior_revision"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"control transition {name} must be non-negative")
        causes = tuple(self.transition_causes)
        if (not 1 <= len(causes) <= 32
                or any(not isinstance(item, str) or not item or len(item) > 256
                       for item in causes)):
            raise ValueError("control transition causes must be bounded text")
        if type(self.semantic_query_attempted) is not bool or type(
                self.semantic_judgment_used) is not bool:
            raise ValueError("control transition semantic flags must be booleans")
        if self.semantic_judgment_used and not self.semantic_query_attempted:
            raise ValueError("a semantic judgment cannot be used without a query")
        if self.cfw_rejection_stage not in {
                None, "cfw_preflight", "cfw_final_gate"}:
            raise ValueError("control transition CFW rejection stage is invalid")
        object.__setattr__(self, "transition_causes", causes)

    def to_dict(self) -> dict[str, object]:
        return {
            "control_session_id": self.control_session_id,
            "boundary_sequence": self.boundary_sequence,
            "boundary_kind": self.boundary_kind,
            "behavior_revision": self.behavior_revision,
            "prior_mode": self.prior_mode,
            "resulting_mode": self.resulting_mode,
            "transition_causes": list(self.transition_causes),
            "justification_class": self.justification_class,
            "semantic_query_attempted": self.semantic_query_attempted,
            "semantic_judgment_used": self.semantic_judgment_used,
            "cfw_rejection_stage": self.cfw_rejection_stage,
        }


__all__ = ["ControlTransitionTrace"]
