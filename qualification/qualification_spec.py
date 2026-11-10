"""Deliverable C — statistical qualification specification.

Criteria are defined BEFORE any measurement. Every threshold below was read from
its authoritative source rather than assumed:

  * 100/30/20  -> lib/system_one_calibration.py
      minimum_outcomes=100
      PRODUCTION_MINIMUM_INDEPENDENT_TRAJECTORIES=30
      PRODUCTION_MINIMUM_COHORT_TRAJECTORIES=20

  * >=0.80 accuracy -> qualification_minimum_accuracy, re-checked against
      holdout_accuracy in valid_for()

  * >=0.15 margin -> AGGREGATE, not per-decision.
      laya-route-screen-stage-b-2026-10-10.json declares
      decision_rule.margin_floor = 0.15 alongside
      metrics.mean_confidence_margin (an aggregate over the benchmark).
      That run's own per-case margins were 0.0298-0.2090 (mean 0.1274), so a
      per-decision floor of 0.15 would have rejected every case that produced
      it. Enforcing 0.15 per-decision would make the only legitimate advisory
      surface unreachable.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Authoritative aggregate gates.
MINIMUM_OUTCOMES = 100
MINIMUM_INDEPENDENT_TRAJECTORIES = 30
MINIMUM_COHORT_TRAJECTORIES = 20

MINIMUM_CALIBRATED_ACCURACY = 0.80
MAXIMUM_BRIER = 0.25
MAXIMUM_ECE = 0.12

# Aggregate qualification margin (benchmark mean top1-top2).
AGGREGATE_MARGIN_FLOOR = 0.15
# Per-decision ambiguity floor. Deliberately much smaller than the aggregate
# bar: it rejects coin-flips, not the qualified model's real per-case spread.
PER_DECISION_MARGIN_FLOOR = 0.05

SAMPLE_UNIT = "trajectory"


@dataclass(frozen=True)
class QualificationSpec:
    """Acceptance criteria, fixed before evaluation."""

    minimum_outcomes: int = MINIMUM_OUTCOMES
    minimum_independent_trajectories: int = MINIMUM_INDEPENDENT_TRAJECTORIES
    minimum_cohort_trajectories: int = MINIMUM_COHORT_TRAJECTORIES
    minimum_calibrated_accuracy: float = MINIMUM_CALIBRATED_ACCURACY
    maximum_brier: float = MAXIMUM_BRIER
    maximum_ece: float = MAXIMUM_ECE
    aggregate_margin_floor: float = AGGREGATE_MARGIN_FLOOR
    per_decision_margin_floor: float = PER_DECISION_MARGIN_FLOOR

    def __post_init__(self) -> None:
        if self.minimum_outcomes < 1 or self.minimum_independent_trajectories < 1:
            raise ValueError("qualification sample floors must be positive")
        if not 0.0 <= self.minimum_calibrated_accuracy <= 1.0:
            raise ValueError("accuracy floor must be a probability")
        if not 0.0 <= self.aggregate_margin_floor <= 1.0:
            raise ValueError("aggregate margin floor must be a probability")
        if not 0.0 <= self.per_decision_margin_floor <= 1.0:
            raise ValueError("per-decision margin floor must be a probability")

    def evaluate_sample_size(self, *, outcomes: int, trajectories: int,
                             cohort_trajectories: int) -> dict[str, Any]:
        """Check the independence gates. Underpowered stays INCONCLUSIVE."""
        return {
            "outcomes_ok": outcomes >= self.minimum_outcomes,
            "trajectories_ok": trajectories >= self.minimum_independent_trajectories,
            "cohort_ok": cohort_trajectories >= self.minimum_cohort_trajectories,
            "sample_unit": SAMPLE_UNIT,
            "verdict": ("QUALIFIED_FOR_EVALUATION"
                        if (outcomes >= self.minimum_outcomes
                            and trajectories >= self.minimum_independent_trajectories
                            and cohort_trajectories >= self.minimum_cohort_trajectories)
                        else "INCONCLUSIVE_UNDERPOWERED"),
        }

    def evaluate_discrimination(self, *, balanced_accuracy: float) -> dict[str, Any]:
        return {
            "balanced_accuracy": balanced_accuracy,
            "floor": self.minimum_calibrated_accuracy,
            "passed": balanced_accuracy >= self.minimum_calibrated_accuracy,
        }

    def evaluate_calibration(self, *, brier: float, ece: float) -> dict[str, Any]:
        return {
            "brier": brier, "brier_floor": self.maximum_brier,
            "brier_passed": brier <= self.maximum_brier,
            "ece": ece, "ece_floor": self.maximum_ece,
            "ece_passed": ece <= self.maximum_ece,
            # Calibration does NOT establish discrimination, and vice versa.
            "note": ("calibration measures whether confidence matches observed "
                     "correctness; it does not measure whether the model can "
                     "distinguish the classes at all"),
        }

    def evaluate_margin(self, *, per_case_margins: tuple[float, ...]
                        ) -> dict[str, Any]:
        """Evaluate the aggregate bar and the per-decision floor separately."""
        if not per_case_margins:
            return {"verdict": "INCONCLUSIVE_NO_DATA"}
        mean_margin = sum(per_case_margins) / len(per_case_margins)
        below_floor = [m for m in per_case_margins
                       if m < self.per_decision_margin_floor]
        return {
            "mean_margin": mean_margin,
            "aggregate_floor": self.aggregate_margin_floor,
            "aggregate_passed": mean_margin >= self.aggregate_margin_floor,
            "min_margin": min(per_case_margins),
            "max_margin": max(per_case_margins),
            "per_decision_floor": self.per_decision_margin_floor,
            "per_decision_abstentions": len(below_floor),
            "note": ("the 0.15 bar is AGGREGATE; the per-decision floor only "
                     "rejects coin-flips"),
        }

    def evaluate_incremental_utility(self, *, baseline_accuracy: float,
                                     model_accuracy: float,
                                     false_intervention_rate: float
                                     ) -> dict[str, Any]:
        """Improvement net of the harm caused by unnecessary intervention."""
        return {
            "baseline_accuracy": baseline_accuracy,
            "model_accuracy": model_accuracy,
            "delta": model_accuracy - baseline_accuracy,
            "false_intervention_rate": false_intervention_rate,
            "net_utility": (model_accuracy - baseline_accuracy
                            - false_intervention_rate),
            "note": ("a model that improves accuracy while overriding correct "
                     "deterministic selections can still be net harmful"),
        }


__all__ = ["AGGREGATE_MARGIN_FLOOR", "MINIMUM_CALIBRATED_ACCURACY",
           "MINIMUM_COHORT_TRAJECTORIES", "MINIMUM_INDEPENDENT_TRAJECTORIES",
           "MINIMUM_OUTCOMES", "PER_DECISION_MARGIN_FLOOR", "QualificationSpec"]
