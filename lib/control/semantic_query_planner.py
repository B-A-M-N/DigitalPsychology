"""Value-of-information gate for optional semantic provider calls.

The planner consumes only host-owned signals and action/outcome facts. It
never chooses an executive policy; it decides whether one semantic question
could resolve a material ambiguity cheaply enough to justify inference.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from ..behavior_state import FrameworkSignal, group_behavioral_causes
from ..runtime.contracts import RuntimeObservation
from .policy_ambiguity import PolicyAmbiguity


@dataclass(frozen=True)
class SemanticQueryPlan:
    question_id: str | None
    reason: str
    value_of_information: float

    def __post_init__(self) -> None:
        if self.question_id is not None and self.question_id not in {
                "action_relation", "progress_relation", "retry_relation", "goal_drift",
                "route_preference"}:
            raise ValueError("semantic query plan names an unsupported question")
        if not isinstance(self.reason, str) or not self.reason or len(self.reason) > 256:
            raise ValueError("semantic query reason must be bounded non-empty text")
        if (not isinstance(self.value_of_information, (int, float))
                or not 0.0 <= float(self.value_of_information) <= 1.0):
            raise ValueError("semantic query value of information must be in [0, 1]")
        if self.question_id is None and self.value_of_information != 0:
            raise ValueError("a skipped semantic query must have zero value of information")

    @property
    def should_query(self) -> bool:
        return self.question_id is not None


class SemanticQueryPlanner:
    """Select at most one calibrated semantic question for an ambiguous event."""

    def __init__(self, *, minimum_value_of_information: float = 0.65,
                 support_action_threshold: int = 3,
                 maximum_ambiguity_gap: float = 0.25) -> None:
        if (type(minimum_value_of_information) not in (int, float)
                or not 0.5 <= float(minimum_value_of_information) <= 1.0):
            raise ValueError("minimum semantic value of information must be in [0.5, 1]")
        if type(support_action_threshold) is not int or not 1 <= support_action_threshold <= 64:
            raise ValueError("support action threshold must be between 1 and 64")
        if (type(maximum_ambiguity_gap) not in (int, float)
                or not 0.0 <= float(maximum_ambiguity_gap) <= 1.0):
            raise ValueError("maximum ambiguity gap must be between zero and one")
        self.minimum_value_of_information = float(minimum_value_of_information)
        self.support_action_threshold = support_action_threshold
        self.maximum_ambiguity_gap = float(maximum_ambiguity_gap)

    def plan(self, observation: RuntimeObservation, *,
             cfw_signals: Sequence[FrameworkSignal] = (),
             policy_ambiguity: PolicyAmbiguity | None = None) -> SemanticQueryPlan:
        causes = group_behavioral_causes(cfw_signals)
        if any(cause.hard_authority for cause in causes):
            return self._skip("hard CFW authority already resolves this boundary")
        codes = tuple((source + "." + code).lower()
                      for cause in causes
                      for source in cause.sources
                      for code in cause.codes)
        retry_signal = any(any(token in code for token in (
            "retry", "repeat", "same_approach", "approach_failed", "equivalent"))
                               for code in codes)
        drift_signal = any(any(token in code for token in (
            "drift", "unrelated", "support_work", "criterion_mismatch", "busywork"))
                               for code in codes)
        retry_count = observation.equivalent_retry_count
        progress_gap = observation.actions_since_goal_progress
        if observation.goal_set is not None:
            candidate = observation.candidate_action
            criterion_ids = (candidate.affected_criterion_ids if candidate is not None
                             else tuple(item.criterion_id
                                        for item in observation.goal_set.runnable_items))
            relevant_items = tuple(item for item in observation.goal_set.items
                                   if item.criterion_id in criterion_ids)
            retry_count = max(retry_count, *(item.equivalent_retry_count
                                             for item in relevant_items), 0)
            progress_gap = max(progress_gap, *(item.actions_since_progress
                                               for item in relevant_items), 0)

        candidate = observation.candidate_action
        if candidate is not None and retry_count >= 2:
            return self._skip("repeated approach is already established by host counters")
        if (observation.action_completed
                and (observation.progress_made or observation.regression_observed)):
            return self._skip("host outcome evidence already resolves progress or regression")

        if policy_ambiguity is None:
            return self._skip("no candidate ambiguity was supplied")
        if policy_ambiguity.score_gap > self.maximum_ambiguity_gap:
            return self._skip("candidate margin is too large for a semantic result to change policy")

        question: str | None = None
        reason = "host evidence does not identify a semantic ambiguity"
        voi = 0.0
        if "statework_fit" in policy_ambiguity.unresolved_dimensions:
            question = "route_preference"
            reason = "two CSW-eligible StateWorks are plausible for the current behavior mode"
            voi = 0.70
        elif candidate is not None:
            if retry_count >= 2:
                return self._skip("repeated approach is already established by host counters")
            if retry_count == 1 or retry_signal:
                question = "retry_relation"
                reason = "candidate may repeat a failed approach; equivalence is unresolved"
                voi = 0.86
            elif drift_signal:
                question = "action_relation"
                reason = "CFW reports possible goal drift in a consequential candidate"
                voi = 0.78
            elif (candidate.relation_certainty == "unknown" or candidate.external
                  or (candidate.mutating and not candidate.reversible)):
                question = "action_relation"
                reason = "candidate goal relation is unknown or consequential"
                voi = 0.72
        elif observation.action_completed:
            if observation.progress_made or observation.regression_observed:
                return self._skip("host outcome evidence already resolves progress or regression")
            if retry_count >= 2:
                return self._skip("repeated approach is already established by host counters")
            if retry_count == 1 or retry_signal:
                question = "retry_relation"
                reason = "host observed one possible repeat without objective progress"
                voi = 0.82
            elif (progress_gap >= self.support_action_threshold
                  and drift_signal):
                question = "goal_drift"
                reason = "sustained support activity may have displaced required work"
                voi = 0.79
            elif observation.artifact_changed or observation.evidence_refs:
                question = "progress_relation"
                reason = "artifact/evidence changed without host-confirmed criterion progress"
                voi = 0.70
        else:
            return self._skip("non-action boundary has no unresolved semantic decision")

        question_dimension = "statework_fit" if question == "route_preference" else question
        if (question is None
                or question_dimension not in policy_ambiguity.unresolved_dimensions
                or voi < self.minimum_value_of_information):
            return self._skip(reason)
        if retry_signal:
            retry_causes = [cause for cause in causes if any(
                token in f"{source}.{code}".lower()
                for source in cause.sources for code in cause.codes
                for token in ("retry", "repeat", "same_approach",
                              "approach_failed", "equivalent"))]
            if retry_causes:
                corroborating = sum(max(len(cause.signal_ids), len(cause.codes))
                                     for cause in retry_causes)
                reason = (f"{len(retry_causes)} CFW retry cause(s) warrant one bounded query; "
                          f"{corroborating} signal(s) are corroborating observations")
        return SemanticQueryPlan(question, reason, voi)

    @staticmethod
    def _skip(reason: str) -> SemanticQueryPlan:
        return SemanticQueryPlan(None, reason[:256], 0.0)


__all__ = ["PolicyAmbiguity", "SemanticQueryPlan", "SemanticQueryPlanner"]
