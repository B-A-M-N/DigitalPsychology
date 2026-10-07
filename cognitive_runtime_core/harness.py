"""Harness capability negotiation and reproducible adapter identity.

Harness adapters translate product-specific events into runtime lifecycle
contracts. These values describe only the boundaries the harness exposes;
they do not claim access to hidden model inference or planning internals.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
import math
import re
from types import MappingProxyType
from typing import Any, Callable, Mapping, Protocol, Sequence, runtime_checkable

from .actions import freeze_json_object
from .completion import ValidatorCompletionEvidence


HARNESS_ADAPTER_PROTOCOL_VERSION = "1.1.0"
HARNESS_IDENTITY_SCHEMA_VERSION = "1.1.0"

_CAPABILITY_FIELDS = (
    "model_boundary",
    "planning_boundary",
    "pre_action",
    "post_action",
    "action_blocking",
    "context_projection",
    "validator_boundary",
    "completion_interception",
    "multi_agent_identity",
)
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._+-]{0,127}$")
_PIN_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
_EVENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_CONTROL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_CONTROL_CRITERIA = 64
_MAX_CONTROL_EVIDENCE_REFS = (_MAX_CONTROL_CRITERIA * 64) + 64
_MAX_CONTROL_JSON_BYTES = 2 * 1024 * 1024


def _control_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _CONTROL_ID_RE.fullmatch(value):
        raise ValueError(f"{label} must be a bounded identifier")
    return value


def _unique_control_ids(values: Any, label: str, *,
                        maximum: int = _MAX_CONTROL_CRITERIA) -> tuple[str, ...]:
    if (not isinstance(values, (tuple, list)) or len(values) > maximum
            or any(not isinstance(value, str) for value in values)
            or len(values) != len(set(values))):
        raise ValueError(f"{label} must be a bounded sequence of unique identifiers")
    return tuple(_control_id(value, label) for value in values)


def _control_text(value: Any, label: str, maximum: int, *,
                  allow_empty: bool = False) -> str:
    if (not isinstance(value, str) or len(value) > maximum
            or (not allow_empty and not value.strip())):
        raise ValueError(f"{label} must be bounded {'text' if allow_empty else 'non-empty text'}")
    return value


def _control_json_size(value: Mapping[str, Any], label: str) -> None:
    try:
        encoded = json.dumps(value, ensure_ascii=True, sort_keys=True,
                             separators=(",", ":"), allow_nan=False).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise ValueError(f"{label} is not bounded JSON") from exc
    if len(encoded) > _MAX_CONTROL_JSON_BYTES:
        raise ValueError(f"{label} exceeds the {_MAX_CONTROL_JSON_BYTES}-byte bound")


@dataclass(frozen=True)
class TaskContract:
    """Harness-neutral task intent and criterion claims supplied by the host."""

    task_id: str
    objective: str
    criterion_claims: Mapping[str, str]
    non_goals: Sequence[str] = ()
    task_kind: str = "general"
    required_criterion_ids: Sequence[str] | None = None

    def __post_init__(self) -> None:
        _control_id(self.task_id, "task contract task_id")
        _control_text(self.objective, "task objective", 4096)
        _control_text(self.task_kind, "task kind", 64)
        if (not isinstance(self.criterion_claims, Mapping)
                or len(self.criterion_claims) > _MAX_CONTROL_CRITERIA):
            raise ValueError("task criterion claims must contain at most 64 entries")
        claims: dict[str, str] = {}
        for criterion_id, claim in self.criterion_claims.items():
            _control_id(criterion_id, "criterion_id")
            claims[criterion_id] = _control_text(claim, "criterion claim", 512)
        if not isinstance(self.non_goals, (tuple, list)) or len(self.non_goals) > 64:
            raise ValueError("task non-goals must be a bounded sequence")
        non_goals = tuple(_control_text(item, "task non-goal", 512)
                          for item in self.non_goals)
        if len(non_goals) != len(set(non_goals)):
            raise ValueError("task non-goals must be unique")
        required = (tuple(claims) if self.required_criterion_ids is None else
                    _unique_control_ids(self.required_criterion_ids,
                                        "required_criterion_ids"))
        if not set(required).issubset(claims):
            raise ValueError("required criteria must be present in criterion_claims")
        object.__setattr__(self, "criterion_claims", MappingProxyType(claims))
        object.__setattr__(self, "non_goals", non_goals)
        object.__setattr__(self, "required_criterion_ids", required)
        _control_json_size(self.to_dict(), "task contract")

    def to_dict(self) -> dict[str, Any]:
        return {"task_id": self.task_id, "objective": self.objective,
                "criterion_claims": dict(self.criterion_claims),
                "non_goals": list(self.non_goals), "task_kind": self.task_kind,
                "required_criterion_ids": list(self.required_criterion_ids or ())}

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "TaskContract":
        expected = {"task_id", "objective", "criterion_claims", "non_goals", "task_kind",
                    "required_criterion_ids"}
        if (not isinstance(value, Mapping) or not {"task_id", "objective",
                "criterion_claims"}.issubset(value) or not set(value).issubset(expected)):
            raise ValueError("task contract fields are invalid")
        return cls(value["task_id"], value["objective"], value["criterion_claims"],
                   value.get("non_goals", ()), value.get("task_kind", "general"),
                   value.get("required_criterion_ids"))


@dataclass(frozen=True)
class ActionProposal:
    """Bounded semantic projection of a host action before execution.

    Raw arguments are excluded. ``parameter_digest`` can bind this summary to
    a host-retained argument record without copying that record into the
    shared runtime.
    """

    action_id: str
    action_class: str
    tool_name: str
    operation: str
    purpose: str
    affected_criterion_ids: Sequence[str]
    parameter_digest: str | None = None
    target_ref_digests: Sequence[str] = ()
    mutating: bool = True
    reversible: bool = False
    external: bool = False
    proposed_reason: str = ""
    approach_epoch: str = ""
    target_kind: str = "workspace"
    relation_certainty: str = "explicit"

    def __post_init__(self) -> None:
        for name in ("action_id",):
            _control_id(getattr(self, name), name)
        for name, limit in (("action_class", 64), ("tool_name", 128),
                            ("operation", 128), ("purpose", 512),
                            ("target_kind", 64)):
            _control_text(getattr(self, name), name, limit)
        _control_text(self.proposed_reason, "proposed_reason", 512, allow_empty=True)
        _control_text(self.approach_epoch, "approach_epoch", 256, allow_empty=True)
        criteria = _unique_control_ids(self.affected_criterion_ids,
                                       "affected_criterion_ids")
        certainty = self.relation_certainty
        if certainty not in {"explicit", "inferred", "unknown"}:
            raise ValueError("relation_certainty must be explicit, inferred, or unknown")
        action_class = self.action_class.lower().replace("-", "_")
        support_only = (action_class in {
            "diagnostic", "support", "read", "observation", "observational"}
            or action_class.startswith(("diagnostic_", "support_", "read_", "observe_")))
        unknown_action = action_class in {"unknown", "unclassified"}
        if not criteria and (not (support_only or unknown_action)
                             or certainty != "unknown"):
            raise ValueError(
                "criterion-free actions require a diagnostic/support/read or "
                "explicitly unknown class with unknown relation certainty")
        if criteria and certainty == "unknown":
            raise ValueError("unknown relation certainty cannot claim affected criteria")
        refs = tuple(self.target_ref_digests)
        if (len(refs) > 16
                or any(not isinstance(ref, str) or not _SHA256_RE.fullmatch(ref)
                       for ref in refs)
                or len(refs) != len(set(refs))):
            raise ValueError("target references must be unique SHA-256 digests")
        if self.parameter_digest is not None and (
                not isinstance(self.parameter_digest, str)
                or not _SHA256_RE.fullmatch(self.parameter_digest)):
            raise ValueError("parameter_digest must be a lowercase SHA-256 digest")
        if any(type(value) is not bool for value in
               (self.mutating, self.reversible, self.external)):
            raise ValueError("action proposal effect flags must be boolean")
        object.__setattr__(self, "affected_criterion_ids", criteria)
        object.__setattr__(self, "target_ref_digests", refs)
        _control_json_size(self.to_dict(), "action proposal")

    def to_dict(self) -> dict[str, Any]:
        return {"action_id": self.action_id, "action_class": self.action_class,
                "tool_name": self.tool_name, "operation": self.operation,
                "purpose": self.purpose,
                "affected_criterion_ids": list(self.affected_criterion_ids),
                "parameter_digest": self.parameter_digest,
                "target_ref_digests": list(self.target_ref_digests),
                "mutating": self.mutating, "reversible": self.reversible,
                "external": self.external, "proposed_reason": self.proposed_reason,
                "approach_epoch": self.approach_epoch, "target_kind": self.target_kind,
                "relation_certainty": self.relation_certainty}

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ActionProposal":
        required = {"action_id", "action_class", "tool_name", "operation", "purpose",
                    "affected_criterion_ids"}
        optional = {"parameter_digest", "target_ref_digests", "mutating", "reversible",
                    "external", "proposed_reason", "approach_epoch", "target_kind",
                    "relation_certainty"}
        if (not isinstance(value, Mapping) or not required.issubset(value)
                or not set(value).issubset(required | optional)):
            raise ValueError("action proposal fields are invalid")
        return cls(**dict(value))


@dataclass(frozen=True)
class ModelBoundaryProjection:
    """Bounded host context offered to a runtime at a model boundary."""

    context: str
    tool_capable: bool

    def __post_init__(self) -> None:
        _control_text(self.context, "model context", 65_536, allow_empty=True)
        if type(self.tool_capable) is not bool:
            raise ValueError("model tool_capable must be boolean")

    def to_dict(self) -> dict[str, Any]:
        return {"context": self.context, "tool_capable": self.tool_capable}

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ModelBoundaryProjection":
        if (not isinstance(value, Mapping)
                or set(value) != {"context", "tool_capable"}):
            raise ValueError("model boundary projection fields are invalid")
        return cls(**dict(value))


@dataclass(frozen=True)
class RuntimeObservationProjection:
    """Bounded host facts about one action outcome, without raw tool output."""

    action_id: str
    action_index: int
    criterion_status: Mapping[str, str]
    action_completed: bool = True
    progress_made: bool = False
    regression_observed: bool = False
    artifact_changed: bool = False
    evidence_refs: Sequence[str] = ()
    criterion_evidence_refs: Mapping[str, Sequence[str]] = field(default_factory=dict)
    context_pressure: float = 0.0
    actions_since_goal_progress: int = 0
    equivalent_retry_count: int = 0
    approach_epoch: str = ""

    def __post_init__(self) -> None:
        _control_id(self.action_id, "observation action_id")
        if type(self.action_index) is not int or not 0 <= self.action_index <= (2 ** 53 - 1):
            raise ValueError("observation action_index must be a safe non-negative integer")
        for name in ("action_completed", "progress_made", "regression_observed",
                     "artifact_changed"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"observation {name} must be boolean")
        if (type(self.context_pressure) not in (int, float)
                or not math.isfinite(float(self.context_pressure))
                or not 0.0 <= float(self.context_pressure) <= 1.0):
            raise ValueError("observation context_pressure must be between 0 and 1")
        for name in ("actions_since_goal_progress", "equivalent_retry_count"):
            value = getattr(self, name)
            if type(value) is not int or not 0 <= value <= 1_000_000:
                raise ValueError(f"observation {name} must be a bounded non-negative integer")
        _control_text(self.approach_epoch, "approach_epoch", 256, allow_empty=True)
        if (not isinstance(self.criterion_status, Mapping)
                or len(self.criterion_status) > _MAX_CONTROL_CRITERIA):
            raise ValueError("observation criterion_status must be a bounded mapping")
        statuses: dict[str, str] = {}
        allowed_statuses = {"unmet", "in_progress", "verified", "blocked", "unknown"}
        for criterion_id, status in self.criterion_status.items():
            _control_id(criterion_id, "criterion_id")
            if not isinstance(status, str) or status not in allowed_statuses:
                raise ValueError("observation criterion status is invalid")
            statuses[criterion_id] = status
        if (not isinstance(self.evidence_refs, (tuple, list))):
            raise ValueError("observation evidence references must be a sequence")
        refs = tuple(self.evidence_refs)
        if (len(refs) > _MAX_CONTROL_EVIDENCE_REFS
                or any(not isinstance(ref, str) or not ref.strip() or len(ref) > 256
                       for ref in refs)
                or len(refs) != len(set(refs))):
            raise ValueError("observation evidence references are malformed")
        if (not isinstance(self.criterion_evidence_refs, Mapping)
                or len(self.criterion_evidence_refs) > _MAX_CONTROL_CRITERIA):
            raise ValueError("criterion evidence mapping exceeds its bound")
        scoped: dict[str, tuple[str, ...]] = {}
        for criterion_id, raw_refs in self.criterion_evidence_refs.items():
            _control_id(criterion_id, "criterion_id")
            if not isinstance(raw_refs, (tuple, list)):
                raise ValueError("criterion evidence references must be a sequence")
            values = tuple(raw_refs)
            if (criterion_id not in statuses or not values or len(values) > 64
                    or any(not isinstance(ref, str) or not ref.strip() or len(ref) > 256
                           for ref in values)
                    or len(values) != len(set(values))
                    or not set(values).issubset(refs)):
                raise ValueError("criterion evidence must reference observed criteria and evidence")
            scoped[criterion_id] = values
        object.__setattr__(self, "criterion_status", MappingProxyType(statuses))
        object.__setattr__(self, "evidence_refs", refs)
        object.__setattr__(self, "criterion_evidence_refs", MappingProxyType(scoped))
        _control_json_size(self.to_dict(), "runtime observation projection")

    def to_dict(self) -> dict[str, Any]:
        return {"action_id": self.action_id, "action_index": self.action_index,
                "criterion_status": dict(self.criterion_status),
                "action_completed": self.action_completed,
                "progress_made": self.progress_made,
                "regression_observed": self.regression_observed,
                "artifact_changed": self.artifact_changed,
                "evidence_refs": list(self.evidence_refs),
                "criterion_evidence_refs": {
                    key: list(value) for key, value in self.criterion_evidence_refs.items()},
                "context_pressure": float(self.context_pressure),
                "actions_since_goal_progress": self.actions_since_goal_progress,
                "equivalent_retry_count": self.equivalent_retry_count,
                "approach_epoch": self.approach_epoch}

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "RuntimeObservationProjection":
        required = {"action_id", "action_index", "criterion_status"}
        optional = {"action_completed", "progress_made", "regression_observed",
                    "artifact_changed", "evidence_refs", "criterion_evidence_refs",
                    "context_pressure", "actions_since_goal_progress",
                    "equivalent_retry_count", "approach_epoch"}
        if (not isinstance(value, Mapping) or not required.issubset(value)
                or not set(value).issubset(required | optional)):
            raise ValueError("runtime observation projection fields are invalid")
        return cls(**dict(value))


@dataclass(frozen=True)
class CompletionProposal:
    """Host claim and evidence summary for runtime completion review."""

    proposal_id: str
    claim: str
    required_criterion_ids: Sequence[str]
    expected_validator_ids: Sequence[str] = ()
    validator_outcomes: Sequence[ValidatorCompletionEvidence | Mapping[str, Any]] = ()
    criterion_evidence_refs: Mapping[str, Sequence[str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _control_id(self.proposal_id, "completion proposal_id")
        _control_text(self.claim, "completion claim", 1024)
        required = _unique_control_ids(self.required_criterion_ids,
                                       "required_criterion_ids")
        if not required:
            raise ValueError("completion proposal requires at least one criterion")
        expected = _unique_control_ids(self.expected_validator_ids,
                                       "expected_validator_ids", maximum=64)
        if (not isinstance(self.validator_outcomes, (tuple, list))
                or len(self.validator_outcomes) > 64):
            raise ValueError("completion validator outcomes exceed their bound")
        outcomes_list: list[ValidatorCompletionEvidence] = []
        for item in self.validator_outcomes:
            if isinstance(item, ValidatorCompletionEvidence):
                outcome = item
            else:
                allowed = {"validator_id", "criterion_ids", "passed", "evidence_ref",
                           "result_digest", "invocation_id"}
                required_fields = {"validator_id", "criterion_ids", "passed"}
                if (not isinstance(item, Mapping)
                        or not required_fields.issubset(item)
                        or not set(item).issubset(allowed)
                        or not isinstance(item["criterion_ids"], (tuple, list))
                        or type(item["passed"]) is not bool):
                    raise ValueError("completion validator outcome fields are invalid")
                outcome = ValidatorCompletionEvidence.from_mapping(item)
            outcomes_list.append(outcome)
        outcomes = tuple(outcomes_list)
        if len({item.validator_id for item in outcomes}) != len(outcomes):
            raise ValueError("completion validator outcomes must be unique")
        if any(item.validator_id not in expected for item in outcomes):
            raise ValueError("completion validator outcome is not expected")
        if any(not set(item.criterion_ids).issubset(required) for item in outcomes):
            raise ValueError("completion validator outcome escapes required criteria")
        if (not isinstance(self.criterion_evidence_refs, Mapping)
                or len(self.criterion_evidence_refs) > _MAX_CONTROL_CRITERIA):
            raise ValueError("completion criterion evidence exceeds its bound")
        evidence: dict[str, tuple[str, ...]] = {}
        for criterion_id, raw_refs in self.criterion_evidence_refs.items():
            _control_id(criterion_id, "criterion_id")
            if not isinstance(raw_refs, (tuple, list)):
                raise ValueError("completion evidence references must be a sequence")
            refs = tuple(raw_refs)
            if (criterion_id not in required or not refs or len(refs) > 64
                    or any(not isinstance(ref, str) or not ref.strip() or len(ref) > 256
                           for ref in refs)):
                raise ValueError("completion criterion evidence is malformed")
            if len(refs) != len(set(refs)):
                raise ValueError("completion criterion evidence is malformed")
            evidence[criterion_id] = refs
        object.__setattr__(self, "required_criterion_ids", required)
        object.__setattr__(self, "expected_validator_ids", expected)
        object.__setattr__(self, "validator_outcomes", outcomes)
        object.__setattr__(self, "criterion_evidence_refs", MappingProxyType(evidence))
        if len(self.evidence_refs) > _MAX_CONTROL_EVIDENCE_REFS:
            raise ValueError("completion evidence references exceed their bound")
        _control_json_size(self.to_dict(), "completion proposal")

    @property
    def evidence_refs(self) -> tuple[str, ...]:
        refs = [ref for values in self.criterion_evidence_refs.values() for ref in values]
        refs.extend(item.evidence_ref for item in self.validator_outcomes
                    if item.evidence_ref is not None)
        return tuple(dict.fromkeys(refs))

    def to_dict(self) -> dict[str, Any]:
        return {"proposal_id": self.proposal_id, "claim": self.claim,
                "required_criterion_ids": list(self.required_criterion_ids),
                "expected_validator_ids": list(self.expected_validator_ids),
                "validator_outcomes": [{
                    "validator_id": item.validator_id,
                    "criterion_ids": list(item.criterion_ids),
                    "passed": item.passed,
                    "evidence_ref": item.evidence_ref,
                    "result_digest": item.result_digest,
                    "invocation_id": item.invocation_id,
                } for item in self.validator_outcomes],
                "criterion_evidence_refs": {
                    key: list(value) for key, value in self.criterion_evidence_refs.items()}}

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "CompletionProposal":
        required = {"proposal_id", "claim", "required_criterion_ids"}
        optional = {"expected_validator_ids", "validator_outcomes",
                    "criterion_evidence_refs"}
        if (not isinstance(value, Mapping) or not required.issubset(value)
                or not set(value).issubset(required | optional)):
            raise ValueError("completion proposal fields are invalid")
        return cls(**dict(value))


ControlData = (TaskContract | ActionProposal | RuntimeObservationProjection
               | CompletionProposal | ModelBoundaryProjection)


def _coerce_control_data(value: Any, expected: type, label: str) -> Any:
    if isinstance(value, expected):
        return value
    if isinstance(value, Mapping):
        return expected.from_mapping(value)
    raise TypeError(f"{label} must be its shared contract type or a structural mapping")


class HarnessBoundary(str, Enum):
    """Canonical lifecycle points independent of a harness event protocol."""

    TASK_START = "task_start"
    MODEL_BOUNDARY = "model_boundary"
    PLANNING_BOUNDARY = "planning_boundary"
    ACTION_PROPOSED = "action_proposed"
    ACTION_AUTHORIZED = "action_authorized"
    ACTION_REJECTED = "action_rejected"
    ACTION_COMPLETED = "action_completed"
    OBSERVATION = "observation"
    VALIDATION_COMPLETED = "validation_completed"
    WORKFLOW_CHANGED = "workflow_changed"
    OPERATOR_OBSERVATION = "operator_observation"
    COMPLETION_PROPOSED = "completion_proposed"
    TASK_END = "task_end"


@dataclass(frozen=True)
class HarnessBoundaryEvent:
    """Normalized, bounded event emitted by a harness adapter.

    Product protocol names and raw event bodies stay in the adapter. Canonical
    task, action, outcome, and completion projections may cross in
    ``control_data``; raw arguments and free-form tool output do not.
    """

    boundary: HarnessBoundary
    event_id: str
    sequence: int
    action_id: str | None = None
    criterion_ids: Sequence[str] = ()
    evidence_refs: Sequence[str] = ()
    control_data: ControlData | Mapping[str, Any] | None = None
    validator_id: str | None = None
    validation_passed: bool | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.boundary, HarnessBoundary):
            raise ValueError("harness boundary must use the canonical enum")
        if not isinstance(self.event_id, str) or not _EVENT_ID_RE.fullmatch(self.event_id):
            raise ValueError("harness event_id must be a bounded identifier")
        if type(self.sequence) is not int or self.sequence < 0:
            raise ValueError("harness event sequence must be a non-negative integer")
        if self.action_id is not None and (
                not isinstance(self.action_id, str) or not self.action_id.strip()
                or len(self.action_id) > 128):
            raise ValueError("harness action_id must be bounded non-empty text")
        if self.validator_id is not None:
            _control_id(self.validator_id, "validator_id")
        if (self.boundary is HarnessBoundary.VALIDATION_COMPLETED):
            if self.validator_id is None or type(self.validation_passed) is not bool:
                raise ValueError("validation completion requires its ID and outcome")
        elif self.validator_id is not None or self.validation_passed is not None:
            raise ValueError("validator fields are only valid on validation boundaries")
        for field_name in ("criterion_ids", "evidence_refs"):
            raw = getattr(self, field_name)
            if not isinstance(raw, (tuple, list)):
                raise ValueError(f"harness {field_name} must be a sequence")
            values = tuple(raw)
            maximum = (_MAX_CONTROL_CRITERIA if field_name == "criterion_ids"
                       else _MAX_CONTROL_EVIDENCE_REFS)
            if (len(values) > maximum
                    or any(not isinstance(item, str) or not item.strip()
                           or len(item) > 256 for item in values)):
                raise ValueError(f"harness {field_name} must be unique bounded text")
            if len(values) != len(set(values)):
                raise ValueError(f"harness {field_name} must be unique bounded text")
            object.__setattr__(self, field_name, values)

        expected_control_type = {
            HarnessBoundary.TASK_START: TaskContract,
            HarnessBoundary.MODEL_BOUNDARY: ModelBoundaryProjection,
            HarnessBoundary.ACTION_PROPOSED: ActionProposal,
            HarnessBoundary.ACTION_COMPLETED: RuntimeObservationProjection,
            HarnessBoundary.OBSERVATION: RuntimeObservationProjection,
            HarnessBoundary.COMPLETION_PROPOSED: CompletionProposal,
        }.get(self.boundary)
        control_data = self.control_data
        if control_data is not None:
            if isinstance(control_data, Mapping):
                if expected_control_type is None:
                    raise ValueError("this harness boundary does not accept typed control data")
                control_data = expected_control_type.from_mapping(control_data)
            if expected_control_type is None or not isinstance(
                    control_data, expected_control_type):
                raise ValueError("harness boundary control data type does not match its boundary")
            object.__setattr__(self, "control_data", control_data)
            data_action_id = getattr(control_data, "action_id", None)
            if data_action_id is not None:
                if self.action_id is not None and self.action_id != data_action_id:
                    raise ValueError("harness event action_id differs from its control data")
                object.__setattr__(self, "action_id", data_action_id)
            data_criteria = (tuple(control_data.criterion_claims)
                             if isinstance(control_data, TaskContract) else None)
            if data_criteria is None:
                data_criteria = getattr(control_data, "affected_criterion_ids", None)
            if data_criteria is None:
                data_criteria = getattr(control_data, "required_criterion_ids", None)
            if data_criteria is None:
                data_criteria = getattr(control_data, "criterion_status", None)
                data_criteria = tuple(data_criteria) if isinstance(data_criteria, Mapping) else None
            if data_criteria is not None:
                normalized_criteria = tuple(data_criteria)
                if self.criterion_ids and tuple(self.criterion_ids) != normalized_criteria:
                    raise ValueError("harness event criteria differ from its control data")
                object.__setattr__(self, "criterion_ids", normalized_criteria)
            data_evidence = getattr(control_data, "evidence_refs", None)
            if data_evidence is None and isinstance(control_data, CompletionProposal):
                data_evidence = control_data.evidence_refs
            if data_evidence is not None:
                normalized_evidence = tuple(data_evidence)
                if self.evidence_refs and tuple(self.evidence_refs) != normalized_evidence:
                    raise ValueError("harness event evidence differs from its control data")
                object.__setattr__(self, "evidence_refs", normalized_evidence)

    def to_dict(self) -> dict[str, Any]:
        result = {
            "boundary": self.boundary.value,
            "event_id": self.event_id,
            "sequence": self.sequence,
            "action_id": self.action_id,
            "criterion_ids": list(self.criterion_ids),
            "evidence_refs": list(self.evidence_refs),
        }
        if self.validator_id is not None:
            result["validator_id"] = self.validator_id
            result["validation_passed"] = self.validation_passed
        if self.control_data is not None:
            result["control_data"] = self.control_data.to_dict()
        return result


@runtime_checkable
class CognitiveHarnessLifecycle(Protocol):
    """Neutral lifecycle port implemented by connected cognitive runtimes."""

    async def observe_boundary(self, event: HarnessBoundaryEvent) -> Mapping[str, Any]: ...

    async def reconcile(self) -> Mapping[str, Any]: ...

    async def close(self) -> None: ...


class CallbackCognitiveHarnessLifecycle:
    """Convenience consumer for callback-backed boundary observers.

    A connected runtime supplies the observation callback. The lifecycle only
    forwards normalized events and optional reconciliation/close hooks; it
    never owns task, action, policy, or completion decisions. Harness adapters
    feed this lifecycle through :class:`CognitiveRuntimeHarnessSession`.
    """

    def __init__(self, observer: Callable[[HarnessBoundaryEvent], Any], *,
                 reconciler: Callable[[], Any] | None = None,
                 closer: Callable[[], Any] | None = None) -> None:
        if not callable(observer):
            raise TypeError("cognitive harness lifecycle requires an event observer")
        if reconciler is not None and not callable(reconciler):
            raise TypeError("harness reconciler must be callable")
        if closer is not None and not callable(closer):
            raise TypeError("harness closer must be callable")
        self._observer = observer
        self._reconciler = reconciler
        self._closer = closer
        self._closed = False

    async def observe_boundary(self, event: HarnessBoundaryEvent) -> Mapping[str, Any]:
        if self._closed:
            raise RuntimeError("cognitive harness lifecycle is closed")
        if not isinstance(event, HarnessBoundaryEvent):
            raise TypeError("cognitive harness lifecycle accepts normalized events only")
        result = await _invoke_callback(self._observer, event)
        if not isinstance(result, Mapping):
            raise TypeError("cognitive harness observer must return a mapping")
        return result

    async def reconcile(self) -> Mapping[str, Any]:
        if self._closed:
            raise RuntimeError("cognitive harness lifecycle is closed")
        if self._reconciler is None:
            return MappingProxyType({"status": "reconciled", "decision": "no_pending_boundary"})
        result = await _invoke_callback(self._reconciler)
        if not isinstance(result, Mapping):
            raise TypeError("harness reconciler must return a mapping")
        return result

    async def close(self) -> None:
        if self._closed:
            return
        if self._closer is not None:
            await _invoke_callback(self._closer)
        self._closed = True


async def _invoke_callback(callback: Callable[..., Any], *args: Any) -> Any:
    """Invoke a host callback without prescribing sync or async host style."""
    import inspect

    result = callback(*args)
    if inspect.isawaitable(result):
        return await result
    return result


class CognitiveRuntimeHarnessSession:
    """Apply one adapter's declared capabilities to normalized lifecycle events.

    The session has no harness-specific event names. It serializes boundaries,
    prevents event replay, and refuses control claims where the pinned adapter
    has not verified the corresponding boundary.
    """

    _MAX_TRACKED_EVENT_IDS = 2048
    _REQUIRED_CAPABILITIES = {
        HarnessBoundary.MODEL_BOUNDARY: ("model_boundary",),
        HarnessBoundary.PLANNING_BOUNDARY: ("planning_boundary",),
        # A typed action candidate is control input only when the adapter can
        # both expose the pre-action boundary and block execution. Without
        # blocking, forwarding it would imply a control surface the runtime
        # cannot enforce.
        HarnessBoundary.ACTION_PROPOSED: ("pre_action", "action_blocking"),
        HarnessBoundary.ACTION_AUTHORIZED: ("pre_action", "action_blocking"),
        HarnessBoundary.ACTION_REJECTED: ("pre_action", "action_blocking"),
        HarnessBoundary.ACTION_COMPLETED: ("post_action",),
        HarnessBoundary.VALIDATION_COMPLETED: ("validator_boundary",),
        HarnessBoundary.COMPLETION_PROPOSED: ("completion_interception",),
    }

    def __init__(self, identity: HarnessAdapterIdentity,
                 lifecycle: CognitiveHarnessLifecycle) -> None:
        if not isinstance(identity, HarnessAdapterIdentity):
            raise TypeError("runtime harness session requires a validated adapter identity")
        for method in ("observe_boundary", "reconcile", "close"):
            if not callable(getattr(lifecycle, method, None)):
                raise TypeError(f"harness lifecycle adapter lacks {method}")
        self.identity = identity
        self.lifecycle = lifecycle
        self._last_sequence = -1
        self._event_ids: dict[str, None] = {}
        self._started = False
        self._ended = False
        self._faulted = False
        self.task_contract: TaskContract | None = None

    async def dispatch(self, event: HarnessBoundaryEvent) -> Mapping[str, Any]:
        """Dispatch a canonical event and return a bounded, normalized outcome."""
        if self._faulted:
            raise RuntimeError("harness lifecycle session requires reconciliation")
        if not isinstance(event, HarnessBoundaryEvent):
            raise TypeError("runtime lifecycle accepts only normalized boundary events")
        if self._ended:
            raise RuntimeError("harness lifecycle session has already ended")
        if event.sequence <= self._last_sequence or event.event_id in self._event_ids:
            raise ValueError("harness lifecycle event is stale or duplicated")
        if not self._started and event.boundary is not HarnessBoundary.TASK_START:
            raise RuntimeError("task start must be the first harness lifecycle event")
        if self._started and event.boundary is HarnessBoundary.TASK_START:
            raise RuntimeError("task start can be dispatched only once")
        self._validate_task_scope(event.control_data)
        if (event.boundary is HarnessBoundary.VALIDATION_COMPLETED
                and self.task_contract is not None
                and not set(event.criterion_ids).issubset(
                    set(self.task_contract.criterion_claims))):
            raise ValueError("validation boundary escapes the active task criteria")

        self._last_sequence = event.sequence
        self._event_ids[event.event_id] = None
        if len(self._event_ids) > self._MAX_TRACKED_EVENT_IDS:
            self._event_ids.pop(next(iter(self._event_ids)))

        if event.boundary is HarnessBoundary.TASK_START:
            self._started = True
            if isinstance(event.control_data, TaskContract):
                self.task_contract = event.control_data
        if not self._supports(event.boundary, event.control_data):
            return self._unavailable(event.boundary)
        result = await self._invoke(self.lifecycle.observe_boundary(event))

        if event.boundary is HarnessBoundary.TASK_END:
            self._ended = True
        return result

    async def observe_boundary(self, event: HarnessBoundaryEvent) -> Mapping[str, Any]:
        """Structural event-ingress alias for hosts and generic adapters."""
        return await self.dispatch(event)

    async def start_task(self, contract: TaskContract | Mapping[str, Any], *,
                         event_id: str, sequence: int) -> Mapping[str, Any]:
        """Open the session with bounded host task intent and criterion claims."""
        value = _coerce_control_data(contract, TaskContract, "task contract")
        return await self.dispatch(HarnessBoundaryEvent(
            HarnessBoundary.TASK_START, event_id, sequence, control_data=value))

    async def propose_action(self, proposal: ActionProposal | Mapping[str, Any], *,
                             event_id: str, sequence: int) -> Mapping[str, Any]:
        """Submit a bounded semantic action summary before host execution."""
        value = _coerce_control_data(proposal, ActionProposal, "action proposal")
        return await self.dispatch(HarnessBoundaryEvent(
            HarnessBoundary.ACTION_PROPOSED, event_id, sequence, control_data=value))

    async def observe_action(self, observation: RuntimeObservationProjection | Mapping[str, Any], *,
                             event_id: str, sequence: int) -> Mapping[str, Any]:
        """Submit host-produced action status, criterion state, and evidence refs."""
        value = _coerce_control_data(
            observation, RuntimeObservationProjection, "runtime observation")
        boundary = (HarnessBoundary.ACTION_COMPLETED if value.action_completed
                    else HarnessBoundary.OBSERVATION)
        return await self.dispatch(HarnessBoundaryEvent(
            boundary, event_id, sequence, control_data=value))

    async def propose_completion(self, proposal: CompletionProposal | Mapping[str, Any], *,
                                 event_id: str, sequence: int) -> Mapping[str, Any]:
        """Pass a host completion claim and evidence for advisory review only."""
        value = _coerce_control_data(proposal, CompletionProposal, "completion proposal")
        return await self.dispatch(HarnessBoundaryEvent(
            HarnessBoundary.COMPLETION_PROPOSED, event_id, sequence,
            control_data=value))

    def _validate_task_scope(self, control_data: ControlData | None) -> None:
        if control_data is None or self.task_contract is None:
            return
        task_criteria = set(self.task_contract.criterion_claims)
        if isinstance(control_data, ActionProposal):
            criteria = set(control_data.affected_criterion_ids)
        elif isinstance(control_data, RuntimeObservationProjection):
            criteria = set(control_data.criterion_status)
        elif isinstance(control_data, CompletionProposal):
            criteria = set(control_data.required_criterion_ids)
            if criteria != set(self.task_contract.required_criterion_ids or ()):
                raise ValueError(
                    "completion criteria must match the task's required criterion set")
        elif isinstance(control_data, ModelBoundaryProjection):
            criteria = set()
        else:
            return
        if not criteria.issubset(task_criteria):
            raise ValueError("harness control data escapes the active task criteria")

    def _supports(self, boundary: HarnessBoundary,
                  control_data: ControlData | None = None) -> bool:
        names = self._REQUIRED_CAPABILITIES.get(boundary, ())
        capabilities = self.identity.effective_capabilities
        return all(getattr(capabilities, name) is True for name in names)

    @staticmethod
    def _unavailable(boundary: HarnessBoundary) -> Mapping[str, Any]:
        return MappingProxyType({
            "status": "degraded",
            "decision": "capability_unavailable",
            "boundary": boundary.value,
        })

    async def _invoke(self, pending: Any) -> Mapping[str, Any]:
        try:
            result = await pending
        except Exception:
            self._faulted = True
            raise
        if not isinstance(result, Mapping):
            self._faulted = True
            raise TypeError("harness lifecycle result must be a mapping")
        if len(result) > 64:
            self._faulted = True
            raise ValueError("harness lifecycle result exceeds its field bound")
        try:
            return freeze_json_object(result, label="harness lifecycle result")
        except (TypeError, ValueError) as exc:
            self._faulted = True
            raise ValueError("harness lifecycle result is not bounded JSON") from exc

    async def reconcile(self) -> Mapping[str, Any]:
        """Ask the adapter to resolve an uncertain boundary before continuing."""
        result = await self._invoke(self.lifecycle.reconcile())
        self._faulted = False
        return result

    async def close(self) -> None:
        await self.lifecycle.close()
        self._ended = True


@dataclass(frozen=True)
class HarnessDispatchSummary:
    """Bounded aggregate for one adapter-to-runtime event stream pass."""

    events_dispatched: int
    first_sequence: int | None
    last_sequence: int | None
    degraded_events: int


async def dispatch_harness_events(session: Any, events: Any, *,
                                  max_events: int = 4096) -> HarnessDispatchSummary:
    """Feed any async stream of canonical events through the session API.

    This is the shared bridge for product adapters. It depends only on the
    structural ``dispatch(event)`` method, so host integrations need not import
    a harness-specific adapter or another product's lifecycle type.
    """
    dispatch = getattr(session, "dispatch", None)
    if not callable(dispatch):
        raise TypeError("harness event session must expose dispatch(event)")
    if type(max_events) is not int or not 1 <= max_events <= 65536:
        raise ValueError("harness event dispatch bound must be between 1 and 65536")
    count = degraded = 0
    first_sequence: int | None = None
    last_sequence: int | None = None
    async for event in events:
        if count >= max_events:
            raise ValueError("harness event stream exceeds its dispatch bound")
        if not isinstance(event, HarnessBoundaryEvent):
            raise TypeError("harness event stream contains a non-canonical event")
        result = await dispatch(event)
        count += 1
        if first_sequence is None:
            first_sequence = event.sequence
        last_sequence = event.sequence
        if (isinstance(result, Mapping)
                and (result.get("status") == "degraded"
                     or bool(result.get("degradation_claims")))):
            degraded += 1
    return HarnessDispatchSummary(count, first_sequence, last_sequence, degraded)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _bounded_version(name: str, value: Any) -> str:
    if (not isinstance(value, str) or value != value.strip()
            or not _VERSION_RE.fullmatch(value)):
        raise ValueError(f"{name} must be bounded version text")
    return value


@dataclass(frozen=True)
class HarnessCapabilities:
    """Bounded set of harness-visible runtime boundaries.

    Every capability is explicit. Omitted values default to unavailable, and
    non-boolean inputs are rejected so truthy strings or integers cannot
    accidentally advertise a boundary.
    """

    model_boundary: bool = False
    planning_boundary: bool = False
    pre_action: bool = False
    post_action: bool = False
    action_blocking: bool = False
    context_projection: bool = False
    validator_boundary: bool = False
    completion_interception: bool = False
    multi_agent_identity: bool = False

    def __post_init__(self) -> None:
        for name in _CAPABILITY_FIELDS:
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"harness capability {name} must be boolean")
        if self.action_blocking and not self.pre_action:
            raise ValueError("action blocking requires a pre-action boundary")

    def to_dict(self) -> dict[str, bool]:
        """Return the complete capability set with a stable field contract."""
        return {name: getattr(self, name) for name in _CAPABILITY_FIELDS}

    def to_json(self) -> str:
        """Serialize capabilities canonically for stable hashing and storage."""
        return _canonical_json(self.to_dict())

    @property
    def capability_hash(self) -> str:
        """Return a domain-separated SHA-256 digest of the capability set."""
        basis = (f"cognitive-runtime-harness-capabilities:{HARNESS_IDENTITY_SCHEMA_VERSION}\n"
                 + self.to_json())
        return _sha256(basis)

    @property
    def degradation_claims(self) -> tuple[str, ...]:
        """Claims the runtime must withhold because a boundary is unavailable."""
        claims = [
            f"{name}_unavailable"
            for name in _CAPABILITY_FIELDS
            if not getattr(self, name)
        ]
        # A turn/model event is not a trace of hidden inference. The capability
        # schema intentionally makes no claim that internal reasoning is visible.
        claims.append("internal_inference_visibility_unavailable")
        return tuple(claims)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "HarnessCapabilities":
        if not isinstance(value, Mapping) or set(value) != set(_CAPABILITY_FIELDS):
            raise ValueError("harness capabilities must contain the complete known field set")
        return cls(**{name: value[name] for name in _CAPABILITY_FIELDS})


class HarnessConformanceLevel(str, Enum):
    """Named evidence levels for harness observation and control."""

    OBSERVATION = "observation"
    BEHAVIORAL_OBSERVATION = "behavioral_observation"
    CONTROL = "control"
    FULL_CONTROL = "full_control"


_CONFORMANCE_CAPABILITIES = {
    HarnessConformanceLevel.OBSERVATION: ("model_boundary",),
    HarnessConformanceLevel.BEHAVIORAL_OBSERVATION: (
        "model_boundary", "post_action", "validator_boundary"),
    HarnessConformanceLevel.CONTROL: (
        "model_boundary", "post_action", "validator_boundary", "pre_action",
        "action_blocking", "context_projection"),
    HarnessConformanceLevel.FULL_CONTROL: (
        "model_boundary", "post_action", "validator_boundary", "pre_action",
        "action_blocking", "context_projection", "completion_interception"),
}


@dataclass(frozen=True)
class HarnessRequirement:
    """Capability-only harness qualification requirement."""

    name: str
    required_capabilities: Sequence[str]
    require_verified_identity: bool = True

    def __post_init__(self) -> None:
        _bounded_version("harness requirement name", self.name)
        if (not isinstance(self.required_capabilities, (tuple, list))
                or len(self.required_capabilities) > len(_CAPABILITY_FIELDS)
                or any(not isinstance(item, str)
                       or item not in _CAPABILITY_FIELDS
                       for item in self.required_capabilities)
                or len(self.required_capabilities) != len(set(
                    self.required_capabilities))):
            raise ValueError("harness requirement capabilities are invalid")
        if type(self.require_verified_identity) is not bool:
            raise ValueError("harness identity requirement flag must be boolean")
        object.__setattr__(self, "required_capabilities",
                           tuple(self.required_capabilities))

    @classmethod
    def for_level(cls, level: HarnessConformanceLevel) -> "HarnessRequirement":
        if not isinstance(level, HarnessConformanceLevel):
            raise ValueError("harness conformance level must use the canonical enum")
        return cls(name=level.value,
                   required_capabilities=_CONFORMANCE_CAPABILITIES[level])

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name,
                "required_capabilities": list(self.required_capabilities),
                "require_verified_identity": self.require_verified_identity}


class HarnessRequirementError(RuntimeError):
    """A verified harness identity does not satisfy a declared requirement."""

    def __init__(self, requirement: HarnessRequirement, *,
                 missing_capabilities: Sequence[str], identity_verified: bool) -> None:
        self.requirement = requirement
        self.missing_capabilities = tuple(missing_capabilities)
        self.identity_verified = identity_verified
        details = []
        if requirement.require_verified_identity and not identity_verified:
            details.append("harness identity is unverified")
        if self.missing_capabilities:
            details.append("missing capabilities: "
                           + ", ".join(self.missing_capabilities))
        super().__init__(f"harness requirement {requirement.name!r} failed: "
                         + ("; ".join(details) or "requirements not met"))


@dataclass(frozen=True)
class HarnessAdapterIdentity:
    """Immutable pins identifying one harness adapter and its host contract."""

    harness_id: str
    adapter_version: str
    host_version: str
    protocol_version: str
    capabilities: HarnessCapabilities
    catalog_pin: Mapping[str, str] = field(default_factory=dict)
    verified: bool = False

    @classmethod
    def unidentified(cls) -> "HarnessAdapterIdentity":
        """Return a fail-closed identity for hosts without an adapter pin."""
        return cls(
            harness_id="unidentified",
            adapter_version="unknown",
            host_version="unknown",
            protocol_version="unknown",
            capabilities=HarnessCapabilities(),
            catalog_pin={},
            verified=False,
        )

    def __post_init__(self) -> None:
        for name in ("harness_id", "adapter_version", "host_version",
                     "protocol_version"):
            object.__setattr__(self, name, _bounded_version(name, getattr(self, name)))
        if self.protocol_version not in {
                HARNESS_ADAPTER_PROTOCOL_VERSION, "unknown"}:
            raise ValueError("harness adapter protocol version is incompatible")
        if not isinstance(self.capabilities, HarnessCapabilities):
            raise ValueError("harness capabilities must be a validated HarnessCapabilities")
        if type(self.verified) is not bool:
            raise ValueError("harness identity verified flag must be boolean")
        if not isinstance(self.catalog_pin, Mapping) or len(self.catalog_pin) > 16:
            raise ValueError("catalog pin must be a bounded string mapping")
        pins: dict[str, str] = {}
        for key, value in self.catalog_pin.items():
            if (not isinstance(key, str) or not _PIN_KEY_RE.fullmatch(key)
                    or not isinstance(value, str) or not value.strip()
                    or len(value) > 256):
                raise ValueError("catalog pin keys and values must be bounded text")
            pins[key] = value
        object.__setattr__(self, "catalog_pin", MappingProxyType(pins))

    @property
    def capability_hash(self) -> str:
        return self.capabilities.capability_hash

    @property
    def effective_capabilities(self) -> HarnessCapabilities:
        """Return only capabilities backed by an explicitly verified identity."""
        return self.capabilities if self.verified else HarnessCapabilities()

    @property
    def effective_capability_hash(self) -> str:
        return self.effective_capabilities.capability_hash

    @property
    def conformance_levels(self) -> tuple[HarnessConformanceLevel, ...]:
        """Return named levels supported by this verified capability set."""
        if not self.verified:
            return ()
        effective = self.effective_capabilities
        return tuple(level for level, required in _CONFORMANCE_CAPABILITIES.items()
                     if all(getattr(effective, name) for name in required))

    def require(self, requirement: HarnessRequirement) -> "HarnessAdapterIdentity":
        """Return this identity or fail before a trial exceeds harness support."""
        if not isinstance(requirement, HarnessRequirement):
            raise TypeError("harness requirement must be a HarnessRequirement")
        effective = self.effective_capabilities
        missing = tuple(name for name in requirement.required_capabilities
                        if not getattr(effective, name))
        if ((requirement.require_verified_identity and not self.verified) or missing):
            raise HarnessRequirementError(requirement,
                                          missing_capabilities=missing,
                                          identity_verified=self.verified)
        return self

    @property
    def degradation_claims(self) -> tuple[str, ...]:
        claims = list(self.effective_capabilities.degradation_claims)
        if not self.verified:
            claims.append("harness_identity_unverified")
        return tuple(claims)

    def to_dict(self) -> dict[str, Any]:
        """Return all reproducibility pins and the derived degradation claims."""
        return {
            "identity_schema_version": HARNESS_IDENTITY_SCHEMA_VERSION,
            "harness_id": self.harness_id,
            "adapter_version": self.adapter_version,
            "host_version": self.host_version,
            "protocol_version": self.protocol_version,
            "capabilities": self.capabilities.to_dict(),
            "capability_hash": self.capability_hash,
            "catalog_pin": dict(self.catalog_pin),
            "verified": self.verified,
            "effective_capabilities": self.effective_capabilities.to_dict(),
            "effective_capability_hash": self.effective_capability_hash,
            "degradation_claims": list(self.degradation_claims),
        }

    def to_json(self) -> str:
        """Serialize the full identity in a deterministic canonical form."""
        return _canonical_json(self.to_dict())

    @property
    def identity_hash(self) -> str:
        """Return a stable digest including capability and host/catalog pins."""
        return _sha256("cognitive-runtime-harness-adapter-identity\n" + self.to_json())

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "HarnessAdapterIdentity":
        declaration_fields = {
            "harness_id", "adapter_version", "host_version", "protocol_version",
            "capabilities", "catalog_pin", "verified",
        }
        canonical_fields = {
            "identity_schema_version", "harness_id", "adapter_version",
            "host_version", "protocol_version", "capabilities",
            "capability_hash", "catalog_pin", "verified",
            "effective_capabilities", "effective_capability_hash",
            "degradation_claims",
        }
        if not isinstance(value, Mapping) or frozenset(value) not in {
                frozenset(declaration_fields), frozenset(canonical_fields)}:
            raise ValueError("harness adapter identity fields are invalid")
        if set(value) == declaration_fields:
            return cls(
                harness_id=value["harness_id"],
                adapter_version=value["adapter_version"],
                host_version=value["host_version"],
                protocol_version=value["protocol_version"],
                capabilities=HarnessCapabilities.from_mapping(value["capabilities"]),
                catalog_pin=value["catalog_pin"],
                verified=value["verified"],
            )
        if value["identity_schema_version"] != HARNESS_IDENTITY_SCHEMA_VERSION:
            raise ValueError("harness adapter identity schema version is incompatible")
        capabilities = HarnessCapabilities.from_mapping(value["capabilities"])
        if value["capability_hash"] != capabilities.capability_hash:
            raise ValueError("harness capability hash does not match its capability set")
        identity = cls(
            harness_id=value["harness_id"],
            adapter_version=value["adapter_version"],
            host_version=value["host_version"],
            protocol_version=value["protocol_version"],
            capabilities=capabilities,
            catalog_pin=value["catalog_pin"],
            verified=value["verified"],
        )
        if value["effective_capabilities"] != identity.effective_capabilities.to_dict():
            raise ValueError("effective harness capabilities do not match verification status")
        if value["effective_capability_hash"] != identity.effective_capability_hash:
            raise ValueError("effective harness capability hash is invalid")
        if value["degradation_claims"] != list(identity.degradation_claims):
            raise ValueError("harness degradation claims do not match its capabilities")
        return identity


__all__ = [
    "ActionProposal",
    "CallbackCognitiveHarnessLifecycle",
    "CompletionProposal",
    "CognitiveHarnessLifecycle",
    "CognitiveRuntimeHarnessSession",
    "HarnessDispatchSummary",
    "HarnessConformanceLevel",
    "HARNESS_ADAPTER_PROTOCOL_VERSION",
    "HARNESS_IDENTITY_SCHEMA_VERSION",
    "HarnessAdapterIdentity",
    "HarnessBoundary",
    "HarnessBoundaryEvent",
    "HarnessCapabilities",
    "HarnessRequirement",
    "HarnessRequirementError",
    "ModelBoundaryProjection",
    "RuntimeObservationProjection",
    "TaskContract",
    "dispatch_harness_events",
]
