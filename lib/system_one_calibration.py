"""Per-question calibration from later independent host outcomes."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import math
from typing import Any, Iterable, Mapping, Optional

from .protocol_artifact import (PROTOCOL_HASH, PROTOCOL_VERSION, QUESTION_REGISTRY,
                                QUESTION_REGISTRY_HASH,
                                STATE_PROJECTION_SCHEMA_HASH)
from .system_one_contracts import QuestionProbability, SystemOneFrame, canonical_hash


VALID_OUTCOME_AUTHORITIES = frozenset({"host_evidence", "validator", "csw_fact", "operator"})
_TEMPERATURES = tuple(round(0.25 + i * 0.05, 2) for i in range(96))


def _timestamp(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed


def _identity(event: Any) -> tuple[str, ...]:
    return tuple(str(getattr(event, field, "") or "") for field in (
        "namespace_id", "application_id", "application_instance_id", "agent_id",
        "agent_instance_id", "session_id", "task_id", "attempt_id", "interaction_id"))


def _normalise_probabilities(raw: Any) -> dict[str, float]:
    if not isinstance(raw, list) or not raw:
        return {}
    values: dict[str, float] = {}
    for item in raw:
        if not isinstance(item, Mapping):
            return {}
        label, probability = item.get("label"), item.get("probability")
        if (not isinstance(label, str) or not label or label in values
                or not isinstance(probability, (int, float))
                or not math.isfinite(float(probability))
                or not 0 <= float(probability) <= 1):
            return {}
        values[label] = float(probability)
    total = sum(values.values())
    if total <= 0 or abs(total - 1.0) > 0.03:
        return {}
    return {label: value / total for label, value in values.items()}


def _apply_temperature(probabilities: Mapping[str, float], temperature: float) -> dict[str, float]:
    logits = {label: math.log(max(probability, 1e-12)) / temperature
              for label, probability in probabilities.items()}
    peak = max(logits.values())
    exp = {label: math.exp(value - peak) for label, value in logits.items()}
    total = sum(exp.values())
    return {label: value / total for label, value in exp.items()}


def _brier(rows: list[tuple[dict[str, float], str]]) -> float:
    if not rows:
        return 1.0
    total = 0.0
    for probs, actual in rows:
        total += sum((probability - float(label == actual)) ** 2
                     for label, probability in probs.items())
    return total / len(rows)


def _ece(rows: list[tuple[dict[str, float], str]], temperature: float) -> float:
    if not rows:
        return 1.0
    bins: list[list[tuple[float, bool]]] = [[] for _ in range(10)]
    for probs, actual in rows:
        calibrated = _apply_temperature(probs, temperature)
        label, confidence = max(calibrated.items(), key=lambda item: item[1])
        bins[min(9, int(confidence * 10))].append((confidence, label == actual))
    return sum(len(bucket) / len(rows) * abs(
        sum(item[0] for item in bucket) / len(bucket)
        - sum(int(item[1]) for item in bucket) / len(bucket))
        for bucket in bins if bucket)


@dataclass(frozen=True)
class CalibrationRecord:
    """One validated transform for one question family and model identity."""

    question_id: str
    backend_id: str
    model_id: str
    model_revision: str
    model_content_hash: str
    provider_protocol_hash: str
    temperature: float
    minimum_advisory_confidence: float
    sample_count: int
    training_count: int
    validation_count: int
    training_outcome_digest: str
    validation_outcome_digest: str
    validation_accuracy: float
    validation_brier: float
    validation_ece: float
    work_modes: tuple[str, ...]
    deliverable_kinds: tuple[str, ...]
    independent_trajectory_count: int = 0
    cohort_counts: tuple[tuple[str, str, int], ...] = ()
    minimum_cohort_trajectories: int = 20
    protocol_version: str = PROTOCOL_VERSION
    protocol_hash: str = PROTOCOL_HASH
    question_registry_hash: str = QUESTION_REGISTRY_HASH
    state_projection_schema_hash: str = STATE_PROJECTION_SCHEMA_HASH
    minimum_outcomes: int = 100
    status: str = "candidate"
    drift_status: str = "clear"

    def __post_init__(self) -> None:
        if self.question_id not in QUESTION_REGISTRY:
            raise ValueError("unsupported calibration question family")
        for name in ("backend_id", "model_id", "model_revision"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or len(value) > 256:
                raise ValueError(f"{name} must be bounded non-empty text")
        for name in ("model_content_hash", "provider_protocol_hash", "protocol_hash",
                     "question_registry_hash", "state_projection_schema_hash",
                     "training_outcome_digest", "validation_outcome_digest"):
            value = getattr(self, name)
            if (not isinstance(value, str) or len(value) != 64
                    or any(ch not in "0123456789abcdef" for ch in value)):
                raise ValueError(f"{name} must be a lowercase SHA-256 digest")
        if self.protocol_version != PROTOCOL_VERSION:
            raise ValueError("unsupported calibration protocol")
        for name in ("temperature", "minimum_advisory_confidence", "validation_accuracy",
                     "validation_brier", "validation_ece"):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise ValueError(f"{name} must be finite")
        if not 0.25 <= self.temperature <= 5.0:
            raise ValueError("calibration temperature must be between 0.25 and 5")
        if not 0 <= self.minimum_advisory_confidence <= 1:
            raise ValueError("minimum confidence must be between zero and one")
        if any(not 0 <= getattr(self, name) <= 1 for name in (
                "validation_accuracy", "validation_brier", "validation_ece")):
            raise ValueError("calibration metrics must be between zero and one")
        if min(self.sample_count, self.training_count, self.validation_count,
               self.minimum_outcomes, self.independent_trajectory_count) < 0:
            raise ValueError("calibration sample counts must be non-negative")
        if self.training_count + self.validation_count != self.sample_count:
            raise ValueError("calibration train/validation counts do not add up")
        if not self.work_modes or not self.deliverable_kinds:
            raise ValueError("calibration applicability must name work modes and deliverables")
        if self.independent_trajectory_count < 1 or self.minimum_cohort_trajectories < 1:
            raise ValueError("calibration must record independent trajectory count")
        if (not self.cohort_counts
                or any(len(item) != 3 or not isinstance(item[0], str) or not item[0]
                       or not isinstance(item[1], str) or not item[1]
                       or not isinstance(item[2], int) or isinstance(item[2], bool)
                       or item[2] < self.minimum_cohort_trajectories
                       or item[2] > self.independent_trajectory_count
                       for item in self.cohort_counts)):
            raise ValueError("calibration applicability cohorts are malformed")
        cohort_pairs = {(mode, kind) for mode, kind, _count in self.cohort_counts}
        if (len(cohort_pairs) != len(self.cohort_counts)
                or set(self.work_modes) != {mode for mode, _kind in cohort_pairs}
                or set(self.deliverable_kinds) != {kind for _mode, kind in cohort_pairs}):
            raise ValueError("calibration applicability cohorts are inconsistent")
        if self.status not in {"candidate", "active", "stale", "rolled_back"}:
            raise ValueError("unsupported calibration lifecycle status")
        if self.drift_status not in {"clear", "warning", "drifted"}:
            raise ValueError("unsupported calibration drift status")

    def canonical(self) -> dict[str, Any]:
        return {"question_id": self.question_id, "backend_id": self.backend_id,
                "model_id": self.model_id, "model_revision": self.model_revision,
                "model_content_hash": self.model_content_hash,
                "provider_protocol_hash": self.provider_protocol_hash,
                "temperature": float(self.temperature),
                "minimum_advisory_confidence": float(self.minimum_advisory_confidence),
                "sample_count": self.sample_count, "training_count": self.training_count,
                "validation_count": self.validation_count,
                "training_outcome_digest": self.training_outcome_digest,
                "validation_outcome_digest": self.validation_outcome_digest,
                "validation_accuracy": float(self.validation_accuracy),
                "validation_brier": float(self.validation_brier),
                "validation_ece": float(self.validation_ece),
                "work_modes": list(self.work_modes),
                "deliverable_kinds": list(self.deliverable_kinds),
                "independent_trajectory_count": self.independent_trajectory_count,
                "cohort_counts": [list(item) for item in self.cohort_counts],
                "minimum_cohort_trajectories": self.minimum_cohort_trajectories,
                "protocol_version": self.protocol_version, "protocol_hash": self.protocol_hash,
                "question_registry_hash": self.question_registry_hash,
                "state_projection_schema_hash": self.state_projection_schema_hash,
                "minimum_outcomes": self.minimum_outcomes, "status": self.status,
                "drift_status": self.drift_status}

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "CalibrationRecord":
        fields = {"question_id", "backend_id", "model_id", "model_revision",
                  "model_content_hash", "provider_protocol_hash", "temperature",
                  "minimum_advisory_confidence", "sample_count", "training_count",
                  "validation_count", "training_outcome_digest", "validation_outcome_digest",
                  "validation_accuracy", "validation_brier", "validation_ece", "work_modes",
                  "deliverable_kinds", "independent_trajectory_count", "cohort_counts",
                  "minimum_cohort_trajectories", "protocol_version", "protocol_hash",
                  "question_registry_hash", "state_projection_schema_hash",
                  "minimum_outcomes", "status", "drift_status"}
        if not isinstance(value, Mapping) or set(value) != fields:
            raise ValueError("malformed per-question calibration record")
        payload = dict(value)
        payload["work_modes"] = tuple(payload["work_modes"])
        payload["deliverable_kinds"] = tuple(payload["deliverable_kinds"])
        payload["cohort_counts"] = tuple((str(mode), str(kind), int(count))
                                         for mode, kind, count in payload["cohort_counts"])
        return cls(**payload)

    @property
    def digest(self) -> str:
        return canonical_hash(self.canonical())

    def apply(self, raw: tuple[QuestionProbability, ...]) -> tuple[QuestionProbability, ...]:
        calibrated = _apply_temperature({item.label: item.probability for item in raw},
                                         self.temperature)
        return tuple(QuestionProbability(item.label, calibrated[item.label]) for item in raw)

    def valid_for(self, frame: SystemOneFrame, *, minimum_accuracy: float = 0.8,
                  maximum_brier: float = 0.25, maximum_ece: float = 0.12) -> bool:
        task = frame.state.task
        return bool(
            self.status == "active" and self.drift_status == "clear"
            and self.question_id in frame.question_ids
            and self.backend_id == frame.backend_id and self.model_id == frame.model_id
            and self.model_revision == frame.model_revision
            and self.model_content_hash == frame.model_content_hash
            and self.provider_protocol_hash == frame.provider_protocol_hash
            and self.protocol_version == frame.protocol_version
            and self.protocol_hash == frame.protocol_hash
            and self.question_registry_hash == QUESTION_REGISTRY_HASH
            and self.state_projection_schema_hash == STATE_PROJECTION_SCHEMA_HASH
            and self.validation_accuracy >= minimum_accuracy
            and self.validation_brier <= maximum_brier
            and self.validation_ece <= maximum_ece
            and self.validation_count >= self.minimum_outcomes
            and task["work_mode"] in self.work_modes
            and task["deliverable_kind"] in self.deliverable_kinds
            and dict(((mode, kind), count) for mode, kind, count in self.cohort_counts).get(
                (str(task["work_mode"]), str(task["deliverable_kind"])), 0)
            >= self.minimum_cohort_trajectories)

    def with_status(self, status: str) -> "CalibrationRecord":
        from dataclasses import replace
        return replace(self, status=status)


@dataclass(frozen=True)
class CalibrationBundle:
    records: Mapping[str, CalibrationRecord]

    def __post_init__(self) -> None:
        if any(key != value.question_id for key, value in self.records.items()):
            raise ValueError("calibration bundle keys must match their question family")
        if len(self.records) > 8:
            raise ValueError("calibration bundle exceeds question-family bound")

    def canonical(self) -> dict[str, Any]:
        return {key: self.records[key].canonical() for key in sorted(self.records)}

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": "1.1.0", "records": self.canonical(),
                "bundle_hash": self.digest}

    @property
    def digest(self) -> str:
        return canonical_hash(self.canonical()) if self.records else ""

    @classmethod
    def active(cls, records: Iterable[CalibrationRecord]) -> "CalibrationBundle":
        source = tuple(records)
        active = tuple(item for item in source if item.status == "active")
        material = {item.question_id: item for item in active}
        if len(material) != len(active):
            raise ValueError("duplicate question family in calibration bundle")
        return cls(material)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "CalibrationBundle":
        if (not isinstance(value, Mapping) or set(value) != {
                "schema_version", "records", "bundle_hash"}
                or value.get("schema_version") != "1.1.0"
                or not isinstance(value.get("records"), Mapping)):
            raise ValueError("malformed System-1 calibration bundle")
        bundle = cls({str(key): CalibrationRecord.from_mapping(item)
                      for key, item in value["records"].items()})
        if value.get("bundle_hash") != bundle.digest:
            raise ValueError("System-1 calibration bundle hash mismatch")
        if any(record.status != "active" for record in bundle.records.values()):
            raise ValueError("runtime calibration bundle contains an inactive record")
        return bundle


def _paired_rows(events: Iterable[Any], question_id: str) -> list[dict[str, Any]]:
    if question_id not in QUESTION_REGISTRY:
        raise ValueError("unsupported calibration question family")
    event_list = tuple(events)
    decisions: dict[tuple[tuple[str, ...], str], Any] = {}
    for event in event_list:
        payload = getattr(event, "payload", {}) or {}
        answers = payload.get("system_one_answers")
        if (getattr(event, "event_type", None) != "system_one_decision"
                or not isinstance(answers, Mapping) or question_id not in answers):
            continue
        decision_id = str(payload.get("system_one_decision_id") or "")
        if decision_id:
            decisions[(_identity(event), decision_id)] = event
    rows = []
    outcome_key = f"system_one_outcome_{question_id}"
    allowed_labels = set(QUESTION_REGISTRY[question_id]["criteria"])
    seen: set[tuple[tuple[str, ...], str]] = set()
    for outcome in event_list:
        payload = getattr(outcome, "payload", {}) or {}
        if (getattr(outcome, "event_type", None) != "system_one_outcome"
                or getattr(outcome, "category", None) != "validation"
                or payload.get("system_one_independent_outcome") is not True
                or payload.get("system_one_outcome_authority") not in VALID_OUTCOME_AUTHORITIES
                or not isinstance(payload.get("system_one_outcome_evidence_ref"), str)
                or not payload.get("system_one_outcome_evidence_ref")
                or (allowed_labels and payload.get(outcome_key) not in allowed_labels)):
            continue
        key = (_identity(outcome), str(payload.get("system_one_outcome_for") or ""))
        decision = decisions.get(key)
        if decision is None or key in seen:
            continue
        decision_time = _timestamp(getattr(decision, "timestamp", None))
        outcome_time = _timestamp(getattr(outcome, "timestamp", None))
        if decision_time is None or outcome_time is None or outcome_time <= decision_time:
            continue
        decision_payload = getattr(decision, "payload", {}) or {}
        answer = decision_payload["system_one_answers"][question_id]
        if not isinstance(answer, Mapping) or answer.get("question_id") != question_id:
            continue
        probabilities = _normalise_probabilities(answer.get("raw_probabilities"))
        selected = answer.get("selected")
        actual = payload[outcome_key]
        if (not probabilities or selected not in probabilities
                or (allowed_labels and set(probabilities) != allowed_labels)
                or actual not in probabilities):
            continue
        if decision_payload.get("system_one_independent_outcome") is True:
            continue
        # All model identity and applicability data are carried on the prediction.
        required_identity = ("system_one_backend_id", "system_one_model_id",
                             "system_one_model_revision", "system_one_model_content_hash",
                             "system_one_provider_protocol_hash", "system_one_work_mode",
                             "system_one_deliverable_kind")
        if any(not decision_payload.get(field) for field in required_identity):
            continue
        rows.append({
            "identity": key[0], "trajectory_id": hashlib.sha256(
                json.dumps(key[0][:7], separators=(",", ":")).encode("utf-8")).hexdigest(),
            "decision_id": key[1], "probabilities": probabilities,
            "selected": selected, "actual": actual,
            "model_identity": tuple(str(decision_payload[field]) for field in required_identity[:5]),
            "work_mode": str(decision_payload["system_one_work_mode"]),
            "deliverable_kind": str(decision_payload["system_one_deliverable_kind"]),
            "evidence_ref": payload["system_one_outcome_evidence_ref"],
            "outcome_authority": payload["system_one_outcome_authority"],
        })
        seen.add(key)
    rows.sort(key=lambda row: (row["identity"], row["decision_id"]))
    return rows


def calibration_metrics(events: Iterable[Any], *, label: str = "progress_relation") -> dict[str, Any]:
    rows = _paired_rows(events, label)
    correct = [row["selected"] == row["actual"] for row in rows]
    raw_brier = _brier([(row["probabilities"], row["actual"]) for row in rows])
    return {"label": label, "independent_outcomes": len(rows),
            "accuracy": sum(map(int, correct)) / len(rows) if rows else 0.0,
            "brier_score": raw_brier,
            "calibration_bins": _calibration_bins(rows)}


def _calibration_bins(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    bins: list[list[tuple[float, bool]]] = [[] for _ in range(10)]
    for row in rows:
        confidence = max(row["probabilities"].values())
        bins[min(9, int(confidence * 10))].append((confidence, row["selected"] == row["actual"]))
    return [{"lower": index / 10, "upper": (index + 1) / 10,
             "count": len(bucket),
             "mean_confidence": sum(item[0] for item in bucket) / len(bucket),
             "accuracy": sum(int(item[1]) for item in bucket) / len(bucket)}
            for index, bucket in enumerate(bins) if bucket]


def calibration_record_from_events(
    events: Iterable[Any], *, question_id: str,
    minimum_confidence: float = 0.9, minimum_outcomes: int = 100,
    minimum_independent_trajectories: int = 30,
    minimum_cohort_trajectories: int = 20,
    minimum_accuracy: float = 0.8, maximum_brier: float = 0.25,
    maximum_ece: float = 0.12,
) -> CalibrationRecord:
    """Fit a temperature on deterministic training rows and evaluate held-out rows."""
    rows = _paired_rows(events, question_id)
    if len(rows) < minimum_outcomes:
        raise ValueError("insufficient later independent outcomes for calibration")
    trajectory_ids = sorted({row["trajectory_id"] for row in rows})
    if len(trajectory_ids) < minimum_independent_trajectories:
        raise ValueError("insufficient later independent trajectories for calibration")
    identities = {row["model_identity"] for row in rows}
    if len(identities) != 1:
        raise ValueError("calibration evidence mixes provider or model content identities")
    model_tuple = next(iter(identities))
    ordered_trajectories = sorted(trajectory_ids, key=lambda item: hashlib.sha256(
        item.encode("utf-8")).digest())
    validation_trajectories = set(ordered_trajectories[::5])
    validation = [row for row in rows if row["trajectory_id"] in validation_trajectories]
    train = [row for row in rows if row["trajectory_id"] not in validation_trajectories]
    if len(train) < 20 or len(validation) < max(10, int(minimum_outcomes * 0.2)):
        raise ValueError("calibration requires at least 20 training and 10 held-out outcomes")
    train_rows = [(row["probabilities"], row["actual"]) for row in train]
    temperature = min(_TEMPERATURES, key=lambda item: (_brier([
        (_apply_temperature(probabilities, item), actual)
        for probabilities, actual in train_rows]), item))
    validation_rows = [(row["probabilities"], row["actual"]) for row in validation]
    calibrated_rows = [(_apply_temperature(probabilities, temperature), actual)
                       for probabilities, actual in validation_rows]
    accuracy = sum(int(max(probs.items(), key=lambda item: item[1])[0] == actual)
                   for probs, actual in calibrated_rows) / len(calibrated_rows)
    brier = _brier(calibrated_rows)
    ece = _ece(validation_rows, temperature)
    if accuracy < minimum_accuracy or brier > maximum_brier or ece > maximum_ece:
        raise ValueError("held-out System-1 calibration failed its accuracy, Brier, or ECE floor")

    def digest(material: list[dict[str, Any]]) -> str:
        rows_for_digest = [{"identity": list(row["identity"]),
                            "trajectory_id": row["trajectory_id"],
                            "decision_id": row["decision_id"],
                            "predicted": row["selected"], "actual": row["actual"],
                            "probabilities": row["probabilities"],
                            "evidence_ref": row["evidence_ref"],
                            "authority": row["outcome_authority"]}
                           for row in material]
        return canonical_hash(rows_for_digest)

    cohort_trajectories: dict[tuple[str, str], set[str]] = {}
    for row in rows:
        key = (row["work_mode"], row["deliverable_kind"])
        cohort_trajectories.setdefault(key, set()).add(row["trajectory_id"])
    applicable_cohorts = tuple(sorted(
        (mode, kind, len(trajectories))
        for (mode, kind), trajectories in cohort_trajectories.items()
        if len(trajectories) >= minimum_cohort_trajectories))
    if not applicable_cohorts:
        raise ValueError("no work-mode/deliverable cohort has enough independent trajectories")
    return CalibrationRecord(
        question_id=question_id, backend_id=model_tuple[0], model_id=model_tuple[1],
        model_revision=model_tuple[2], model_content_hash=model_tuple[3],
        provider_protocol_hash=model_tuple[4], temperature=temperature,
        minimum_advisory_confidence=minimum_confidence,
        sample_count=len(rows), training_count=len(train), validation_count=len(validation),
        training_outcome_digest=digest(train), validation_outcome_digest=digest(validation),
        validation_accuracy=accuracy, validation_brier=brier, validation_ece=ece,
        work_modes=tuple(sorted({mode for mode, _kind, _count in applicable_cohorts})),
        deliverable_kinds=tuple(sorted({kind for _mode, kind, _count in applicable_cohorts})),
        independent_trajectory_count=len(trajectory_ids),
        cohort_counts=applicable_cohorts,
        minimum_cohort_trajectories=minimum_cohort_trajectories,
        minimum_outcomes=max(10, int(minimum_outcomes * 0.2)))


__all__ = ["CalibrationRecord", "CalibrationBundle", "calibration_metrics",
           "calibration_record_from_events"]
