"""Closed, bounded wire contracts for optional System-1 judgment.

These objects contain typed host projections only. They deliberately have no
field for transcripts, prompts, chain-of-thought, source text, or tool output.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping, Optional, Tuple

PROTOCOL_VERSION = "1.0.0"
PROTOCOL_HASH = "abba6a690fab64dae9a7595d71209106fb14f285e7f490c2194476be19f3e048"
ACTION_RELATIONS = frozenset({
    "advances_primary_goal", "removes_concrete_blocker",
    "required_verification", "diagnostic_support", "unrelated",
})
PROGRESS_RELATIONS = frozenset({
    "goal_progress", "blocker_removed", "diagnostic_progress",
    "verification_progress", "recovery_progress", "no_progress",
})
RETRY_RELATIONS = frozenset({
    "materially_new", "same_premise_new_action", "equivalent_retry", "unrelated",
})
GOAL_DRIFT = frozenset({"yes", "no"})
QUESTION_FIELDS = frozenset({"action_relation", "progress_relation", "retry_relation", "goal_drift"})


def _text(value: Any, name: str, limit: int = 256, *, optional: bool = False) -> Optional[str]:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{name} must be bounded non-empty text")
    return value


def _digest(value: Any, name: str) -> str:
    result = _text(value, name, 64)
    if len(result) != 64 or any(ch not in "0123456789abcdef" for ch in result):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return result


@dataclass(frozen=True)
class SystemOneIdentity:
    namespace_id: str
    application_id: str
    application_instance_id: str
    agent_id: str
    agent_instance_id: str
    session_id: str
    task_id: str
    attempt_id: str
    interaction_id: Optional[str] = None

    def __post_init__(self) -> None:
        for name in ("namespace_id", "application_id", "application_instance_id",
                     "agent_id", "agent_instance_id", "session_id", "task_id", "attempt_id"):
            _text(getattr(self, name), name)
        if self.interaction_id is not None:
            _text(self.interaction_id, "interaction_id")

    def to_dict(self) -> dict[str, Any]:
        return {"namespace_id": self.namespace_id, "application_id": self.application_id,
                "application_instance_id": self.application_instance_id,
                "agent_id": self.agent_id, "agent_instance_id": self.agent_instance_id,
                "session_id": self.session_id, "task_id": self.task_id,
                "attempt_id": self.attempt_id, "interaction_id": self.interaction_id}


@dataclass(frozen=True)
class SystemOneFrame:
    identity: SystemOneIdentity
    request_id: str
    sequence: int
    state_digest: str
    policy_hash: str
    backend_id: str
    model_id: str
    calibration_hash: str
    questions: Tuple[str, ...]
    objective: str
    criteria: Tuple[Tuple[str, str], ...]
    recent_activity: Tuple[str, ...]
    convergence: Tuple[Tuple[str, int], ...]
    csw_facts: Tuple[str, ...]
    candidate: Tuple[Tuple[str, str], ...]
    route_candidates: Tuple[str, ...] = ()
    protocol_version: str = PROTOCOL_VERSION
    protocol_hash: str = PROTOCOL_HASH

    def __post_init__(self) -> None:
        _text(self.request_id, "request_id")
        if not isinstance(self.sequence, int) or self.sequence < 1:
            raise ValueError("sequence must be positive")
        _digest(self.state_digest, "state_digest")
        _digest(self.policy_hash, "policy_hash")
        _text(self.backend_id, "backend_id")
        _text(self.model_id, "model_id")
        if self.calibration_hash:
            _digest(self.calibration_hash, "calibration_hash")
        if self.protocol_version != PROTOCOL_VERSION:
            raise ValueError("unsupported System-1 protocol version")
        if self.protocol_hash != PROTOCOL_HASH:
            raise ValueError("unsupported System-1 protocol hash")
        if not self.questions or len(set(self.questions)) != len(self.questions):
            raise ValueError("questions must be unique and non-empty")
        if any(question not in QUESTION_FIELDS for question in self.questions):
            raise ValueError("unsupported System-1 question")
        _text(self.objective, "objective", 1024)
        if len(self.criteria) > 8 or len(self.recent_activity) > 12 or len(self.csw_facts) > 12:
            raise ValueError("System-1 frame exceeds a bounded projection")
        if len(self.convergence) > 8 or len(self.candidate) > 12 or len(self.route_candidates) > 32:
            raise ValueError("System-1 frame exceeds a bounded projection")
        for criterion_id, claim in self.criteria:
            _text(criterion_id, "criterion_id", 128)
            _text(claim, "criterion claim", 256)
        for item in self.recent_activity + self.csw_facts:
            _text(item, "typed activity", 128)
        for key, value in self.convergence:
            _text(key, "projection key", 128)
            if not isinstance(value, int) or value < 0:
                raise ValueError("convergence values must be non-negative integers")
        for key, value in self.candidate:
            _text(key, "projection key", 128)
            _text(value, "projection value", 256)
        for route in self.route_candidates:
            _text(route, "route candidate", 128)

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "protocol_hash": self.protocol_hash,
            "identity": self.identity.to_dict(), "request_id": self.request_id,
            "sequence": self.sequence, "state_digest": self.state_digest,
            "policy_hash": self.policy_hash, "backend_id": self.backend_id,
            "model_id": self.model_id, "calibration_hash": self.calibration_hash,
            "questions": list(self.questions), "objective": self.objective,
            "criteria": [{"criterion_id": key, "claim": value}
                         for key, value in self.criteria],
            "recent_activity": list(self.recent_activity),
            "convergence": dict(self.convergence), "csw_facts": list(self.csw_facts),
            "candidate": dict(self.candidate),
            "route_candidates": list(self.route_candidates),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SystemOneFrame":
        fields = {"protocol_version", "protocol_hash", "identity", "request_id", "sequence",
                  "state_digest", "policy_hash", "backend_id", "model_id",
                  "calibration_hash", "questions", "objective", "criteria",
                  "recent_activity", "convergence", "csw_facts", "candidate",
                  "route_candidates"}
        if not isinstance(data, Mapping) or set(data) != fields:
            raise ValueError("System-1 frame contains missing or unknown fields")
        identity = data["identity"]
        identity_fields = {"namespace_id", "application_id", "application_instance_id",
                           "agent_id", "agent_instance_id", "session_id", "task_id",
                           "attempt_id", "interaction_id"}
        if not isinstance(identity, Mapping) or set(identity) != identity_fields:
            raise ValueError("System-1 frame identity is malformed")
        criteria = data["criteria"]
        if not isinstance(criteria, list) or any(
                not isinstance(item, Mapping) or set(item) != {"criterion_id", "claim"}
                for item in criteria):
            raise ValueError("System-1 criteria projection is malformed")
        if not isinstance(data["convergence"], Mapping):
            raise ValueError("System-1 convergence projection is malformed")
        if not isinstance(data["candidate"], Mapping):
            raise ValueError("System-1 candidate projection is malformed")
        for key in ("questions", "recent_activity", "csw_facts", "route_candidates"):
            if not isinstance(data[key], list):
                raise ValueError(f"System-1 {key} projection must be an array")
        return cls(
            identity=SystemOneIdentity(**dict(identity)), request_id=data["request_id"],
            sequence=data["sequence"], state_digest=data["state_digest"],
            policy_hash=data["policy_hash"], backend_id=data["backend_id"],
            model_id=data["model_id"], calibration_hash=data["calibration_hash"],
            questions=tuple(data["questions"]), objective=data["objective"],
            criteria=tuple((item["criterion_id"], item["claim"]) for item in criteria),
            recent_activity=tuple(data["recent_activity"]),
            convergence=tuple(sorted(data["convergence"].items())),
            csw_facts=tuple(data["csw_facts"]),
            candidate=tuple(sorted(data["candidate"].items())),
            route_candidates=tuple(data["route_candidates"]),
            protocol_version=data["protocol_version"], protocol_hash=data["protocol_hash"])

    @property
    def frame_hash(self) -> str:
        raw = json.dumps(self.canonical_payload(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SystemOneJudgment:
    identity: SystemOneIdentity
    request_id: str
    sequence: int
    state_digest: str
    policy_hash: str
    backend_id: str
    model_id: str
    calibration_hash: str
    action_relation: Optional[str] = None
    progress_relation: Optional[str] = None
    retry_relation: Optional[str] = None
    goal_drift: Optional[str] = None
    route_preference: Optional[str] = None
    confidence: float = 0.0
    protocol_version: str = PROTOCOL_VERSION
    protocol_hash: str = PROTOCOL_HASH

    def __post_init__(self) -> None:
        _text(self.request_id, "request_id")
        if not isinstance(self.sequence, int) or self.sequence < 1:
            raise ValueError("sequence must be positive")
        _digest(self.state_digest, "state_digest")
        _digest(self.policy_hash, "policy_hash")
        _text(self.backend_id, "backend_id")
        _text(self.model_id, "model_id")
        if self.calibration_hash:
            _digest(self.calibration_hash, "calibration_hash")
        if self.protocol_version != PROTOCOL_VERSION:
            raise ValueError("unsupported System-1 protocol version")
        if self.protocol_hash != PROTOCOL_HASH:
            raise ValueError("unsupported System-1 protocol hash")
        for field, allowed in (("action_relation", ACTION_RELATIONS),
                               ("progress_relation", PROGRESS_RELATIONS),
                               ("retry_relation", RETRY_RELATIONS),
                               ("goal_drift", GOAL_DRIFT)):
            value = getattr(self, field)
            if value is not None and value not in allowed:
                raise ValueError(f"invalid {field}")
        if self.route_preference is not None:
            _text(self.route_preference, "route_preference", 128)
        if not isinstance(self.confidence, (int, float)) or not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")

    def to_dict(self) -> dict[str, Any]:
        result = {"protocol_version": self.protocol_version,
                  "protocol_hash": self.protocol_hash,
                  "identity": self.identity.to_dict(), "request_id": self.request_id,
                  "sequence": self.sequence, "state_digest": self.state_digest,
                  "policy_hash": self.policy_hash, "backend_id": self.backend_id,
                  "model_id": self.model_id, "calibration_hash": self.calibration_hash,
                  "confidence": float(self.confidence)}
        for key in ("action_relation", "progress_relation", "retry_relation", "goal_drift",
                    "route_preference"):
            value = getattr(self, key)
            if value is not None:
                result[key] = value
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SystemOneJudgment":
        allowed = {"protocol_version", "identity", "request_id", "sequence", "state_digest",
                  "policy_hash", "backend_id", "model_id", "calibration_hash", "confidence",
                   "protocol_hash", "action_relation", "progress_relation", "retry_relation", "goal_drift",
                   "route_preference"}
        if not isinstance(data, Mapping) or set(data) - allowed:
            raise ValueError("System-1 response contains unknown fields")
        required = allowed - {"action_relation", "progress_relation", "retry_relation",
                              "goal_drift", "route_preference"}
        if not required.issubset(data):
            raise ValueError("System-1 response is missing required fields")
        identity_data = data["identity"]
        if not isinstance(identity_data, Mapping) or set(identity_data) != {
                "namespace_id", "application_id", "application_instance_id", "agent_id",
                "agent_instance_id", "session_id", "task_id", "attempt_id", "interaction_id"}:
            raise ValueError("System-1 response identity is malformed")
        return cls(identity=SystemOneIdentity(**dict(identity_data)),
                   **{key: data[key] for key in allowed - {"identity"} if key in data})
