"""Session-aware behavioral routing profiles.

DigitalPsychology owns characterization and experiment evidence.  This module
only emits bounded, machine-readable preferences; CognitiveFrameWorks remains
the authority that decides whether a preference is safe and applicable.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

try:
    import jsonschema
except ImportError:  # pragma: no cover
    jsonschema = None


PROFILE_VERSION = "1.0.0"
ROUTING_STATUSES = {"candidate", "validated", "active", "stale", "rolled_back"}
ROUTING_EXPERIMENT_VERSION = "1.0.0"


class RoutingProfileError(ValueError):
    pass


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def profile_semantic_hash(profile: Mapping[str, Any]) -> str:
    data = dict(profile)
    data.pop("profile_hash", None)
    data.pop("created_at", None)
    # Trial observations and receipts are mutable evidence, not the proposed
    # intervention.  The hash is therefore stable before the experiment runs.
    evidence = dict(data.get("evidence") or {})
    stable_evidence = {"experiment_id": evidence.get("experiment_id")}
    if "candidate_adjustment" in evidence:
        stable_evidence["candidate_adjustment"] = {
            key: value for key, value in dict(evidence["candidate_adjustment"]).items()
            if key != "evidence_refs"
        }
    data["evidence"] = {key: value for key, value in stable_evidence.items()
                         if value is not None}
    data["observations"] = []
    data["routing_adjustments"] = [
        {key: value for key, value in dict(adjustment).items()
         if key != "evidence_refs"}
        for adjustment in data.get("routing_adjustments", [])
    ]
    return hashlib.sha256(_canonical(data).encode("utf-8")).hexdigest()


def _criterion_hash(criterion: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(dict(criterion)).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RoutingExperimentPlan:
    experiment_id: str
    candidate_profile_hash: str
    candidate_profile_id: str
    candidate_adjustment: Mapping[str, Any]
    eligibility: Mapping[str, Any]
    assignment_algorithm: str
    canary_fraction: float
    target_evaluator: str
    control_policy_identity: str
    treatment_policy_identity: str
    holdout_requirements: Mapping[str, Any]
    plan_version: str = ROUTING_EXPERIMENT_VERSION

    def to_dict(self) -> dict[str, Any]:
        data = {
            "plan_version": self.plan_version,
            "experiment_id": self.experiment_id,
            "candidate_profile_hash": self.candidate_profile_hash,
            "candidate_profile_id": self.candidate_profile_id,
            "candidate_adjustment": dict(self.candidate_adjustment),
            "eligibility": dict(self.eligibility),
            "assignment_algorithm": self.assignment_algorithm,
            "canary_fraction": self.canary_fraction,
            "target_evaluator": self.target_evaluator,
            "control_policy_identity": self.control_policy_identity,
            "treatment_policy_identity": self.treatment_policy_identity,
            "holdout_requirements": dict(self.holdout_requirements),
        }
        data["plan_hash"] = hashlib.sha256(_canonical(data).encode("utf-8")).hexdigest()
        return data


def build_experiment_plan(*, experiment_id: str, candidate_profile_hash: str,
                          candidate_profile_id: str = "",
                          candidate_adjustment: Mapping[str, Any],
                          eligibility: Mapping[str, Any],
                          target_evaluator: str,
                          control_policy_identity: str,
                          treatment_policy_identity: str,
                          holdout_requirements: Mapping[str, Any] | None = None,
                          canary_fraction: float = 0.0) -> dict[str, Any]:
    if not experiment_id or not candidate_profile_hash:
        raise RoutingProfileError("routing experiment requires an id and candidate hash")
    if not 0 <= canary_fraction <= 1:
        raise RoutingProfileError("routing experiment canary_fraction must be between 0 and 1")
    return RoutingExperimentPlan(
        experiment_id=experiment_id,
        candidate_profile_hash=candidate_profile_hash,
        candidate_profile_id=candidate_profile_id,
        candidate_adjustment=dict(candidate_adjustment),
        eligibility=dict(eligibility),
        assignment_algorithm="sha256(experiment_id + trial_identity)@v1",
        canary_fraction=canary_fraction,
        target_evaluator=target_evaluator,
        control_policy_identity=control_policy_identity,
        treatment_policy_identity=treatment_policy_identity,
        holdout_requirements=dict(holdout_requirements or {}),
    ).to_dict()


def assign_routing_cohort(plan: Mapping[str, Any], trial_identity: str) -> str | None:
    """Host-independent deterministic assignment for an eligible trial."""
    if not condition_matches(plan.get("eligibility") or {}, {"trial_identity": trial_identity}):
        # Eligibility fields are normally checked by the host; an explicit
        # identity condition is the only condition this pure helper evaluates.
        condition = plan.get("eligibility") or {}
        if condition.get("field") == "trial_identity":
            return None
    fraction = float(plan.get("canary_fraction", 0.0) or 0.0)
    if fraction <= 0:
        return "treatment"
    digest = hashlib.sha256(
        f"{plan.get('experiment_id')}:{trial_identity}".encode("utf-8")).hexdigest()
    percentile = int(digest[:8], 16) / float(0xFFFFFFFF)
    return "treatment" if percentile < fraction else "control"


def _wilson(rate: float, samples: int) -> tuple[float, float]:
    if not samples:
        return 0.0, 0.0
    z = 1.96
    denominator = 1 + z * z / samples
    centre = (rate + z * z / (2 * samples)) / denominator
    margin = z * (rate * (1 - rate) / samples + z * z / (4 * samples * samples)) ** 0.5 / denominator
    return max(0.0, centre - margin), min(1.0, centre + margin)


def build_routing_experiment_receipt(plan: Mapping[str, Any], trials: Iterable[Mapping[str, Any]],
                                     *, minimum_samples: int = 10,
                                     minimum_effect: float = 0.10,
                                     evaluator_version: str = "routing-outcome-v2") -> dict[str, Any]:
    """Evaluate independent trajectories and return a verifiable receipt.

    A trial is one unique (agent, session, task, attempt) trajectory.  Route
    log lines are evidence references inside a trial; they are never samples.
    """
    trial_list = [dict(item) for item in trials]
    unique: dict[str, dict[str, Any]] = {}
    for trial in trial_list:
        trajectory_id = str(trial.get("trajectory_id") or "")
        cohort = trial.get("cohort")
        if not trajectory_id or cohort not in {"control", "treatment"}:
            raise RoutingProfileError("receipt trials require trajectory_id and control/treatment cohort")
        if trajectory_id in unique:
            prior = unique[trajectory_id]
            if prior.get("outcome") != trial.get("outcome") or prior.get("cohort") != cohort:
                raise RoutingProfileError("one trajectory has conflicting route outcomes")
            prior.setdefault("event_ids", []).extend(trial.get("event_ids") or [])
            continue
        if trial.get("candidate_profile_hash") != plan.get("candidate_profile_hash"):
            raise RoutingProfileError("trial is not bound to the experiment candidate")
        expected_applied = cohort == "treatment"
        if bool(trial.get("candidate_adjustment_applied")) != expected_applied:
            raise RoutingProfileError("trial cohort does not match applied candidate adjustment")
        if not trial.get("route_decision_id") or not trial.get("evaluator_id"):
            raise RoutingProfileError("trial requires a bound route decision and evaluator")
        if trial.get("outcome_source") != f"evaluator:{plan.get('target_evaluator')}":
            raise RoutingProfileError("route outcome was not produced by the planned evaluator")
        unique[trajectory_id] = trial
    trials = list(unique.values())
    comparison_hashes = {str(t.get("comparison_context_hash") or "") for t in trials}
    if not trials or not comparison_hashes or "" in comparison_hashes or len(comparison_hashes) != 1:
        raise RoutingProfileError("control/treatment trials require one matched comparison context")
    control = [t for t in trials if t["cohort"] == "control"]
    treatment = [t for t in trials if t["cohort"] == "treatment"]
    control_bad = sum(str(t.get("outcome")) in {"bad", "fail", "failed"} for t in control)
    treatment_bad = sum(str(t.get("outcome")) in {"bad", "fail", "failed"} for t in treatment)
    control_rate = control_bad / len(control) if control else 0.0
    treatment_rate = treatment_bad / len(treatment) if treatment else 0.0
    effect = control_rate - treatment_rate
    control_low, control_high = _wilson(control_rate, len(control))
    treatment_low, treatment_high = _wilson(treatment_rate, len(treatment))
    effect_low, effect_high = control_low - treatment_high, control_high - treatment_low
    holdout = dict(plan.get("holdout_requirements") or {})
    regressions = list(holdout.get("regressions") or [])
    decision = "pass" if (
        len(control) >= minimum_samples and len(treatment) >= minimum_samples
        and effect >= minimum_effect and effect_low >= 0.0 and not regressions
    ) else "fail"
    source_hash = hashlib.sha256(_canonical(trials).encode("utf-8")).hexdigest()
    receipt: dict[str, Any] = {
        "receipt_id": f"routing-rcpt-{hashlib.sha256((str(plan.get('experiment_id')) + source_hash).encode()).hexdigest()[:20]}",
        "kind": "routing_experiment",
        "receipt_version": ROUTING_EXPERIMENT_VERSION,
        "experiment_id": plan.get("experiment_id"),
        "experiment_plan_hash": plan.get("plan_hash"),
        "profile_candidate_hash": plan.get("candidate_profile_hash"),
        "candidate_adjustment": dict(plan.get("candidate_adjustment") or {}),
        "control_trajectory_ids": sorted(t["trajectory_id"] for t in control),
        "treatment_trajectory_ids": sorted(t["trajectory_id"] for t in treatment),
        "policy_hashes": sorted({str(t.get("policy_hash")) for t in trials}),
        "comparison_context_hash": next(iter(comparison_hashes)),
        "evaluator_id": plan.get("target_evaluator"),
        "evaluator_version": evaluator_version,
        "evaluator_hash": hashlib.sha256(str(evaluator_version).encode()).hexdigest(),
        "control_bad_rate": round(control_rate, 6),
        "treatment_bad_rate": round(treatment_rate, 6),
        "effect": round(effect, 6),
        "confidence_interval": [round(effect_low, 6), round(effect_high, 6)],
        "minimum_samples": minimum_samples,
        "minimum_effect": minimum_effect,
        "regressions": regressions,
        "source_evidence_hash": source_hash,
        "decision": decision,
    }
    receipt["receipt_hash"] = hashlib.sha256(_canonical(receipt).encode("utf-8")).hexdigest()
    return receipt


def routing_validation_receipt(profile: Mapping[str, Any], receipt_id: str) -> dict[str, Any]:
    raise RoutingProfileError(
        "routing receipts must be generated from a predeclared experiment; "
        "promotion cannot manufacture evidence")


def promote_profile(profile: Mapping[str, Any], receipt: Mapping[str, Any],
                    *, status: str = "active") -> dict[str, Any]:
    if status not in {"validated", "active"}:
        raise RoutingProfileError("routing profile promotion requires validated or active status")
    if not isinstance(receipt, Mapping):
        raise RoutingProfileError("promotion requires a pre-existing routing experiment receipt")
    receipt = dict(receipt)
    receipt_body = dict(receipt)
    receipt_body.pop("receipt_hash", None)
    if receipt.get("receipt_hash") != hashlib.sha256(_canonical(receipt_body).encode("utf-8")).hexdigest():
        raise RoutingProfileError("routing experiment receipt hash mismatch")
    if receipt.get("kind") != "routing_experiment" or receipt.get("decision") != "pass":
        raise RoutingProfileError("only passing routing experiment receipts may promote")
    promoted = json.loads(json.dumps(dict(profile)))
    promoted["status"] = "candidate"
    if receipt.get("profile_candidate_hash") != profile_semantic_hash(promoted):
        raise RoutingProfileError("routing receipt is bound to a different candidate")
    evidence = dict(promoted.get("evidence") or {})
    if evidence.get("source_hash") != receipt.get("source_evidence_hash"):
        raise RoutingProfileError("routing receipt evidence does not match candidate evidence")
    if evidence.get("experiment_id") != receipt.get("experiment_id"):
        raise RoutingProfileError("routing receipt experiment is not bound to candidate")
    promoted["status"] = status
    promoted.setdefault("evidence", {})["receipt_ref"] = receipt["receipt_id"]
    promoted["evidence"]["receipt"] = receipt
    promoted["profile_hash"] = profile_semantic_hash(promoted)
    validate_profile(promoted, require_deployable=True)
    return promoted


def _schema() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[1] / "schemas" / "behavioral-routing-profile.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


def validate_profile(profile: Mapping[str, Any], *, require_deployable: bool = False) -> None:
    if jsonschema is None:
        raise RoutingProfileError("jsonschema is required for routing profile validation")
    errors = sorted(jsonschema.Draft202012Validator(_schema()).iter_errors(dict(profile)),
                    key=lambda error: list(error.path))
    if errors:
        raise RoutingProfileError(errors[0].message)
    if profile.get("profile_hash") != profile_semantic_hash(profile):
        raise RoutingProfileError("routing profile semantic hash mismatch")
    if require_deployable and profile.get("status") not in {"validated", "active"}:
        raise RoutingProfileError("only validated or active routing profiles may deploy")
    if require_deployable:
        evidence = profile.get("evidence") or {}
        receipt = evidence.get("receipt") or {}
        if not evidence.get("receipt_ref") or not receipt:
            raise RoutingProfileError("deployable routing profile requires a validation receipt")
        if (receipt.get("receipt_id") != evidence.get("receipt_ref")
                or receipt.get("kind") != "routing_experiment"
                or receipt.get("decision") != "pass"):
            raise RoutingProfileError("routing profile validation receipt is not bound to the profile")
        expected_receipt_hash = dict(receipt)
        expected_receipt_hash.pop("receipt_hash", None)
        if receipt.get("receipt_hash") != hashlib.sha256(
                _canonical(expected_receipt_hash).encode("utf-8")).hexdigest():
            raise RoutingProfileError("routing profile validation receipt hash mismatch")
        candidate = json.loads(json.dumps(dict(profile)))
        candidate["status"] = "candidate"
        candidate.pop("profile_hash", None)
        candidate_evidence = dict(candidate.get("evidence") or {})
        candidate_evidence.pop("receipt_ref", None)
        candidate_evidence.pop("receipt", None)
        candidate["evidence"] = candidate_evidence
        if receipt.get("profile_candidate_hash") != profile_semantic_hash(candidate):
            raise RoutingProfileError("routing profile receipt is bound to a different candidate")
        if receipt.get("source_evidence_hash") != candidate_evidence.get("source_hash"):
            raise RoutingProfileError("routing profile receipt evidence mismatch")
        if receipt.get("experiment_id") != candidate_evidence.get("experiment_id"):
            raise RoutingProfileError("routing profile receipt experiment mismatch")
    expires = profile.get("expires_at")
    if expires:
        now = datetime.now(timezone.utc)
        expiry = datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
        if expiry <= now and profile.get("status") in {"validated", "active"}:
            raise RoutingProfileError("routing profile has expired")


@dataclass(frozen=True)
class BehavioralRoutingProfile:
    profile_id: str
    status: str
    subject: Mapping[str, Any]
    context: Mapping[str, Any]
    observations: tuple[Mapping[str, Any], ...]
    routing_adjustments: tuple[Mapping[str, Any], ...]
    evidence: Mapping[str, Any]
    criterion: Mapping[str, Any]
    created_at: str
    expires_at: Optional[str] = None
    profile_version: str = PROFILE_VERSION
    profile_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = {
            "profile_version": self.profile_version,
            "profile_id": self.profile_id,
            "status": self.status,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "subject": dict(self.subject),
            "context": dict(self.context),
            "observations": [dict(item) for item in self.observations],
            "routing_adjustments": [dict(item) for item in self.routing_adjustments],
            "evidence": dict(self.evidence),
            "criterion": dict(self.criterion),
        }
        data["profile_hash"] = profile_semantic_hash(data)
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], *, require_deployable: bool = False) -> "BehavioralRoutingProfile":
        validate_profile(data, require_deployable=require_deployable)
        return cls(
            profile_id=str(data["profile_id"]), status=str(data["status"]),
            subject=dict(data["subject"]), context=dict(data.get("context") or {}),
            observations=tuple(dict(item) for item in data.get("observations", [])),
            routing_adjustments=tuple(dict(item) for item in data.get("routing_adjustments", [])),
            evidence=dict(data["evidence"]), criterion=dict(data["criterion"]),
            created_at=str(data.get("created_at") or ""),
            expires_at=data.get("expires_at"), profile_version=str(data["profile_version"]),
            profile_hash=str(data["profile_hash"]))


def _match_value(expected: Any, actual: Any) -> bool:
    if expected in (None, "", [], ()):  # absent scope means wildcard
        return True
    if isinstance(expected, (list, tuple, set)):
        actual_values = set(actual if isinstance(actual, (list, tuple, set)) else [actual])
        return bool(actual_values & set(expected))
    return expected == actual


def condition_matches(condition: Mapping[str, Any], context: Mapping[str, Any]) -> bool:
    if not condition:
        return True
    if "all" in condition:
        return all(condition_matches(item, context) for item in condition["all"])
    if "any" in condition:
        return any(condition_matches(item, context) for item in condition["any"])
    field = condition.get("field")
    if not isinstance(field, str):
        return False
    if "contains" in condition:
        value = context.get(field, ())
        return condition["contains"] in (value if isinstance(value, (list, tuple, set)) else [value])
    if "equals" in condition:
        return context.get(field) == condition["equals"]
    return False


def applicable_adjustments(profile: BehavioralRoutingProfile,
                           context: Mapping[str, Any]) -> list[dict[str, Any]]:
    if not _match_value(profile.subject.get("agent_instance_id"), context.get("agent_instance_id")):
        return []
    for field in ("model", "harness"):
        if not _match_value(profile.subject.get(field), context.get(field)):
            return []
    for field, expected in profile.context.items():
        if not _match_value(expected, context.get(field)):
            return []
    return [dict(item) for item in profile.routing_adjustments
            if condition_matches(item.get("condition") or {}, context)]


def build_profile(*, profile_id: str, subject: Mapping[str, Any], context: Mapping[str, Any],
                  observations: Iterable[Mapping[str, Any]],
                  routing_adjustments: Iterable[Mapping[str, Any]],
                  evidence: Mapping[str, Any], criterion: Mapping[str, Any],
                  status: str = "candidate", expires_at: Optional[str] = None) -> dict[str, Any]:
    if status not in ROUTING_STATUSES:
        raise RoutingProfileError(f"invalid routing profile status {status!r}")
    if status in {"validated", "active"}:
        raise RoutingProfileError("build_profile creates candidates; use promote_profile with a receipt")
    data = {
        "profile_version": PROFILE_VERSION,
        "profile_id": profile_id,
        "status": status,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "expires_at": expires_at,
        "subject": dict(subject),
        "context": dict(context),
        "observations": [dict(item) for item in observations],
        "routing_adjustments": [dict(item) for item in routing_adjustments],
        "evidence": dict(evidence),
        "criterion": dict(criterion),
    }
    data["profile_hash"] = profile_semantic_hash(data)
    validate_profile(data)
    return data


def derive_profiles(events: Iterable[Any], task_context: Mapping[str, Mapping[str, Any]],
                    *, minimum_samples: int = 10, minimum_effect: float = 0.10,
                    evaluator_version: str = "routing-outcome-v2") -> list[dict[str, Any]]:
    """Derive candidates from host-bound, evaluator-produced trial results.

    Route events are clustered into independent trajectories before counting.
    A caller cannot create a promotion-grade sample by repeating labels in one
    session, and a manual ``good``/``bad`` label without a bound evaluator and
    predeclared experiment is ignored.
    """
    from .trajectories import build_trajectories

    event_list = list(events)
    _episodes, tasks, sessions, _agents = build_trajectories(event_list)
    event_to_session: dict[str, str] = {}
    for session in sessions:
        for event in session.events:
            event_to_session[event.event_id] = session.trajectory_id
    grouped: dict[tuple[Any, ...], dict[str, dict[str, Any]]] = {}
    for event in event_list:
        if event.event_type != "route_outcome":
            continue
        payload = event.payload or {}
        route = payload.get("route")
        route_type = payload.get("route_type")
        outcome = payload.get("outcome")
        if not route or not route_type or outcome not in {"bad", "good", "fail", "failed", "pass"}:
            continue
        trajectory_id = event_to_session.get(event.event_id)
        if not trajectory_id or not payload.get("route_decision_id"):
            continue
        context = dict(task_context.get(
            f"{event.session_id}:{event.task_id}:{event.attempt_id}",
            task_context.get(f"{event.session_id}:{event.task_id}", {})))
        plan = context.get("routing_experiment_plan")
        cohort = context.get("routing_cohort")
        if not isinstance(plan, Mapping) or cohort not in {"control", "treatment"}:
            continue
        if payload.get("outcome_source") != f"evaluator:{plan.get('target_evaluator')}":
            continue
        if payload.get("candidate_profile_hash") != plan.get("candidate_profile_hash"):
            continue
        if bool(payload.get("candidate_adjustment_applied")) != (cohort == "treatment"):
            continue
        comparison_hash = context.get("comparison_context_hash")
        if not comparison_hash:
            continue
        if cohort not in {"control", "treatment"}:
            continue
        statework_versions_value = context.get("statework_versions") or {}
        statework_versions_text = (statework_versions_value if isinstance(statework_versions_value, str)
                                   else repr(statework_versions_value))
        key = (context.get("agent_instance_id", event.agent_instance_id),
               context.get("model"), context.get("harness"),
               context.get("task_family") or context.get("task_shape"),
               tuple(sorted(context.get("domain_tags") or [])),
               context.get("phase"), context.get("environment"),
               context.get("toolset"), statework_versions_text,
               context.get("framework_version"), context.get("guard_pack_hash"),
               context.get("role"), comparison_hash, plan.get("plan_hash"),
               route_type, route)
        bucket = grouped.setdefault(key, {"control": {}, "treatment": {}, "plan": plan})
        trial = bucket[cohort].setdefault(trajectory_id, {
            "trajectory_id": trajectory_id,
            "cohort": cohort,
            "candidate_profile_hash": plan.get("candidate_profile_hash"),
            "candidate_adjustment_applied": cohort == "treatment",
            "comparison_context_hash": comparison_hash,
            "route_decision_id": payload.get("route_decision_id"),
            "evaluator_id": payload.get("evaluator_id") or plan.get("target_evaluator"),
            "outcome_source": payload.get("outcome_source"),
            "outcome": "good",
            "policy_hash": payload.get("policy_hash") or context.get("policy_hash"),
            "event_ids": [],
        })
        trial["event_ids"].append(event.event_id)
        if outcome in {"bad", "fail", "failed"}:
            trial["outcome"] = "bad"

    profiles: list[dict[str, Any]] = []
    for key, cohorts in sorted(grouped.items(), key=lambda item: repr(item[0])):
        plan = cohorts["plan"]
        control = list(cohorts["control"].values())
        treatment = list(cohorts["treatment"].values())
        if len(control) < minimum_samples or len(treatment) < minimum_samples:
            continue
        trials = control + treatment
        agent_instance_id, model, harness, task_family, domains, phase, environment, toolset, statework_versions, framework_version, guard_pack_hash, role, comparison_hash, _plan_hash, route_type, route = key
        source_hash = hashlib.sha256(_canonical(trials).encode("utf-8")).hexdigest()
        context = {"task_family": task_family, "task_shape": task_family,
                   "domain_tags": list(domains),
                   "phase": phase, "environment": environment, "toolset": toolset,
                   "statework_versions": statework_versions,
                   "framework_version": framework_version,
                   "guard_pack_hash": guard_pack_hash}
        subject = {"agent_instance_id": agent_instance_id or "unknown",
                   "model": model, "harness": harness}
        adjustment = dict(plan.get("candidate_adjustment") or {})
        adjustment = {
            **adjustment,
            "route_type": route_type,
            "route": route,
            "evidence_refs": sorted({event_id for trial in trials for event_id in trial["event_ids"]}),
        }
        profile = build_profile(
            profile_id=str(plan.get("candidate_profile_id") or
                          f"routing-{hashlib.sha256(repr(key).encode()).hexdigest()[:16]}"),
            subject=subject, context=context,
            observations=[{"route": route, "route_type": route_type,
                           "control_samples": len(control),
                           "treatment_samples": len(treatment),
                           "comparison_context_hash": comparison_hash}],
            routing_adjustments=[adjustment],
            evidence={"event_ids": sorted({event_id for trial in trials for event_id in trial["event_ids"]}),
                      "trajectory_ids": sorted(trial["trajectory_id"] for trial in trials),
                      "source_hash": source_hash,
                      "experiment_id": plan.get("experiment_id"),
                      "experiment_plan_hash": plan.get("plan_hash"),
                      "candidate_adjustment": adjustment,
                      "receipt_ref": None},
            criterion={"evaluator_version": evaluator_version,
                       "minimum_samples": minimum_samples,
                       "minimum_effect": minimum_effect,
                       "confidence_rule": "independent trajectory effect lower confidence bound is non-negative"},
            status="candidate")
        if profile["profile_hash"] != plan.get("candidate_profile_hash"):
            raise RoutingProfileError(
                "experiment plan candidate hash does not match derived candidate "
                f"({profile['profile_hash']} != {plan.get('candidate_profile_hash')}); "
                f"context={profile['context']!r}; criterion={profile['criterion']!r}")
        receipt = build_routing_experiment_receipt(
            plan, trials, minimum_samples=minimum_samples,
            minimum_effect=minimum_effect, evaluator_version=evaluator_version)
        profile["evidence"]["experiment_receipt"] = receipt
        profiles.append(profile)
    return profiles
