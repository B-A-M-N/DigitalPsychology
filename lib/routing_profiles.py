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


class RoutingProfileError(ValueError):
    pass


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def profile_semantic_hash(profile: Mapping[str, Any]) -> str:
    data = dict(profile)
    data.pop("profile_hash", None)
    return hashlib.sha256(_canonical(data).encode("utf-8")).hexdigest()


def _criterion_hash(criterion: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(dict(criterion)).encode("utf-8")).hexdigest()


def routing_validation_receipt(profile: Mapping[str, Any], receipt_id: str) -> dict[str, Any]:
    """Build a content-addressed promotion receipt for a candidate profile."""
    candidate = json.loads(json.dumps(dict(profile)))
    candidate["status"] = "candidate"
    candidate.pop("profile_hash", None)
    evidence = dict(candidate.get("evidence") or {})
    for key in ("receipt_ref", "receipt"):
        evidence.pop(key, None)
    candidate["evidence"] = evidence
    candidate_hash = profile_semantic_hash(candidate)
    receipt = {
        "receipt_id": receipt_id,
        "kind": "routing_validation",
        "profile_candidate_hash": candidate_hash,
        "criterion_hash": _criterion_hash(profile.get("criterion") or {}),
        "evidence_hash": evidence.get("source_hash", ""),
        "decision": "pass",
    }
    receipt["receipt_hash"] = hashlib.sha256(_canonical(receipt).encode("utf-8")).hexdigest()
    return receipt


def promote_profile(profile: Mapping[str, Any], receipt_id: str,
                    *, status: str = "active") -> dict[str, Any]:
    if status not in {"validated", "active"}:
        raise RoutingProfileError("routing profile promotion requires validated or active status")
    promoted = json.loads(json.dumps(dict(profile)))
    receipt = routing_validation_receipt(promoted, receipt_id)
    promoted["status"] = status
    promoted.setdefault("evidence", {})["receipt_ref"] = receipt_id
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
        if receipt.get("receipt_id") != evidence.get("receipt_ref") or receipt.get("decision") != "pass":
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
        if receipt.get("criterion_hash") != _criterion_hash(profile.get("criterion") or {}):
            raise RoutingProfileError("routing profile receipt criterion mismatch")
        if receipt.get("evidence_hash") != candidate_evidence.get("source_hash"):
            raise RoutingProfileError("routing profile receipt evidence mismatch")
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
                    evaluator_version: str = "routing-outcome-v1") -> list[dict[str, Any]]:
    """Characterize route experiments without joining unrelated sessions.

    Events must be host-produced ``route_outcome`` records whose payload has
    ``route``, ``route_type`` and ``outcome`` (``bad``/``good``).  A profile
    is emitted as a candidate only when matched control/treatment cohorts
    provide enough evidence and treatment reduces the bad-route rate.
    """
    from .trajectories import build_trajectories

    event_list = list(events)
    _episodes, tasks, sessions, _agents = build_trajectories(event_list)
    event_to_session: dict[str, str] = {}
    for session in sessions:
        for event in session.events:
            event_to_session[event.event_id] = session.trajectory_id
    grouped: dict[tuple[Any, ...], dict[str, list[Any]]] = {}
    for event in event_list:
        if event.event_type != "route_outcome":
            continue
        payload = event.payload or {}
        route = payload.get("route")
        route_type = payload.get("route_type")
        outcome = payload.get("outcome")
        if not route or not route_type or outcome not in {"bad", "good"}:
            continue
        context = dict(task_context.get(
            f"{event.session_id}:{event.task_id}",
            task_context.get(event.task_id, {})))
        cohort = context.get("cohort")
        if cohort not in {"control", "treatment"}:
            continue
        key = (event.agent_instance_id, context.get("model"), context.get("harness"),
               context.get("task_family") or context.get("task_shape"),
               tuple(sorted(context.get("domain_tags") or [])),
               context.get("phase"), context.get("environment"), route_type, route)
        grouped.setdefault(key, {"control": [], "treatment": []})[cohort].append(event)

    profiles: list[dict[str, Any]] = []
    for key, cohorts in sorted(grouped.items(), key=lambda item: repr(item[0])):
        control = cohorts["control"]
        treatment = cohorts["treatment"]
        if len(control) < minimum_samples or len(treatment) < minimum_samples:
            continue
        control_bad = sum(event.payload.get("outcome") == "bad" for event in control) / len(control)
        treatment_bad = sum(event.payload.get("outcome") == "bad" for event in treatment) / len(treatment)
        effect = control_bad - treatment_bad
        if effect < minimum_effect:
            continue
        agent_instance_id, model, harness, task_family, domains, phase, environment, route_type, route = key
        evidence_events = control + treatment
        context = {"task_family": task_family, "domain_tags": list(domains),
                   "phase": phase, "environment": environment}
        subject = {"agent_instance_id": agent_instance_id or "unknown",
                   "model": model, "harness": harness}
        adjustment = {
            "route_type": route_type, "route": route, "statework_id": None,
            "disposition": "suppress",
            "condition": {"all": [{"field": "task_family", "equals": task_family}]},
            "evidence_refs": [event.event_id for event in evidence_events],
            "confidence": min(1.0, effect),
            "expires_after_samples": minimum_samples * 10,
        }
        profile = build_profile(
            profile_id=f"routing-{hashlib.sha256(repr(key).encode()).hexdigest()[:16]}",
            subject=subject, context=context,
            observations=[{"route": route, "route_type": route_type,
                           "control_samples": len(control),
                           "treatment_samples": len(treatment),
                           "control_bad_rate": round(control_bad, 6),
                           "treatment_bad_rate": round(treatment_bad, 6),
                           "effect": round(effect, 6)}],
            routing_adjustments=[adjustment],
            evidence={"event_ids": [event.event_id for event in evidence_events],
                      "trajectory_ids": sorted({event_to_session.get(event.event_id, "")
                                                 for event in evidence_events}),
                      "source_hash": hashlib.sha256(
                          _canonical([event.to_dict() for event in evidence_events]).encode()).hexdigest(),
                      "receipt_ref": None},
            criterion={"evaluator_version": evaluator_version,
                       "minimum_samples": minimum_samples,
                       "minimum_effect": minimum_effect,
                       "confidence_rule": "treatment bad-rate lower than control by minimum_effect"},
            status="candidate")
        profiles.append(profile)
    return profiles
