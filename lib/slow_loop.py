"""Bounded Digital Psychology slow-loop orchestration.

The controller coordinates existing trusted primitives. It never decides
eligibility, mints evaluator evidence, or promotes without a verified receipt.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from .routing_lifecycle import RoutingLifecycleStore
from .routing_profiles import (BehavioralRoutingProfile, RoutingProfileError,
                               SharingDecisionAuthority, build_routing_pack,
                               derive_profiles, promote_profile, validate_profile)


@dataclass
class SlowLoopReport:
    """Typed feedback that keeps lifecycle and evaluation signals distinct."""

    status: str
    candidates: list[dict[str, Any]]
    promoted: list[str]
    rejected: list[dict[str, Any]]
    deployment_exposures: list[dict[str, Any]] = field(default_factory=list)
    observations_completed: list[dict[str, Any]] = field(default_factory=list)
    intervention_effectiveness: list[dict[str, Any]] = field(default_factory=list)
    regressions: list[dict[str, Any]] = field(default_factory=list)
    rejection_reasons: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)


class BoundedLearningController:
    """One bounded observe → derive → validate → promote cycle.

    Inputs are already authenticated events and host task contexts. The
    controller clusters independent trajectories, builds bounded candidate
    profiles, and only promotes a profile when its predeclared receipt passes.
    """

    def __init__(self, *, lifecycle: RoutingLifecycleStore,
                 minimum_samples: int = 10, minimum_effect: float = 0.10,
                 sharing_authority: SharingDecisionAuthority | None = None) -> None:
        if minimum_samples < 1:
            raise ValueError("slow-loop minimum_samples must be positive")
        self.lifecycle = lifecycle
        self.minimum_samples = minimum_samples
        self.minimum_effect = minimum_effect
        self.sharing_authority = sharing_authority

    def run(self, events: Iterable[Any], task_context: Mapping[str, Mapping[str, Any]]) -> SlowLoopReport:
        event_list = list(events)
        candidates = derive_profiles(
            event_list, task_context, minimum_samples=self.minimum_samples,
            minimum_effect=self.minimum_effect)
        promoted: list[str] = []
        rejected: list[dict[str, Any]] = []
        exposures: list[dict[str, Any]] = []
        observations: list[dict[str, Any]] = []
        effectiveness: list[dict[str, Any]] = []
        regressions: list[dict[str, Any]] = []
        rejection_reasons: list[dict[str, Any]] = []
        feedback_events: list[dict[str, Any]] = []
        for candidate in candidates:
            profile_id = str(candidate.get("profile_id") or "")
            evidence = dict(candidate.get("evidence") or {})
            exposure = {
                "profile_id": profile_id,
                "profile_hash": candidate.get("profile_hash"),
                "experiment_id": evidence.get("experiment_id"),
            }
            exposures.append(exposure)
            feedback_events.append({"type": "deployment_exposed", **exposure})
            observation = {
                "profile_id": profile_id,
                "event_ids": list(evidence.get("event_ids") or []),
                "trajectory_ids": list(evidence.get("trajectory_ids") or []),
                "completed": bool(evidence.get("event_ids") or evidence.get("trajectory_ids")),
            }
            observations.append(observation)
            feedback_events.append({"type": "observation_completed", **observation})
            try:
                receipt = dict(evidence.get("experiment_receipt") or {})
                if receipt.get("decision") != "pass":
                    raise RoutingProfileError("candidate lacks a passing experiment receipt")
                effect = {
                    "profile_id": profile_id,
                    "receipt_id": receipt.get("receipt_id"),
                    "baseline_rate": receipt.get("baseline_rate"),
                    "intervention_rate": receipt.get("intervention_rate"),
                    "effect": receipt.get("effect"),
                    "sample_counts": {
                        "baseline": receipt.get("baseline_count", 0),
                        "intervention": receipt.get("intervention_count", 0),
                        "holdout": receipt.get("holdout_count", 0),
                    },
                }
                effectiveness.append(effect)
                feedback_events.append({"type": "intervention_effectiveness", **effect})
                active = promote_profile(candidate, receipt, status="active")
                # Publish deployment without counting it as a trajectory.
                active = self.lifecycle.deploy(active, reason="validated experiment receipt")
                promoted.append(str(active["profile_hash"]))
                feedback_events.append({
                    "type": "deployment_published",
                    "profile_id": profile_id,
                    "profile_hash": active["profile_hash"],
                    "lifecycle_revision": active.get("lifecycle_revision"),
                })
            except (RoutingProfileError, TypeError, ValueError) as exc:
                reason = str(exc)
                rejected.append({"profile_id": candidate_id(candidate), "reason": reason})
                rejection_reasons.append({"profile_id": candidate_id(candidate), "reason": reason})
                feedback_events.append({
                    "type": "candidate_rejected",
                    "profile_id": candidate_id(candidate),
                    "reason": reason,
                })
            # Only actual holdout failures are regressions. Labeling every
            # candidate trajectory as one contradicted the receipt's own
            # semantics and poisoned the feedback plane.
            receipt = dict(evidence.get("experiment_receipt") or {})
            for trajectory_id in receipt.get("regressions", ()):
                regression = {"profile_id": profile_id, "trajectory_id": trajectory_id,
                              "receipt_id": receipt.get("receipt_id")}
                regressions.append(regression)
                feedback_events.append({"type": "holdout_regression_observed", **regression})
        return SlowLoopReport(
            status="completed" if not rejected else "completed_with_rejections",
            candidates=candidates, promoted=promoted, rejected=rejected,
            deployment_exposures=exposures, observations_completed=observations,
            intervention_effectiveness=effectiveness, regressions=regressions,
            rejection_reasons=rejection_reasons, events=feedback_events)


def candidate_id(candidate: Mapping[str, Any]) -> str:
    return str(candidate.get("profile_id") or candidate.get("profile_hash") or "unknown")
