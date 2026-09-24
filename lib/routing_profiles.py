"""Session-aware behavioral routing profiles.

DigitalPsychology owns characterization and experiment evidence.  This module
only emits bounded, machine-readable preferences; CognitiveFrameWorks remains
the authority that decides whether a preference is safe and applicable.
"""
from __future__ import annotations

import hashlib
import json
import os
from importlib.resources import files as resource_files
from dataclasses import dataclass, field
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
ROUTING_PACK_VERSION = "1.0.0"
ROUTING_EVALUATOR_VERSION = "1.0.0"


class RoutingProfileError(ValueError):
    pass


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


_CANONICAL_ROUTE_OUTCOMES = {"PASS", "FAIL", "NOT_APPLICABLE"}
_ROUTE_OUTCOME_ALIASES = {
    "PASS": "PASS", "GOOD": "PASS", "OK": "PASS", "SUCCESS": "PASS",
    "FAIL": "FAIL", "FAILED": "FAIL", "BAD": "FAIL",
    "NOT_APPLICABLE": "NOT_APPLICABLE", "N/A": "NOT_APPLICABLE",
}

def normalize_route_outcome(value: Any) -> str:
    if not isinstance(value, str):
        raise RoutingProfileError("route outcome must be a string")
    normalized = _ROUTE_OUTCOME_ALIASES.get(value.strip().upper())
    if normalized is None:
        raise RoutingProfileError(f"unknown route outcome {value!r}")
    return normalized


def routing_pack_semantic_hash(pack: Mapping[str, Any]) -> str:
    body = dict(pack)
    body.pop("semantic_hash", None)
    return hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()


def build_routing_pack(profiles: Iterable[Mapping[str, Any]], *,
                       source_revision: str = "") -> dict[str, Any]:
    payload: dict[str, Any] = {
        "routing_pack_version": ROUTING_PACK_VERSION,
        "source_revision": source_revision or None,
        "profiles": [dict(profile) for profile in profiles],
    }
    payload["semantic_hash"] = routing_pack_semantic_hash(payload)
    return payload


def profile_semantic_hash(profile: Mapping[str, Any]) -> str:
    data = dict(profile)
    data.pop("profile_hash", None)
    data.pop("created_at", None)
    data.pop("lifecycle_revision", None)
    data.pop("lifecycle_status", None)
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
    target_evaluator_version: str
    target_evaluator_hash: str
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
            "target_evaluator_version": self.target_evaluator_version,
            "target_evaluator_hash": self.target_evaluator_hash,
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
                          target_evaluator_version: str = ROUTING_EVALUATOR_VERSION,
                          target_evaluator_hash: str = "",
                          holdout_requirements: Mapping[str, Any] | None = None,
                          canary_fraction: float = 0.0) -> dict[str, Any]:
    if not experiment_id or not candidate_profile_hash:
        raise RoutingProfileError("routing experiment requires an id and candidate hash")
    if not 0 <= canary_fraction <= 1:
        raise RoutingProfileError("routing experiment canary_fraction must be between 0 and 1")
    target_evaluator_hash = target_evaluator_hash or hashlib.sha256(
        f"{target_evaluator}@{target_evaluator_version}".encode("utf-8")).hexdigest()
    holdout = dict(holdout_requirements or {})
    holdout.setdefault("evaluators", [f"{target_evaluator}@{target_evaluator_version}"])
    holdout.setdefault("minimum_samples", 1)
    holdout.setdefault("max_not_applicable_fraction", 0.25)
    plan = RoutingExperimentPlan(
        experiment_id=experiment_id,
        candidate_profile_hash=candidate_profile_hash,
        candidate_profile_id=candidate_profile_id or experiment_id,
        candidate_adjustment=dict(candidate_adjustment),
        eligibility=dict(eligibility),
        assignment_algorithm="sha256(experiment_id + trial_identity)@v1",
        canary_fraction=canary_fraction,
        target_evaluator=target_evaluator,
        target_evaluator_version=target_evaluator_version,
        target_evaluator_hash=target_evaluator_hash,
        control_policy_identity=control_policy_identity,
        treatment_policy_identity=treatment_policy_identity,
        holdout_requirements=holdout,
    ).to_dict()
    validate_experiment_plan(plan)
    return plan


def _validate_experiment_condition(condition: Any) -> None:
    if not isinstance(condition, Mapping) or not condition:
        raise RoutingProfileError("routing experiment eligibility must be a non-empty condition")
    if "all" in condition or "any" in condition:
        key = "all" if "all" in condition else "any"
        items = condition.get(key)
        if not isinstance(items, list) or not items:
            raise RoutingProfileError("routing experiment condition groups must be non-empty lists")
        if set(condition) != {key}:
            raise RoutingProfileError("routing experiment condition group has extra fields")
        for item in items:
            _validate_experiment_condition(item)
        return
    if set(condition) - {"field", "equals", "contains"}:
        raise RoutingProfileError("routing experiment condition has unknown fields")
    if not isinstance(condition.get("field"), str) or not condition["field"]:
        raise RoutingProfileError("routing experiment condition requires a field")
    if ("equals" in condition) == ("contains" in condition):
        raise RoutingProfileError("routing experiment condition requires exactly one matcher")


def validate_experiment_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
    data = dict(plan)
    try:
        import jsonschema
        jsonschema.Draft202012Validator(_routing_plan_schema()).validate(data)
    except ImportError as exc:
        raise RoutingProfileError("jsonschema is required for routing plan validation") from exc
    except jsonschema.ValidationError as exc:
        raise RoutingProfileError(f"routing experiment plan schema mismatch: {exc.message}") from exc
    required = (
        "plan_version", "experiment_id", "candidate_profile_hash", "candidate_profile_id",
        "candidate_adjustment", "eligibility", "assignment_algorithm", "canary_fraction",
        "target_evaluator", "target_evaluator_version", "target_evaluator_hash",
        "control_policy_identity", "treatment_policy_identity", "holdout_requirements",
        "plan_hash",
    )
    if any(key not in data or (not data.get(key) and key != "canary_fraction") for key in required):
        raise RoutingProfileError("routing experiment plan is incomplete")
    if data.get("plan_version") != ROUTING_EXPERIMENT_VERSION:
        raise RoutingProfileError("unsupported routing experiment plan version")
    if data.get("assignment_algorithm") != "sha256(experiment_id + trial_identity)@v1":
        raise RoutingProfileError("unsupported routing experiment assignment algorithm")
    fraction = data.get("canary_fraction")
    if not isinstance(fraction, (int, float)) or not 0 <= fraction <= 1:
        raise RoutingProfileError("routing experiment canary_fraction must be between 0 and 1")
    if data.get("control_policy_identity") in {"static-control", "control"}:
        raise RoutingProfileError("control policy identity must be host-prepared")
    if data.get("treatment_policy_identity") in {"candidate-treatment", "treatment"}:
        raise RoutingProfileError("treatment policy identity must be host-prepared")
    expected_evaluator_hash = hashlib.sha256(
        f"{data['target_evaluator']}@{data['target_evaluator_version']}".encode("utf-8")
    ).hexdigest()
    if data.get("target_evaluator_hash") != expected_evaluator_hash:
        raise RoutingProfileError("routing experiment evaluator hash mismatch")
    _validate_experiment_condition(data.get("eligibility"))
    holdout = data.get("holdout_requirements")
    if not isinstance(holdout, Mapping):
        raise RoutingProfileError("routing experiment requires holdout requirements")
    evaluators = holdout.get("evaluators")
    if not isinstance(evaluators, list) or not evaluators:
        raise RoutingProfileError("routing experiment requires versioned holdout evaluators")
    if int(holdout.get("minimum_samples", 0) or 0) < 1:
        raise RoutingProfileError("routing experiment requires a positive holdout sample minimum")
    expected_hash = dict(data)
    expected_hash.pop("plan_hash", None)
    if data.get("plan_hash") != hashlib.sha256(_canonical(expected_hash).encode("utf-8")).hexdigest():
        raise RoutingProfileError("routing experiment plan hash mismatch")
    return data


def _routing_plan_schema() -> dict[str, Any]:
    for package in ("digital_psychology", "lib"):
        try:
            resource = resource_files(package).joinpath("schemas/routing-experiment-plan.schema.json")
            if resource.is_file():
                return json.loads(resource.read_text(encoding="utf-8"))
        except (ModuleNotFoundError, FileNotFoundError):
            continue
    path = Path(__file__).resolve().parents[1] / "schemas" / "routing-experiment-plan.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


def assign_routing_cohort(plan: Mapping[str, Any], trial_identity: str,
                          task_context: Optional[Mapping[str, Any]] = None) -> str | None:
    """Assign only after the trusted host context proves eligibility."""
    plan = validate_experiment_plan(plan)
    context = dict(task_context or {})
    context.setdefault("trial_identity", trial_identity)
    if not condition_matches(plan["eligibility"], context):
        return None
    holdout_ids = set((plan.get("holdout_requirements") or {}).get("holdout_task_ids") or ())
    if context.get("task_id") in holdout_ids:
        return "holdout"
    fraction = float(plan.get("canary_fraction", 0.0) or 0.0)
    if fraction <= 0:
        # A zero canary fraction means no experiment cohort. Full deployment
        # must use an explicit rollout mode, not an experiment with no control.
        return None
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
                                     evaluator_version: str = ROUTING_EVALUATOR_VERSION) -> dict[str, Any]:
    """Evaluate independent trajectories and return a verifiable receipt.

    A trial is one unique (agent, session, task, attempt) trajectory.  Route
    log lines are evidence references inside a trial; they are never samples.
    """
    plan = validate_experiment_plan(plan)
    if evaluator_version != plan.get("target_evaluator_version"):
        raise RoutingProfileError("receipt evaluator version is not pinned by the experiment plan")
    holdout_requirements = dict(plan.get("holdout_requirements") or {})
    trial_list = [dict(item) for item in trials]
    unique: dict[str, dict[str, Any]] = {}
    for trial in trial_list:
        trajectory_id = str(trial.get("trajectory_id") or "")
        cohort = trial.get("cohort")
        if not trajectory_id or cohort not in {"control", "treatment", "holdout"}:
            raise RoutingProfileError("receipt trials require trajectory_id and a declared cohort")
        if trajectory_id in unique:
            prior = unique[trajectory_id]
            normalized = normalize_route_outcome(trial.get("outcome"))
            if prior.get("outcome") != normalized or prior.get("cohort") != cohort:
                raise RoutingProfileError("one trajectory has conflicting route outcomes")
            prior.setdefault("event_ids", []).extend(trial.get("event_ids") or [])
            continue
        if trial.get("experiment_plan_hash") != plan.get("plan_hash"):
            raise RoutingProfileError("trial is not bound to the exact experiment plan")
        if trial.get("candidate_profile_hash") != plan.get("candidate_profile_hash"):
            raise RoutingProfileError("trial is not bound to the experiment candidate")
        expected_applied = cohort == "treatment"
        if bool(trial.get("candidate_adjustment_applied")) != expected_applied:
            raise RoutingProfileError("trial cohort does not match applied candidate adjustment")
        expected_policy = {
            "control": plan["control_policy_identity"],
            "treatment": plan["treatment_policy_identity"],
            "holdout": plan["control_policy_identity"],
        }[cohort]
        if trial.get("policy_identity") != expected_policy:
            raise RoutingProfileError("trial is not bound to its cohort policy identity")
        if (not trial.get("route_decision_id") or
                trial.get("evaluator_id") != plan.get("target_evaluator") or
                trial.get("evaluator_version") != plan.get("target_evaluator_version") or
                trial.get("evaluator_hash") != plan.get("target_evaluator_hash")):
            raise RoutingProfileError("trial evaluator is not exactly pinned by the plan")
        if trial.get("outcome_source") != f"evaluator:{plan.get('target_evaluator')}":
            raise RoutingProfileError("route outcome was not produced by the planned evaluator")
        trial["outcome"] = normalize_route_outcome(trial.get("outcome"))
        if cohort == "holdout":
            holdout_evaluators = set(holdout_requirements.get("evaluators") or ())
            versioned = f"{trial.get('evaluator_id')}@{trial.get('evaluator_version')}"
            if versioned not in holdout_evaluators:
                raise RoutingProfileError("holdout trial is not bound to a declared regression evaluator")
        if not trial.get("comparison_context_hash"):
            raise RoutingProfileError("trial requires a comparison context hash")
        unique[trajectory_id] = trial
    trials = list(unique.values())
    comparison_hashes = {str(t.get("comparison_context_hash") or "") for t in trials}
    if not trials or not comparison_hashes or "" in comparison_hashes or len(comparison_hashes) != 1:
        raise RoutingProfileError("control/treatment trials require one matched comparison context")
    control = [t for t in trials if t["cohort"] == "control"]
    treatment = [t for t in trials if t["cohort"] == "treatment"]
    holdout = [t for t in trials if t["cohort"] == "holdout"]
    holdout_minimum = int(holdout_requirements.get("minimum_samples", minimum_samples) or 0)
    if len(holdout) < holdout_minimum:
        raise RoutingProfileError("required independent holdout evidence is missing")
    holdout_applicable = sum(str(t.get("outcome")).upper() != "NOT_APPLICABLE" for t in holdout)
    max_na = float(holdout_requirements.get("max_not_applicable_fraction", 0.25))
    if (holdout and (len(holdout) - holdout_applicable) / len(holdout) > max_na
            or holdout_applicable < holdout_minimum):
        raise RoutingProfileError("holdout regression evidence is not sufficiently applicable")
    max_arm_na = float(holdout_requirements.get("max_not_applicable_fraction", 0.25))
    for arm, values in (("control", control), ("treatment", treatment)):
        applicable_count = sum(t.get("outcome") != "NOT_APPLICABLE" for t in values)
        if values and (len(values) - applicable_count) / len(values) > max_arm_na:
            raise RoutingProfileError(f"{arm} arm has too many non-applicable outcomes")
    control_applicable = [t for t in control if t.get("outcome") != "NOT_APPLICABLE"]
    treatment_applicable = [t for t in treatment if t.get("outcome") != "NOT_APPLICABLE"]
    control_bad = sum(t.get("outcome") == "FAIL" for t in control_applicable)
    treatment_bad = sum(t.get("outcome") == "FAIL" for t in treatment_applicable)
    control_rate = control_bad / len(control_applicable) if control_applicable else 0.0
    treatment_rate = treatment_bad / len(treatment_applicable) if treatment_applicable else 0.0
    effect = control_rate - treatment_rate
    control_low, control_high = _wilson(control_rate, len(control_applicable))
    treatment_low, treatment_high = _wilson(treatment_rate, len(treatment_applicable))
    effect_low, effect_high = control_low - treatment_high, control_high - treatment_low
    regressions = [t["trajectory_id"] for t in holdout if t.get("outcome") == "FAIL"]
    decision = "pass" if (
        len(control_applicable) >= minimum_samples and len(treatment_applicable) >= minimum_samples
        and len(holdout) >= holdout_minimum
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
        "policy_identities": {
            "control": plan.get("control_policy_identity"),
            "treatment": plan.get("treatment_policy_identity"),
            "holdout": plan.get("control_policy_identity"),
        },
        "comparison_context_hash": next(iter(comparison_hashes)),
        "evaluator_id": plan.get("target_evaluator"),
        "evaluator_version": evaluator_version,
        "evaluator_hash": plan.get("target_evaluator_hash"),
        "holdout_trajectory_ids": sorted(t["trajectory_id"] for t in holdout),
        "holdout_outcomes": [[t["trajectory_id"], t.get("outcome")] for t in holdout],
        "holdout_evaluations": [[t["trajectory_id"], t.get("evaluator_id"),
                                  t.get("evaluator_version"), t.get("outcome")] for t in holdout],
        "control_bad_rate": round(control_rate, 6),
        "treatment_bad_rate": round(treatment_rate, 6),
        "effect": round(effect, 6),
        "confidence_interval": [round(effect_low, 6), round(effect_high, 6)],
        "minimum_samples": minimum_samples,
        "minimum_holdout_samples": holdout_minimum,
        "holdout_evaluators": sorted(holdout_requirements.get("evaluators") or ()),
        "holdout_max_not_applicable_fraction": max_na,
        "control_max_not_applicable_fraction": max_arm_na,
        "treatment_max_not_applicable_fraction": max_arm_na,
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
    for package in ("digital_psychology", "lib"):
        try:
            resource = resource_files(package).joinpath(
                "schemas/behavioral-routing-profile.schema.json")
            if resource.is_file():
                return json.loads(resource.read_text(encoding="utf-8"))
        except (ModuleNotFoundError, FileNotFoundError):
            continue
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
        scope_mode = profile.get("scope_mode")
        if scope_mode not in {"exact", "validated_generalization"}:
            raise RoutingProfileError("deployable routing profile requires an explicit scope_mode")
        selectors = profile.get("selector_semantics") or {}
        if selectors.get("subject", "exact") != "exact":
            raise RoutingProfileError("learned subject selectors must be exact")
        required_identity = (
            "namespace_id", "application_id", "application_version", "application_instance_id",
            "provider_id", "model_id", "model_revision", "model_capability_hash",
            "harness_id", "harness_version", "agent_instance_id")
        subject = profile.get("subject") or {}
        missing_identity = [name for name in required_identity
                            if subject.get(name) in (None, "")]
        if missing_identity:
            raise RoutingProfileError(
                "deployable routing profile lacks exact producer identity: "
                + ", ".join(missing_identity))
        if scope_mode == "validated_generalization":
            strata = profile.get("validated_strata") or []
            receipt = profile.get("generalization_receipt")
            if not strata or not isinstance(receipt, Mapping) or receipt.get("decision") != "pass":
                raise RoutingProfileError(
                    "generalized routing scope requires independent validation strata and receipt")
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
    artifact_trust: Mapping[str, Any] = field(default_factory=dict)
    lifecycle_revision: Optional[int] = None
    lifecycle_status: Optional[str] = None
    scope_mode: str = "exact"
    selector_semantics: Mapping[str, Any] = field(
        default_factory=lambda: {"subject": "exact", "context": "exact"})
    validated_strata: tuple[Mapping[str, Any], ...] = ()
    generalization_receipt: Optional[Mapping[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        data = {
            "profile_version": self.profile_version,
            "profile_id": self.profile_id,
            "status": self.status,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "artifact_trust": dict(self.artifact_trust),
            "lifecycle_revision": self.lifecycle_revision,
            "lifecycle_status": self.lifecycle_status,
            "scope_mode": self.scope_mode,
            "selector_semantics": dict(self.selector_semantics),
            "validated_strata": [dict(item) for item in self.validated_strata],
            "generalization_receipt": (dict(self.generalization_receipt)
                                        if self.generalization_receipt is not None else None),
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
            profile_hash=str(data["profile_hash"]),
            artifact_trust=dict(data.get("artifact_trust") or {}),
            lifecycle_revision=data.get("lifecycle_revision"),
            lifecycle_status=data.get("lifecycle_status"),
            scope_mode=str(data.get("scope_mode") or "exact"),
            selector_semantics=dict(data.get("selector_semantics") or
                                    {"subject": "exact", "context": "exact"}),
            validated_strata=tuple(dict(item) for item in data.get("validated_strata", [])),
            generalization_receipt=(dict(data["generalization_receipt"])
                                   if data.get("generalization_receipt") is not None else None))


def _match_value(expected: Any, actual: Any, *, semantics: str = "exact") -> bool:
    """Match a declared selector; absence is never a production wildcard."""
    if expected in (None, "", [], ()) or actual in (None, ""):
        return False
    if semantics == "exact":
        if isinstance(expected, (list, tuple, set)):
            return list(expected) == list(actual) if isinstance(actual, (list, tuple, set)) else False
        return expected == actual
    expected_values = set(expected if isinstance(expected, (list, tuple, set)) else [expected])
    actual_values = set(actual if isinstance(actual, (list, tuple, set)) else [actual])
    if semantics == "contains_all":
        return expected_values.issubset(actual_values)
    if semantics == "contains_any":
        return bool(expected_values & actual_values)
    return False


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
    validate_profile(profile.to_dict(), require_deployable=True)
    for field in ("namespace_id", "application_id", "application_instance_id",
                  "provider_id", "model_id", "model_revision", "harness_id",
                  "agent_instance_id", "application_version", "model_capability_hash",
                  "harness_version"):
        if not _match_value(profile.subject.get(field), context.get(field), semantics="exact"):
            return []
    semantics = dict(profile.selector_semantics or {})
    for field, expected in profile.context.items():
        if not _match_value(expected, context.get(field),
                            semantics=str(semantics.get("context", "exact"))):
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
    selector_context = {
        key: value for key, value in dict(context).items()
        if value not in (None, "", [], (), {})
    }
    data = {
        "profile_version": PROFILE_VERSION,
        "profile_id": profile_id,
        "status": status,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "expires_at": expires_at,
        "artifact_trust": {"mode": "filesystem-owner", "owner_uid": os.getuid()},
        "scope_mode": "exact",
        "selector_semantics": {"subject": "exact", "context": "exact"},
        "validated_strata": [],
        "generalization_receipt": None,
        "subject": dict(subject),
        "context": selector_context,
        "observations": [dict(item) for item in observations],
        "routing_adjustments": [dict(item) for item in routing_adjustments],
        "evidence": dict(evidence),
        "criterion": dict(criterion),
    }
    data["profile_hash"] = profile_semantic_hash(data)
    validate_profile(data)
    return data


def _composite_event_identity(event: Any) -> tuple[Any, ...]:
    return (getattr(event, "namespace_id", "default"),
            getattr(event, "application_instance_id", "unknown-application"),
            getattr(event, "agent_instance_id", getattr(event, "agent_id", "agent")),
            getattr(event, "session_id", "session-unknown"), getattr(event, "task_id", ""),
            getattr(event, "attempt_id", "attempt-1"),
            getattr(event, "behavioral_subject", ""), getattr(event, "role", None),
            getattr(event, "interaction_id", None), getattr(event, "delegation_id", None),
            getattr(event, "event_id", ""))


def _composite_context_key(event: Any) -> str:
    values = _composite_event_identity(event)
    return "|".join(str(value) for value in values[:-1])


def _context_matches_event(event: Any, context: Mapping[str, Any]) -> bool:
    pairs = (("namespace_id", "namespace_id"),
             ("application_instance_id", "application_instance_id"),
             ("agent_instance_id", "agent_instance_id"),
             ("session_id", "session_id"), ("task_id", "task_id"),
             ("attempt_id", "attempt_id"),
             ("behavioral_subject", "behavioral_subject"),
             ("role", "role"), ("interaction_id", "interaction_id"),
             ("delegation_id", "delegation_id"))
    return all(context.get(field) in (None, getattr(event, event_field)) for field, event_field in pairs)


def derive_profiles(events: Iterable[Any], task_context: Mapping[str, Mapping[str, Any]],
                    *, minimum_samples: int = 10, minimum_effect: float = 0.10,
                    evaluator_version: str = ROUTING_EVALUATOR_VERSION) -> list[dict[str, Any]]:
    """Derive candidates from host-bound, evaluator-produced trial results.

    Route events are clustered into independent trajectories before counting.
    A caller cannot create a promotion-grade sample by repeating labels in one
    session, and a manual ``good``/``bad`` label without a bound evaluator and
    predeclared experiment is ignored.
    """
    from .trajectories import build_trajectories

    event_list = list(events)
    _episodes, tasks, _sessions, _agents = build_trajectories(event_list)
    event_to_session: dict[tuple[Any, ...], str] = {}
    for task in tasks:
        for event in task.events:
            # The experimental unit is an independent task attempt, not a
            # log line and not a whole long-lived session containing several
            # tasks.
            event_to_session[_composite_event_identity(event)] = task.trajectory_id
    grouped: dict[tuple[Any, ...], dict[str, dict[str, Any]]] = {}
    for event in event_list:
        if event.event_type != "route_outcome":
            continue
        payload = event.payload or {}
        route = payload.get("route")
        route_type = payload.get("route_type")
        outcome = payload.get("outcome")
        if not route or not route_type:
            continue
        try:
            outcome = normalize_route_outcome(outcome)
        except RoutingProfileError:
            continue
        trajectory_id = event_to_session.get(_composite_event_identity(event))
        if not trajectory_id or not payload.get("route_decision_id"):
            continue
        context_value = task_context.get(_composite_context_key(event))
        if not isinstance(context_value, Mapping):
            # Sidecars may use the older session/task/attempt key, but the
            # authenticated identity must still agree before promotion.
            context_value = task_context.get(
                f"{event.session_id}:{event.task_id}:{event.attempt_id}")
        if isinstance(context_value, Mapping) and not _context_matches_event(event, context_value):
            raise RoutingProfileError(
                "authenticated event identity disagrees with its experiment context")
        if not isinstance(context_value, Mapping):
            # Schema-v2 routing evidence is never attributed through a
            # task-only fallback; legacy imports must be explicitly adapted
            # before they can become promotion-grade evidence.
            continue
        context = dict(context_value)
        plan = context.get("routing_experiment_plan")
        cohort = context.get("routing_cohort")
        if not isinstance(plan, Mapping) or cohort not in {"control", "treatment", "holdout"}:
            continue
        if payload.get("experiment_plan_hash") != plan.get("plan_hash"):
            continue
        if payload.get("outcome_source") != f"evaluator:{plan.get('target_evaluator')}":
            continue
        if (payload.get("evaluator_id") != plan.get("target_evaluator")
                or payload.get("evaluator_version") != plan.get("target_evaluator_version")
                or payload.get("evaluator_hash") != plan.get("target_evaluator_hash")):
            continue
        if payload.get("candidate_profile_hash") != plan.get("candidate_profile_hash"):
            continue
        if bool(payload.get("candidate_adjustment_applied")) != (cohort == "treatment"):
            continue
        expected_policy_identity = (
            plan.get("treatment_policy_identity") if cohort == "treatment"
            else plan.get("control_policy_identity"))
        if payload.get("policy_identity") != expected_policy_identity:
            continue
        comparison_hash = context.get("comparison_context_hash")
        if not comparison_hash:
            continue
        statework_versions_value = context.get("statework_versions") or {}
        if not isinstance(statework_versions_value, Mapping):
            continue
        statework_versions = json.loads(_canonical(statework_versions_value))
        statework_versions_key = tuple(sorted(statework_versions.items()))
        key = (context.get("namespace_id", event.namespace_id),
               context.get("application_id", event.application_id),
               context.get("application_version", event.application_version),
               context.get("application_instance_id", event.application_instance_id),
               context.get("provider_id", event.provider_id),
               context.get("model_id", event.model_id or context.get("model")),
               context.get("model_revision", event.model_revision),
               context.get("model_capability_hash", event.model_capability_hash),
               context.get("harness_id", event.harness_id or context.get("harness")),
               context.get("harness_version", event.harness_version),
               context.get("agent_instance_id", event.agent_instance_id),
               context.get("task_family") or context.get("task_shape"),
               tuple(sorted(context.get("domain_tags") or [])),
               context.get("phase"), context.get("environment"),
               context.get("toolset"), statework_versions_key,
               context.get("framework_version"), context.get("guard_pack_hash"),
               context.get("role"), context.get("interaction_id"), context.get("delegation_id"),
               comparison_hash, plan.get("plan_hash"), route_type, route)
        bucket = grouped.setdefault(key, {"control": {}, "treatment": {},
                                          "holdout": {}, "plan": plan})
        trial = bucket[cohort].setdefault(trajectory_id, {
            "trajectory_id": trajectory_id,
            "cohort": cohort,
            "candidate_profile_hash": plan.get("candidate_profile_hash"),
            "candidate_adjustment_applied": cohort == "treatment",
            "experiment_plan_hash": plan.get("plan_hash"),
            "policy_identity": expected_policy_identity,
            "comparison_context_hash": comparison_hash,
            "route_decision_id": payload.get("route_decision_id"),
            "evaluator_id": payload.get("evaluator_id") or plan.get("target_evaluator"),
            "evaluator_version": payload.get("evaluator_version"),
            "evaluator_hash": payload.get("evaluator_hash"),
            "outcome_source": payload.get("outcome_source"),
            "outcome": None,
            "policy_hash": payload.get("policy_hash") or context.get("policy_hash"),
            "event_ids": [],
            "interaction_ids": [],
        })
        if trial["outcome"] is None:
            trial["outcome"] = outcome
        elif outcome != trial["outcome"]:
            raise RoutingProfileError(
                f"trajectory {trajectory_id!r} contains conflicting terminal outcomes; "
                "explicit evaluator supersession is required")
        trial["event_ids"].append(event.event_id)
        if event.interaction_id:
            trial["interaction_ids"].append(event.interaction_id)

    profiles: list[dict[str, Any]] = []
    for key, cohorts in sorted(grouped.items(), key=lambda item: repr(item[0])):
        plan = cohorts["plan"]
        control = list(cohorts["control"].values())
        treatment = list(cohorts["treatment"].values())
        holdout = list(cohorts["holdout"].values())
        cohort_interactions = {}
        for cohort_name, values in (("control", control), ("treatment", treatment), ("holdout", holdout)):
            for value in values:
                for interaction_id in value.get("interaction_ids", ()):
                    cohort_interactions.setdefault(interaction_id, set()).add(cohort_name)
        shared = {interaction_id for interaction_id, names in cohort_interactions.items() if len(names) > 1}
        if shared:
            raise RoutingProfileError(
                f"shared interaction evidence crosses experimental cohorts: {sorted(shared)}")
        holdout_minimum = int((plan.get("holdout_requirements") or {}).get(
            "minimum_samples", minimum_samples) or 0)
        if (len(control) < minimum_samples or len(treatment) < minimum_samples
                or len(holdout) < holdout_minimum):
            continue
        trials = control + treatment + holdout
        (namespace_id, application_id, application_version, application_instance_id,
         provider_id, model_id, model_revision, model_capability_hash, harness_id,
         harness_version, agent_instance_id, task_family,
         domains, phase, environment, toolset, statework_versions_items, framework_version,
         guard_pack_hash, role, interaction_id, delegation_id, comparison_hash,
         _plan_hash, route_type, route) = key
        source_hash = hashlib.sha256(_canonical(trials).encode("utf-8")).hexdigest()
        context = {"task_family": task_family, "task_shape": task_family,
                   "domain_tags": list(domains),
                   "phase": phase, "environment": environment, "toolset": toolset,
                   "statework_versions": dict(statework_versions_items),
                   "framework_version": framework_version,
                   "guard_pack_hash": guard_pack_hash,
                   "role": role,
                   "interaction_id": interaction_id,
                   "delegation_id": delegation_id}
        subject = {
            "namespace_id": namespace_id or "unknown",
            "application_id": application_id or "unknown",
            "application_version": application_version,
            "application_instance_id": application_instance_id or "unknown",
            "provider_id": provider_id,
            "model_id": model_id,
            "model_revision": model_revision,
            "model_capability_hash": model_capability_hash,
            "harness_id": harness_id,
            "harness_version": harness_version,
            "agent_instance_id": agent_instance_id or "unknown",
            # Compatibility aliases remain descriptive; deployable matching
            # uses the canonical identity fields above.
            "model": model_id,
            "harness": harness_id,
        }
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
            criterion={"evaluator_version": "routing-outcome-v2",
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
