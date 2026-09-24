"""Receipt-domain primitives independent of the event and guard planes."""
from __future__ import annotations

from typing import Optional, Tuple

RECEIPT_BUILDER_VERSION = "2.0.0"
RECEIPT_PROVENANCE_VERSION = "1.0.0"
MIN_EVIDENCE_SAMPLES = 3
DEFAULT_EXPERIMENT_CRITERION = {
    "version": "criterion-1",
    "evaluator_version": "wilson-95-v1",
    "minimum_applicable_per_cohort": MIN_EVIDENCE_SAMPLES,
    "min_applicable": MIN_EVIDENCE_SAMPLES,
    "min_effect": 0.0,
    "confidence_rule": "wilson_lower_bound_positive",
    "regression_probes": [],
}

# A research receipt may be useful for analysis, but production activation
# needs a materially larger cohort and a non-trivial measured effect.  The
# exporter applies this criterion to active guards; offline acceptance runs
# may explicitly identify themselves in the receipt environment.
DEFAULT_PRODUCTION_CRITERION = {
    "version": "criterion-production-1",
    "evaluator_version": "wilson-95-v1",
    "minimum_applicable_per_cohort": 10,
    "min_effect": 0.10,
    "confidence_rule": "wilson_intervention_gt_baseline",
    "regression_probes": [],
    "authority": "digitalpsychology-built-in-production-v1",
}


def is_production_grade_criterion(criterion: dict) -> bool:
    if bool(criterion.get("research_only")) or bool(criterion.get("caller_authored")):
        return False
    minimum = int(criterion.get("minimum_applicable_per_cohort",
                               criterion.get("min_applicable", 0)) or 0)
    return (minimum >= DEFAULT_PRODUCTION_CRITERION["minimum_applicable_per_cohort"]
            and float(criterion.get("min_effect", 0.0)) >=
            DEFAULT_PRODUCTION_CRITERION["min_effect"]
            and criterion.get("confidence_rule") ==
            DEFAULT_PRODUCTION_CRITERION["confidence_rule"]
            and criterion.get("authority") == DEFAULT_PRODUCTION_CRITERION["authority"]
            and bool(criterion.get("version"))
            and bool(criterion.get("evaluator_version")))


def wilson_interval(rate: float, count: int) -> Tuple[Optional[float], Optional[float]]:
    """Return the versioned Wilson 95% interval used by promotion gates."""
    if count <= 0:
        return None, None
    z = 1.96
    denom = 1 + z * z / count
    center = (rate + z * z / (2 * count)) / denom
    margin = z * (rate * (1 - rate) / count + z * z / (4 * count * count)) ** 0.5 / denom
    return max(0.0, center - margin), min(1.0, center + margin)
