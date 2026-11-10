"""Provider-neutral semantic evidence parsed for DP policy decisions."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Protocol

class _Answer(Protocol):
    selected: str
    raw_confidence: float
    calibrated_confidence: Optional[float]
    raw_probabilities: Any
    calibrated_probabilities: Any
class _Judgment(Protocol):
    def answer(self, question_id: str) -> Optional[_Answer]: ...
@dataclass(frozen=True)
class SemanticAssessment:
    """Small provider-neutral view of DP System-1 results."""

    action_relation: Optional[str] = None
    progress_relation: Optional[str] = None
    retry_relation: Optional[str] = None
    goal_drift: Optional[bool] = None
    confidence: float = 0.0
    retry_confidence: float = 0.0
    goal_drift_confidence: float = 0.0
    action_confidence: float = 0.0
    progress_confidence: float = 0.0
    statework_preference: str | None = None
    statework_confidence: float = 0.0
    statework_support: tuple[tuple[str, float], ...] = ()
    decomposition_preference: str | None = None
    decomposition_confidence: float = 0.0
    decomposition_support: tuple[tuple[str, float], ...] = ()
    integration_preference: str | None = None
    integration_confidence: float = 0.0
    integration_support: tuple[tuple[str, float], ...] = ()
    test_strategy_preference: str | None = None
    test_strategy_confidence: float = 0.0
    test_strategy_support: tuple[tuple[str, float], ...] = ()

    def __post_init__(self) -> None:
        for name, choices in (
            ("action_relation", {"advances_primary_goal", "removes_concrete_blocker",
                                 "required_verification", "diagnostic_support", "unrelated"}),
            ("progress_relation", {"goal_progress", "blocker_removed", "diagnostic_progress",
                                   "verification_progress", "recovery_progress", "no_progress"}),
            ("retry_relation", {"materially_new", "same_premise_new_action",
                                "equivalent_retry", "unrelated"}),
        ):
            value = getattr(self, name)
            if value is not None and value not in choices:
                raise ValueError(f"unsupported semantic {name}")
        if self.goal_drift is not None and not isinstance(self.goal_drift, bool):
            raise ValueError("goal_drift must be a boolean or null")
        if (self.statework_preference is not None
                and (not isinstance(self.statework_preference, str)
                     or not self.statework_preference
                     or len(self.statework_preference) > 128)):
            raise ValueError("statework_preference must be bounded text or null")
        for name in ("decomposition_preference", "integration_preference",
                     "test_strategy_preference"):
            value = getattr(self, name)
            if (value is not None
                    and (not isinstance(value, str)
                         or not value
                         or len(value) > 128)):
                raise ValueError(f"{name} must be bounded text or null")
        for confidence in (self.confidence, self.retry_confidence,
                           self.goal_drift_confidence, self.action_confidence,
                           self.progress_confidence, self.statework_confidence,
                           self.decomposition_confidence,
                           self.integration_confidence,
                           self.test_strategy_confidence):
            if not 0.0 <= float(confidence) <= 1.0:
                raise ValueError("semantic confidence must be between zero and one")

def assess_semantics(judgment: Optional[_Judgment]) -> SemanticAssessment:
    if judgment is None:
        return SemanticAssessment()

    def read(question_id: str) -> tuple[Optional[str], float]:
        answer = judgment.answer(question_id)
        if answer is None:
            return None, 0.0
        confidence = (answer.calibrated_confidence
                      if answer.calibrated_confidence is not None
                      else answer.raw_confidence)
        return answer.selected, float(confidence)

    def support(question_id: str) -> tuple[tuple[str, float], ...]:
        """Return the authentic option distribution for one question.

        Preference ambiguity cannot be judged from a single selected label: the
        runner-up mass is what distinguishes a decisive preference from a coin
        flip. Calibrated probabilities are used when a calibration record
        applies, otherwise raw model probabilities.
        """
        answer = judgment.answer(question_id)
        if answer is None:
            return ()
        distribution = (answer.calibrated_probabilities
                        if answer.calibrated_probabilities is not None
                        else answer.raw_probabilities)
        return tuple((str(item.label), float(item.probability))
                     for item in distribution)

    action, action_conf = read("action_relation")
    progress, progress_conf = read("progress_relation")
    retry, retry_conf = read("retry_relation")
    drift, drift_conf = read("goal_drift")
    statework, statework_conf = read("route_preference")
    decomposition, decomposition_conf = read("decomposition_preference")
    integration, integration_conf = read("integration_preference")
    test_strategy, test_strategy_conf = read("test_strategy_preference")
    selected_drift = (True if drift == "yes" else False if drift == "no" else None)
    confidence = max(action_conf, progress_conf, retry_conf, drift_conf,
                     statework_conf, decomposition_conf, integration_conf,
                     test_strategy_conf)
    return SemanticAssessment(
        action, progress, retry, selected_drift, confidence,
        retry_confidence=retry_conf, goal_drift_confidence=drift_conf,
        action_confidence=action_conf, progress_confidence=progress_conf,
        statework_preference=statework, statework_confidence=statework_conf,
        statework_support=support("route_preference"),
        decomposition_preference=decomposition,
        decomposition_confidence=decomposition_conf,
        decomposition_support=support("decomposition_preference"),
        integration_preference=integration,
        integration_confidence=integration_conf,
        integration_support=support("integration_preference"),
        test_strategy_preference=test_strategy,
        test_strategy_confidence=test_strategy_conf,
        test_strategy_support=support("test_strategy_preference"),
    )
