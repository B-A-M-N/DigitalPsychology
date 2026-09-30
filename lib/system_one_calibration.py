"""Pinned calibration metadata for advisory System-1 use."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Iterable, Mapping

from .system_one_contracts import PROTOCOL_VERSION


@dataclass(frozen=True)
class CalibrationRecord:
    backend_id: str
    model_id: str
    protocol_version: str = PROTOCOL_VERSION
    minimum_confidence: float = 0.9
    sample_count: int = 0
    independent_outcomes: int = 0
    calibration_hash: str = ""
    outcome_digest: str = ""
    observed_accuracy: float = 0.0
    observed_brier_score: float = 1.0

    def __post_init__(self) -> None:
        if not self.backend_id or len(self.backend_id) > 256:
            raise ValueError("backend_id must be bounded")
        if not self.model_id or len(self.model_id) > 256:
            raise ValueError("model_id must be bounded")
        if self.protocol_version != PROTOCOL_VERSION:
            raise ValueError("unsupported calibration protocol")
        if not 0 <= self.minimum_confidence <= 1:
            raise ValueError("minimum_confidence must be between 0 and 1")
        if self.sample_count < 0 or self.independent_outcomes < 0:
            raise ValueError("calibration counts must be non-negative")
        if self.independent_outcomes > self.sample_count:
            raise ValueError("independent outcomes cannot exceed sample count")
        if not 0 <= self.observed_accuracy <= 1:
            raise ValueError("observed_accuracy must be between 0 and 1")
        if not 0 <= self.observed_brier_score <= 1:
            raise ValueError("observed_brier_score must be between 0 and 1")
        if self.independent_outcomes and (
                len(self.outcome_digest) != 64
                or any(ch not in "0123456789abcdef" for ch in self.outcome_digest)):
            raise ValueError("calibrated records require a lowercase outcome digest")

    def canonical(self) -> dict[str, Any]:
        return {"backend_id": self.backend_id, "model_id": self.model_id,
                "protocol_version": self.protocol_version,
                "minimum_confidence": self.minimum_confidence,
                "sample_count": self.sample_count,
                "independent_outcomes": self.independent_outcomes,
                "outcome_digest": self.outcome_digest,
                "observed_accuracy": self.observed_accuracy,
                "observed_brier_score": self.observed_brier_score}

    @property
    def digest(self) -> str:
        body = json.dumps(self.canonical(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(body.encode("utf-8")).hexdigest()

    def valid_for(self, *, backend_id: str, model_id: str,
                  expected_hash: str, minimum_outcomes: int = 100,
                  minimum_accuracy: float = 0.8) -> bool:
        return bool(expected_hash and self.digest == expected_hash
                    and self.backend_id == backend_id and self.model_id == model_id
                    and self.independent_outcomes >= minimum_outcomes
                    and self.outcome_digest and self.observed_accuracy >= minimum_accuracy)


def calibration_metrics(events: Iterable[Any], *, label: str = "progress_relation") -> dict[str, Any]:
    """Pair predictions with later independent host outcomes, scoped by identity.

    Only T2 events marked independent and carrying a non-empty host evidence
    reference participate. Output contains aggregates, never per-event raw
    confidence or model explanations.
    """
    if label not in {"action_relation", "progress_relation", "retry_relation", "goal_drift"}:
        raise ValueError("unsupported calibration label")
    events = tuple(events)
    prediction_key = f"system_one_{label}"
    outcome_key = f"system_one_outcome_{label}"
    valid_authorities = {"host_evidence", "validator", "csw_fact", "operator"}
    decisions: dict[tuple[tuple[str, ...], str], Any] = {}
    for event in events:
        event_type = getattr(event, "event_type", None)
        payload = getattr(event, "payload", {}) or {}
        if event_type != "system_one_decision" or prediction_key not in payload:
            continue
        identity = tuple(str(getattr(event, field, "") or "") for field in (
            "namespace_id", "application_id", "application_instance_id", "agent_id", "agent_instance_id",
            "session_id", "task_id", "attempt_id", "interaction_id"))
        decision_id = str(payload.get("system_one_decision_id") or "")
        if decision_id:
            decisions[(identity, decision_id)] = event
    pairs = []
    matched = set()
    for outcome in events:
        payload = getattr(outcome, "payload", {}) or {}
        if (getattr(outcome, "event_type", None) != "system_one_outcome"
                or payload.get("system_one_independent_outcome") is not True
                or payload.get("system_one_outcome_authority") not in valid_authorities
                or not payload.get("system_one_outcome_evidence_ref")
                or outcome_key not in payload):
            continue
        identity = tuple(str(getattr(outcome, field, "") or "") for field in (
            "namespace_id", "application_id", "application_instance_id", "agent_id", "agent_instance_id",
            "session_id", "task_id", "attempt_id", "interaction_id"))
        key = (identity, str(payload.get("system_one_outcome_for") or ""))
        decision = decisions.get(key)
        if decision is None or key in matched:
            continue
        predicted = (getattr(decision, "payload", {}) or {}).get(prediction_key)
        actual = payload.get(outcome_key)
        confidence = (getattr(decision, "payload", {}) or {}).get("system_one_confidence")
        if predicted is None or actual is None or not isinstance(confidence, (int, float)):
            continue
        pairs.append((float(confidence), predicted == actual))
        matched.add(key)
    count = len(pairs)
    bins = []
    for lower in (0.0, 0.5, 0.7, 0.8, 0.9):
        upper = lower + 0.1 if lower < 0.9 else 1.0000001
        selected = [(confidence, correct) for confidence, correct in pairs
                    if lower <= confidence < upper]
        if selected:
            bins.append({"lower": lower, "upper": min(1.0, upper),
                         "count": len(selected),
                         "mean_confidence": sum(item[0] for item in selected) / len(selected),
                         "accuracy": sum(int(item[1]) for item in selected) / len(selected)})
    return {"label": label, "independent_outcomes": count,
            "accuracy": sum(int(correct) for _, correct in pairs) / count if count else 0.0,
            "brier_score": sum((confidence - float(correct)) ** 2
                                for confidence, correct in pairs) / count if count else 0.0,
            "calibration_bins": bins}


def calibration_record_from_events(events: Iterable[Any], *, backend_id: str,
                                   model_id: str, label: str = "progress_relation",
                                   minimum_confidence: float = 0.9,
                                   minimum_outcomes: int = 100,
                                   minimum_accuracy: float = 0.8) -> CalibrationRecord:
    events = tuple(events)
    report = calibration_metrics(events, label=label)
    prediction_key = f"system_one_{label}"
    outcome_key = f"system_one_outcome_{label}"
    count = int(report["independent_outcomes"])
    if count < minimum_outcomes:
        raise ValueError("insufficient independent System-1 outcomes for calibration")
    if float(report["accuracy"]) < minimum_accuracy:
        raise ValueError("System-1 outcome accuracy is below the calibration floor")
    decision_keys = {}
    for event in events:
        payload = getattr(event, "payload", {}) or {}
        if getattr(event, "event_type", None) == "system_one_decision":
            identity = tuple(str(getattr(event, field, "") or "") for field in (
                "namespace_id", "application_id", "application_instance_id", "agent_id",
                "agent_instance_id", "session_id", "task_id", "attempt_id", "interaction_id"))
            decision_keys[(identity, str(payload.get("system_one_decision_id") or ""))] = payload
    outcome_material = []
    for event in events:
        payload = getattr(event, "payload", {}) or {}
        identity = tuple(str(getattr(event, field, "") or "") for field in (
            "namespace_id", "application_id", "application_instance_id", "agent_id",
            "agent_instance_id", "session_id", "task_id", "attempt_id", "interaction_id"))
        key = (identity, str(payload.get("system_one_outcome_for") or ""))
        if (getattr(event, "event_type", None) == "system_one_outcome"
                and payload.get("system_one_independent_outcome") is True
                and payload.get("system_one_outcome_authority") in {
                    "host_evidence", "validator", "csw_fact", "operator"}
                and payload.get("system_one_outcome_evidence_ref")
                and outcome_key in payload and key in decision_keys):
            outcome_material.append({
                "identity": list(identity), "decision_id": key[1],
                "predicted": decision_keys[key].get(prediction_key),
                "actual": payload.get(outcome_key),
                "evidence_ref": payload["system_one_outcome_evidence_ref"],
            })
    outcome_material.sort(key=lambda item: (item["identity"], item["decision_id"]))
    outcome_digest = hashlib.sha256(json.dumps(
        outcome_material, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return CalibrationRecord(
        backend_id=backend_id, model_id=model_id, minimum_confidence=minimum_confidence,
        sample_count=count, independent_outcomes=count,
        outcome_digest=outcome_digest, observed_accuracy=float(report["accuracy"]),
        observed_brier_score=float(report["brier_score"]))
