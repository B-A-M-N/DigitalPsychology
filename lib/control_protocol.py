"""Versioned, bounded wire contracts for host initiated DP control ticks."""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Mapping, Sequence

from .behavior_state import (CSWBehaviorSnapshot, FrameworkSignal,
                             GoalSetSnapshot, StateWorkOption)
from .outcomes import HostOutcomeFact
from .runtime.contracts import ActionCandidate, RuntimeIdentity, RuntimeObservation


PROTOCOL_VERSION = "1.2.0"
MAX_CONTROL_PAYLOAD_BYTES = 131_072
CONTROL_BOUNDARY_KINDS = frozenset({
    "task_start", "pre_model_call", "pre_action", "post_action",
    "validator", "workflow", "operator", "pre_completion",
})


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must contain JSON values") from exc
    if len(encoded.encode("utf-8")) > MAX_CONTROL_PAYLOAD_BYTES:
        raise ValueError(f"{name} exceeds the control protocol size limit")
    return value


def _sequence(value: Any, name: str, *, maximum: int) -> tuple[Any, ...]:
    if not isinstance(value, (tuple, list)) or len(value) > maximum:
        raise ValueError(f"{name} must be a sequence bounded to {maximum}")
    return tuple(value)


def parse_goal_set(value: Any) -> GoalSetSnapshot:
    data = dict(_mapping(value, "goal_set"))
    if set(data) != {"revision", "items"}:
        raise ValueError("goal_set fields are invalid")
    items = _sequence(data["items"], "goal_set.items", maximum=256)
    return GoalSetSnapshot(revision=data["revision"], items=items)


def parse_csw_snapshot(value: Any) -> CSWBehaviorSnapshot:
    data = dict(_mapping(value, "csw_snapshot"))
    expected = {"revision", "active_stateworks", "eligible_stateworks",
                "workflow_states", "snapshot_id", "workflow_revisions",
                "eligible_steps"}
    if set(data) != expected:
        raise ValueError("CSW snapshot fields are invalid")
    options = _sequence(data["eligible_stateworks"], "eligible_stateworks", maximum=32)
    return CSWBehaviorSnapshot(
        revision=data["revision"],
        active_stateworks=tuple(data["active_stateworks"]),
        eligible_stateworks=tuple(
            item if isinstance(item, StateWorkOption) else StateWorkOption(**dict(item))
            for item in options),
        workflow_states=dict(data["workflow_states"]),
        snapshot_id=data["snapshot_id"],
        workflow_revisions=dict(data["workflow_revisions"]),
        eligible_steps={key: tuple(steps) for key, steps in
                        dict(data["eligible_steps"]).items()},
    )


@dataclass(frozen=True)
class ControlSessionDescriptor:
    identity: RuntimeIdentity
    objective: str
    objective_criteria: tuple[str, ...]
    initial_goal_set: GoalSetSnapshot
    available_frameworks: tuple[str, ...]
    csw_authority_identity: Mapping[str, str]
    host_capabilities: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.identity, RuntimeIdentity):
            raise ValueError("control session identity is invalid")
        if not isinstance(self.objective, str) or not self.objective.strip() \
                or len(self.objective) > 1024:
            raise ValueError("control session objective must be bounded text")
        criteria = _sequence(self.objective_criteria, "objective_criteria", maximum=256)
        if not criteria or len(criteria) != len(set(criteria)) or any(
                not isinstance(item, str) or not item or len(item) > 128
                for item in criteria):
            raise ValueError("objective_criteria must contain unique bounded IDs")
        if not isinstance(self.initial_goal_set, GoalSetSnapshot) or {
                item.criterion_id for item in self.initial_goal_set.items} != set(criteria):
            raise ValueError("initial goal set must cover the complete objective")
        frameworks = _sequence(self.available_frameworks, "available_frameworks", maximum=16)
        if (len(frameworks) != len(set(frameworks))
                or any(not isinstance(item, str) or item.lower() not in {
                    "owl", "anchor", "fuse", "ward", "flow", "sispis", "dox"}
                       for item in frameworks)):
            raise ValueError("available frameworks are invalid")
        authority = dict(_mapping(self.csw_authority_identity, "csw_authority_identity"))
        if len(authority) > 16 or any(
                not isinstance(key, str) or not key or len(key) > 64
                or not isinstance(value, str) or not value or len(value) > 256
                for key, value in authority.items()):
            raise ValueError("CSW authority identity is invalid")
        capabilities = dict(_mapping(self.host_capabilities, "host_capabilities"))
        if len(capabilities) > 32:
            raise ValueError("host capabilities exceed their bound")
        object.__setattr__(self, "objective_criteria", tuple(criteria))
        object.__setattr__(self, "available_frameworks", tuple(item.lower() for item in frameworks))
        object.__setattr__(self, "csw_authority_identity", authority)
        object.__setattr__(self, "host_capabilities", capabilities)

    @property
    def control_session_id(self) -> str:
        return self.identity.scope

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": PROTOCOL_VERSION,
            "identity": self.identity.to_dict(),
            "objective": self.objective,
            "objective_criteria": list(self.objective_criteria),
            "initial_goal_set": self.initial_goal_set.to_dict(),
            "available_frameworks": list(self.available_frameworks),
            "csw_authority_identity": dict(self.csw_authority_identity),
            "host_capabilities": dict(self.host_capabilities),
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ControlSessionDescriptor":
        data = dict(_mapping(value, "control session"))
        expected = {"protocol_version", "identity", "objective", "objective_criteria",
                    "initial_goal_set", "available_frameworks", "csw_authority_identity",
                    "host_capabilities"}
        if set(data) != expected or data["protocol_version"] != PROTOCOL_VERSION:
            raise ValueError("control session protocol version or fields are invalid")
        return cls(
            identity=RuntimeIdentity(**dict(data["identity"])),
            objective=data["objective"],
            objective_criteria=tuple(data["objective_criteria"]),
            initial_goal_set=parse_goal_set(data["initial_goal_set"]),
            available_frameworks=tuple(data["available_frameworks"]),
            csw_authority_identity=dict(data["csw_authority_identity"]),
            host_capabilities=dict(data["host_capabilities"]),
        )


@dataclass(frozen=True)
class ControlTickRequest:
    control_session_id: str
    tick_id: str
    expected_behavior_revision: int
    cfw_lifecycle_revision: int
    observation: RuntimeObservation
    goal_set: GoalSetSnapshot
    cfw_signals: tuple[FrameworkSignal, ...]
    csw_snapshot: CSWBehaviorSnapshot
    outcome_facts: tuple[HostOutcomeFact, ...] = ()
    boundary_kind: str | None = None
    boundary_sequence: int = 0

    def __post_init__(self) -> None:
        for name in ("control_session_id", "tick_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value or len(value) > 256:
                raise ValueError(f"{name} must be bounded text")
        kind = self.boundary_kind or (
            "post_action" if self.observation.action_completed else "pre_action")
        if kind not in CONTROL_BOUNDARY_KINDS:
            raise ValueError("control boundary kind is unsupported")
        if (type(self.boundary_sequence) is not int or self.boundary_sequence < 0):
            raise ValueError("boundary_sequence must be a non-negative integer")
        if (type(self.expected_behavior_revision) is not int
                or self.expected_behavior_revision < 0):
            raise ValueError("expected_behavior_revision must be non-negative")
        # -1 represents a connected CFW runtime that has not applied an
        # external behavior projection yet. The first committed directive
        # advances it to revision 0 or later.
        if (type(self.cfw_lifecycle_revision) is not int
                or self.cfw_lifecycle_revision < -1):
            raise ValueError("cfw_lifecycle_revision must be at least -1")
        signals = _sequence(self.cfw_signals, "cfw_signals", maximum=32)
        outcomes = _sequence(self.outcome_facts, "outcome_facts", maximum=128)
        if any(not isinstance(item, FrameworkSignal) for item in signals):
            raise ValueError("CFW signals are malformed")
        if any(not isinstance(item, HostOutcomeFact) for item in outcomes):
            raise ValueError("host outcome facts are malformed")
        if not isinstance(self.goal_set, GoalSetSnapshot):
            raise ValueError("goal_set is invalid")
        if not isinstance(self.csw_snapshot, CSWBehaviorSnapshot):
            raise ValueError("csw_snapshot is invalid")
        if not isinstance(self.observation, RuntimeObservation):
            raise ValueError("observation is invalid")
        if self.observation.action_completed != (kind == "post_action"):
            raise ValueError("only post_action boundaries confirm a completed host action")
        if (kind == "pre_action") != (self.observation.candidate_action is not None):
            raise ValueError("pre_action boundaries require exactly one action candidate")
        if self.observation.goal_set != self.goal_set:
            raise ValueError("observation and control request goal sets differ")
        refs = set(self.observation.evidence_refs)
        outcome_refs = [item.evidence_ref for item in outcomes]
        if len(outcome_refs) != len(set(outcome_refs)):
            raise ValueError("host outcome facts must have unique evidence references")
        if any(item.evidence_ref not in refs for item in outcomes):
            raise ValueError("outcome facts must reference evidence in this observation")
        object.__setattr__(self, "cfw_signals", tuple(signals))
        object.__setattr__(self, "outcome_facts", tuple(outcomes))
        object.__setattr__(self, "boundary_kind", kind)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ControlTickRequest":
        data = dict(_mapping(value, "control tick"))
        expected = {"protocol_version", "control_session_id", "tick_id",
                    "expected_behavior_revision", "cfw_lifecycle_revision",
                    "observation", "goal_set", "cfw_signals", "csw_snapshot",
                    "outcome_facts", "boundary_kind", "boundary_sequence"}
        if set(data) != expected or data["protocol_version"] != PROTOCOL_VERSION:
            raise ValueError("control tick protocol version or fields are invalid")
        goal_set = parse_goal_set(data["goal_set"])
        observation_data = dict(data["observation"])
        if "goal_set" in observation_data:
            raise ValueError("observation must not duplicate the goal set")
        candidate = observation_data.get("candidate_action")
        if candidate is not None:
            if not isinstance(candidate, Mapping):
                raise ValueError("candidate_action must be an object or null")
            observation_data["candidate_action"] = ActionCandidate(
                **dict(candidate))
        observation = RuntimeObservation(**observation_data, goal_set=goal_set)
        signals = _sequence(data["cfw_signals"], "cfw_signals", maximum=32)
        outcome_facts = _sequence(data["outcome_facts"], "outcome_facts", maximum=128)
        return cls(
            control_session_id=data["control_session_id"], tick_id=data["tick_id"],
            expected_behavior_revision=data["expected_behavior_revision"],
            cfw_lifecycle_revision=data["cfw_lifecycle_revision"],
            observation=observation, goal_set=goal_set,
            cfw_signals=tuple(FrameworkSignal(**dict(item)) for item in signals),
            csw_snapshot=parse_csw_snapshot(data["csw_snapshot"]),
            outcome_facts=tuple(HostOutcomeFact(**dict(item)) for item in outcome_facts),
            boundary_kind=data["boundary_kind"],
            boundary_sequence=data["boundary_sequence"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": PROTOCOL_VERSION,
            "control_session_id": self.control_session_id,
            "tick_id": self.tick_id,
            "boundary_kind": self.boundary_kind,
            "boundary_sequence": self.boundary_sequence,
            "expected_behavior_revision": self.expected_behavior_revision,
            "cfw_lifecycle_revision": self.cfw_lifecycle_revision,
            "observation": {
                "action_index": self.observation.action_index,
                "action_completed": self.observation.action_completed,
                "progress_made": self.observation.progress_made,
                "regression_observed": self.observation.regression_observed,
                "artifact_changed": self.observation.artifact_changed,
                "actions_since_goal_progress": self.observation.actions_since_goal_progress,
                "equivalent_retry_count": self.observation.equivalent_retry_count,
                "context_pressure": self.observation.context_pressure,
                "criterion_status": dict(self.observation.criterion_status),
                "evidence_refs": list(self.observation.evidence_refs),
                "active_trigger_codes": list(self.observation.active_trigger_codes),
                "approach_epoch": self.observation.approach_epoch,
                "candidate_action": (self.observation.candidate_action.to_dict()
                                     if self.observation.candidate_action else None),
            },
            "goal_set": self.goal_set.to_dict(),
            "cfw_signals": [{"source": item.source, "code": item.code,
                              "active": item.active, "severity": item.severity,
                              "criterion_id": item.criterion_id,
                              "evidence_refs": list(item.evidence_refs)}
                             for item in self.cfw_signals],
            "csw_snapshot": self.csw_snapshot.to_dict(),
            "outcome_facts": [item.__dict__ for item in self.outcome_facts],
        }


@dataclass(frozen=True)
class ControlCommitRequest:
    control_session_id: str
    tick_id: str
    decision_id: str
    basis_digest: str
    applied_directive_ids: tuple[str, ...]
    cfw_lifecycle_revision: int
    csw_snapshot: CSWBehaviorSnapshot

    def __post_init__(self) -> None:
        for name in ("control_session_id", "tick_id", "decision_id", "basis_digest"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value or len(value) > 256:
                raise ValueError(f"{name} must be bounded text")
        if len(self.basis_digest) != 64 or any(
                char not in "0123456789abcdef" for char in self.basis_digest):
            raise ValueError("basis_digest must be a SHA-256 digest")
        receipts = _sequence(self.applied_directive_ids, "applied_directive_ids", maximum=32)
        if len(receipts) != len(set(receipts)) or any(
                not isinstance(item, str) or not item or len(item) > 128
                for item in receipts):
            raise ValueError("directive application receipts are malformed")
        if type(self.cfw_lifecycle_revision) is not int or self.cfw_lifecycle_revision < 0:
            raise ValueError("CFW lifecycle revision must be non-negative")
        if not isinstance(self.csw_snapshot, CSWBehaviorSnapshot):
            raise ValueError("post-application CSW snapshot is malformed")
        object.__setattr__(self, "applied_directive_ids", tuple(receipts))

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ControlCommitRequest":
        data = dict(_mapping(value, "control commit"))
        expected = {"protocol_version", "control_session_id", "tick_id", "decision_id",
                    "basis_digest", "applied_directive_ids", "cfw_lifecycle_revision",
                    "csw_snapshot"}
        if set(data) != expected or data["protocol_version"] != PROTOCOL_VERSION:
            raise ValueError("control commit protocol version or fields are invalid")
        return cls(
            control_session_id=data["control_session_id"], tick_id=data["tick_id"],
            decision_id=data["decision_id"],
            basis_digest=data["basis_digest"],
            applied_directive_ids=tuple(data["applied_directive_ids"]),
            cfw_lifecycle_revision=data["cfw_lifecycle_revision"],
            csw_snapshot=parse_csw_snapshot(data["csw_snapshot"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"protocol_version": PROTOCOL_VERSION,
                "control_session_id": self.control_session_id, "tick_id": self.tick_id,
                "decision_id": self.decision_id, "basis_digest": self.basis_digest,
                "applied_directive_ids": list(self.applied_directive_ids),
                "cfw_lifecycle_revision": self.cfw_lifecycle_revision,
                "csw_snapshot": self.csw_snapshot.to_dict()}


@dataclass(frozen=True)
class ControlAbortRequest:
    control_session_id: str
    tick_id: str
    decision_id: str
    basis_digest: str
    applied_directive_ids: tuple[str, ...]
    rollback_completed: bool
    failure_class: str
    cfw_lifecycle_revision: int
    csw_snapshot: CSWBehaviorSnapshot

    def __post_init__(self) -> None:
        for name in ("control_session_id", "tick_id", "decision_id", "basis_digest"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value or len(value) > 256:
                raise ValueError(f"{name} must be bounded text")
        if len(self.basis_digest) != 64 or any(
                char not in "0123456789abcdef" for char in self.basis_digest):
            raise ValueError("basis_digest must be a SHA-256 digest")
        receipts = _sequence(self.applied_directive_ids, "applied_directive_ids", maximum=32)
        if len(receipts) != len(set(receipts)) or any(
                not isinstance(item, str) or not item or len(item) > 128
                for item in receipts):
            raise ValueError("directive application receipts are malformed")
        if not isinstance(self.rollback_completed, bool):
            raise ValueError("rollback_completed must be boolean")
        if (not isinstance(self.failure_class, str) or not self.failure_class
                or len(self.failure_class) > 128):
            raise ValueError("failure_class must be bounded text")
        if type(self.cfw_lifecycle_revision) is not int or self.cfw_lifecycle_revision < -1:
            raise ValueError("CFW lifecycle revision must be at least -1")
        if not isinstance(self.csw_snapshot, CSWBehaviorSnapshot):
            raise ValueError("post-rollback CSW snapshot is malformed")
        object.__setattr__(self, "applied_directive_ids", tuple(receipts))

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ControlAbortRequest":
        data = dict(_mapping(value, "control abort"))
        expected = {"protocol_version", "control_session_id", "tick_id", "decision_id",
                    "basis_digest", "applied_directive_ids", "rollback_completed",
                    "failure_class", "cfw_lifecycle_revision", "csw_snapshot"}
        if set(data) != expected or data["protocol_version"] != PROTOCOL_VERSION:
            raise ValueError("control abort protocol version or fields are invalid")
        return cls(
            control_session_id=data["control_session_id"], tick_id=data["tick_id"],
            decision_id=data["decision_id"], basis_digest=data["basis_digest"],
            applied_directive_ids=tuple(data["applied_directive_ids"]),
            rollback_completed=data["rollback_completed"],
            failure_class=data["failure_class"],
            cfw_lifecycle_revision=data["cfw_lifecycle_revision"],
            csw_snapshot=parse_csw_snapshot(data["csw_snapshot"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"protocol_version": PROTOCOL_VERSION,
                "control_session_id": self.control_session_id,
                "tick_id": self.tick_id, "decision_id": self.decision_id,
                "basis_digest": self.basis_digest,
                "applied_directive_ids": list(self.applied_directive_ids),
                "rollback_completed": self.rollback_completed,
                "failure_class": self.failure_class,
                "cfw_lifecycle_revision": self.cfw_lifecycle_revision,
                "csw_snapshot": self.csw_snapshot.to_dict()}


def bounded_control_mapping(value: Any, name: str) -> dict[str, Any]:
    return dict(_mapping(value, name))


__all__ = ["MAX_CONTROL_PAYLOAD_BYTES", "PROTOCOL_VERSION",
           "ControlAbortRequest", "ControlCommitRequest", "ControlSessionDescriptor", "ControlTickRequest",
           "bounded_control_mapping", "parse_csw_snapshot", "parse_goal_set"]
