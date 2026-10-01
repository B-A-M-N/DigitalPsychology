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
    intervention_credit_assignments: list[dict[str, Any]] = field(default_factory=list)
    intervention_strategy_observations: list[dict[str, Any]] = field(default_factory=list)
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
        credit_assignments, strategy_observations = collect_intervention_credit_assignments(event_list)
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
        feedback_events.extend({"type": "independent_intervention_outcome", **item}
                               for item in credit_assignments)
        feedback_events.extend({"type": "intervention_strategy_observation", **item}
                               for item in strategy_observations)
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
            intervention_credit_assignments=credit_assignments,
            intervention_strategy_observations=strategy_observations,
            rejection_reasons=rejection_reasons, events=feedback_events)


def collect_intervention_credit_assignments(
        events: Iterable[Any]
        ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Join authenticated host application receipts to assessed outcomes.

    These pairs are learning observations only. They are never promoted as a
    causal strategy without the existing baseline/intervention/holdout gates.
    """
    applications: dict[tuple[str, ...], Any] = {}
    outcomes: dict[tuple[str, ...], list[Any]] = {}

    def pair_key(event: Any, intervention_id: str) -> tuple[str, ...]:
        return tuple(str(getattr(event, name) or "") for name in (
            "namespace_id", "application_instance_id", "agent_instance_id",
            "session_id", "task_id", "attempt_id", "scope",
            "goal_contract_hash")) + (intervention_id,)

    for event in events:
        payload = getattr(event, "payload", None) or {}
        intervention_id = payload.get("behavior_intervention_id")
        if not isinstance(intervention_id, str) or not intervention_id:
            continue
        if (event.actor_id != "digitalpsychology_runtime"
                or not event.scope or not event.task_id or not event.attempt_id):
            continue
        if event.event_type == "behavior_intervention_applied":
            if (payload.get("behavior_intervention_status") != "applied"
                    or event.authority_source != "digitalpsychology_runtime"):
                continue
            applications[pair_key(event, intervention_id)] = event
        elif event.event_type == "behavior_intervention_assessed":
            if event.authority_source != "host_outcome_evaluator":
                continue
            outcomes.setdefault(pair_key(event, intervention_id), []).append(event)

    pairs: list[dict[str, Any]] = []
    for key, assessments in outcomes.items():
        intervention_id = key[-1]
        application = applications.get(key)
        if application is None:
            continue
        assessment = max(assessments, key=lambda event: (event.timestamp, event.event_id))
        payload = assessment.payload or {}
        status = payload.get("behavior_intervention_status")
        attribution = payload.get("behavior_intervention_attribution")
        evidence_refs = tuple(assessment.evidence_refs or ())
        independently_supported = (
            status in {"effective", "ineffective", "regressed"}
            and attribution == "supported" and bool(evidence_refs))
        applied_payload = application.payload or {}
        pair = {
            "intervention_id": intervention_id,
            "cause": applied_payload.get("behavior_intervention_cause"),
            "outcome": status,
            "attribution": attribution,
            "independently_supported": independently_supported,
            "target_criteria": list(
                applied_payload.get("behavior_intervention_target_criteria") or ()),
            "criterion_ids_progressed": list(
                payload.get("behavior_intervention_criterion_progress") or ()),
            "goal_progress": payload.get("behavior_intervention_goal_progress"),
            "comparison_id": payload.get("behavior_intervention_comparison_id"),
            "artifact_changed": payload.get("behavior_intervention_artifact_changed"),
            "repeated_approach": payload.get("behavior_intervention_repeated_approach"),
            "verification_quality": payload.get(
                "behavior_intervention_verification_quality", "unknown"),
            "task_id": assessment.task_id,
            "attempt_id": assessment.attempt_id,
            "agent_instance_id": assessment.agent_instance_id,
            "goal_contract_hash": assessment.goal_contract_hash,
            "evidence_refs": list(evidence_refs),
        }
        pairs.append(pair)

    groups: dict[tuple[str, tuple[str, ...]], list[dict[str, Any]]] = {}
    for pair in pairs:
        if not pair["independently_supported"]:
            continue
        key = (str(pair.get("cause") or "unknown"),
               tuple(sorted(pair.get("target_criteria") or ())))
        groups.setdefault(key, []).append(pair)
    observations = []
    for (cause, criteria), rows in sorted(groups.items()):
        counts: dict[str, int] = {}
        for row in rows:
            outcome = str(row["outcome"])
            counts[outcome] = counts.get(outcome, 0) + 1
        total = sum(counts.values())
        observations.append({
            "cause": cause,
            "target_criteria": list(criteria),
            "supported_trial_count": total,
            "effective_count": counts.get("effective", 0),
            "ineffective_count": counts.get("ineffective", 0),
            "regressed_count": counts.get("regressed", 0),
            "effective_rate": counts.get("effective", 0) / total if total else 0.0,
            "disposition": "observational_only",
            "promotion_requires": "independent baseline, intervention, and holdout cohorts",
        })
    pairs.sort(key=lambda item: (str(item.get("cause")), item["intervention_id"]))
    return pairs, observations


def candidate_id(candidate: Mapping[str, Any]) -> str:
    return str(candidate.get("profile_id") or candidate.get("profile_hash") or "unknown")
