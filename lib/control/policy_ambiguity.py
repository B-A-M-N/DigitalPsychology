"""A bounded distinction between the leading behavior candidates."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PolicyAmbiguity:
    """Top candidate distinction that a semantic answer could resolve."""

    top_candidate_id: str
    second_candidate_id: str
    score_gap: float
    unresolved_dimensions: tuple[str, ...]

    def __post_init__(self) -> None:
        for name, value in (("top candidate ID", self.top_candidate_id),
                            ("second candidate ID", self.second_candidate_id)):
            if not isinstance(value, str) or not value or len(value) > 128:
                raise ValueError(f"{name} must be bounded text")
        if self.top_candidate_id == self.second_candidate_id:
            raise ValueError("policy ambiguity requires two distinct candidates")
        if type(self.score_gap) not in (int, float) or not 0.0 <= float(self.score_gap) <= 1.0:
            raise ValueError("policy ambiguity score gap must be between zero and one")
        dimensions = tuple(self.unresolved_dimensions)
        supported = {"action_relation", "progress_relation", "retry_relation",
                     "goal_drift", "statework_fit", "decomposition_fit",
                     "integration_fit", "test_strategy_fit"}
        if (not dimensions or len(dimensions) > 8
                or len(dimensions) != len(set(dimensions))
                or any(item not in supported for item in dimensions)):
            raise ValueError("policy ambiguity must name bounded unresolved dimensions")
        object.__setattr__(self, "unresolved_dimensions", dimensions)


__all__ = ["PolicyAmbiguity"]
