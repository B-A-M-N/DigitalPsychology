"""Observational adapter for Athena's public task event and result surfaces.

The adapter depends only on an injected structural port. Canonical events can
be streamed through the shared lifecycle session. Athena's public stream does
not authorize actions or intercept completion on behalf of the host.
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import AsyncIterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from ...harness import (
    ActionProposal,
    HARNESS_ADAPTER_PROTOCOL_VERSION,
    HarnessAdapterIdentity,
    HarnessBoundary,
    HarnessBoundaryEvent,
    HarnessCapabilities,
    HarnessDispatchSummary,
    RuntimeObservationProjection,
    TaskContract,
    dispatch_harness_events,
)

_TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_EVENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_STATUS_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
_MAX_EVIDENCE_REFS = 128
_MAX_REFERENCE_LENGTH = 256
ATHENA_TASK_PORT_CONTRACT_VERSION = "1.1.0"
ATHENA_ADAPTER_VERSION = "1.1.0"


class AthenaTaskPort(Protocol):
    """Structural subset of Athena's public task service API."""

    async def get_task(self, task_id: str) -> Any: ...

    def stream_events(self, task_id: str, after_sequence: int = 0
                      ) -> AsyncIterable[Any]: ...

    async def get_result(self, task_id: str) -> Any: ...


@dataclass(frozen=True)
class AthenaTaskResultProjection:
    """Safe terminal result metadata; free-form summaries are never retained."""

    task_id: str
    status: str
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.task_id, str) or not _TASK_ID_RE.fullmatch(self.task_id):
            raise ValueError("Athena result task_id must be a bounded identifier")
        if not isinstance(self.status, str) or not _STATUS_RE.fullmatch(self.status):
            raise ValueError("Athena result status must be bounded status text")
        refs = tuple(self.evidence_refs)
        if (len(refs) > _MAX_EVIDENCE_REFS
                or any(not isinstance(ref, str) or not ref.strip()
                       or len(ref) > _MAX_REFERENCE_LENGTH for ref in refs)
                or len(refs) != len(set(refs))):
            raise ValueError("Athena result evidence references are malformed")
        object.__setattr__(self, "evidence_refs", refs)

    def to_dict(self) -> dict[str, Any]:
        return {"task_id": self.task_id, "status": self.status,
                "evidence_refs": list(self.evidence_refs)}


# These event names are the subset of Athena's public event taxonomy that
# identifies observation boundaries the shared runtime can represent. Event bodies remain
# private to the adapter and are never copied into a runtime event.
_BOUNDARIES = {
    "TaskCreated": HarnessBoundary.TASK_START,
    "TaskStarted": HarnessBoundary.TASK_START,
    "ModelRequestStarted": HarnessBoundary.MODEL_BOUNDARY,
    "ModelDelta": HarnessBoundary.MODEL_BOUNDARY,
    "ModelReasoningDelta": HarnessBoundary.MODEL_BOUNDARY,
    "ModelResponseCompleted": HarnessBoundary.MODEL_BOUNDARY,
    "ModelRequestFailed": HarnessBoundary.MODEL_BOUNDARY,
    "CapabilityRequested": HarnessBoundary.ACTION_PROPOSED,
    "CapabilityStarted": HarnessBoundary.ACTION_AUTHORIZED,
    "CapabilityCompleted": HarnessBoundary.ACTION_COMPLETED,
    "CapabilityFailed": HarnessBoundary.ACTION_COMPLETED,
    "VerificationCheckCompleted": HarnessBoundary.VALIDATION_COMPLETED,
    "VerificationCompleted": HarnessBoundary.VALIDATION_COMPLETED,
    "ExecutionStarted": HarnessBoundary.ACTION_AUTHORIZED,
    "ExecutionExited": HarnessBoundary.ACTION_COMPLETED,
    "ExecutionInterrupted": HarnessBoundary.ACTION_COMPLETED,
    "ExecutionTimedOut": HarnessBoundary.ACTION_COMPLETED,
    "TaskCompleted": HarnessBoundary.TASK_END,
    "TaskPartial": HarnessBoundary.TASK_END,
    "TaskFailed": HarnessBoundary.TASK_END,
    "TaskCancelled": HarnessBoundary.TASK_END,
    "TaskInterrupted": HarnessBoundary.TASK_END,
}


class AthenaRuntimeAdapter:
    """Normalize Athena's durable task stream without importing Athena code."""

    def __init__(self, task_port: AthenaTaskPort, *, host_version: str = "unknown",
                 verified_identity: HarnessAdapterIdentity | Mapping[str, Any] | None = None
                 ) -> None:
        task = getattr(task_port, "get_task", None)
        stream = getattr(task_port, "stream_events", None)
        result = getattr(task_port, "get_result", None)
        if not callable(task) or not callable(stream) or not callable(result):
            raise TypeError("Athena task port requires get_task, stream_events, and get_result")
        self.task_port = task_port
        self.capabilities = HarnessCapabilities(
            model_boundary=any(
                boundary is HarnessBoundary.MODEL_BOUNDARY
                for boundary in _BOUNDARIES.values()),
            pre_action=HarnessBoundary.ACTION_PROPOSED in _BOUNDARIES.values(),
            post_action=HarnessBoundary.ACTION_COMPLETED in _BOUNDARIES.values(),
            # The public event/result surfaces are observational. They expose
            # no action gate, validator decision hook, context projection,
            # completion interception, planning boundary, or agent identity.
            action_blocking=False,
            validator_boundary=any(
                boundary is HarnessBoundary.VALIDATION_COMPLETED
                for boundary in _BOUNDARIES.values()),
            completion_interception=False,
            context_projection=False,
            planning_boundary=False,
            multi_agent_identity=False,
        )
        # A caller-provided version string is not an attestation of the
        # connected service. Control claims become effective only when a
        # runtime integration supplies a matching, independently verified pin.
        expected_catalog_pin = {
            "athena_task_port_contract": ATHENA_TASK_PORT_CONTRACT_VERSION,
        }
        if verified_identity is None:
            self.identity = HarnessAdapterIdentity(
                harness_id="athena",
                adapter_version=ATHENA_ADAPTER_VERSION,
                host_version=host_version,
                protocol_version=HARNESS_ADAPTER_PROTOCOL_VERSION,
                capabilities=self.capabilities,
                catalog_pin=expected_catalog_pin,
                verified=False,
            )
        else:
            identity = (verified_identity if isinstance(
                verified_identity, HarnessAdapterIdentity) else
                HarnessAdapterIdentity.from_mapping(verified_identity))
            if (identity.harness_id != "athena"
                    or identity.adapter_version != ATHENA_ADAPTER_VERSION
                    or identity.capabilities != self.capabilities
                    or identity.protocol_version != HARNESS_ADAPTER_PROTOCOL_VERSION
                    or identity.catalog_pin != expected_catalog_pin
                    or not identity.verified):
                raise ValueError("verified Athena identity differs from adapter capabilities")
            self.identity = identity

    async def stream_events(self, task_id: str, after_sequence: int = 0
                            ) -> AsyncIterable[HarnessBoundaryEvent]:
        """Yield mapped events after a durable cursor, preserving sequence.

        Unknown Athena event types are validated for task identity and sequence
        integrity, then skipped. Only explicit ``evidence_refs`` are read from
        a supported event payload; no other payload fields cross this seam.
        """
        _validate_task_id(task_id)
        if type(after_sequence) is not int or after_sequence < 0:
            raise ValueError("Athena after_sequence must be a non-negative integer")
        source = self.task_port.stream_events(task_id, after_sequence=after_sequence)
        if not isinstance(source, AsyncIterable):
            raise TypeError("Athena stream_events must return an async event stream")

        last_sequence = after_sequence
        task_start_seen = False
        async for raw_event in source:
            event_id = _field(raw_event, "id")
            sequence = _field(raw_event, "sequence")
            event_type = _field(raw_event, "type")
            event_task_id = _field(raw_event, "task_id")
            if not isinstance(event_id, str) or not _EVENT_ID_RE.fullmatch(event_id):
                raise ValueError("Athena event id must be a bounded identifier")
            if type(sequence) is not int or sequence <= last_sequence:
                raise ValueError("Athena durable event sequence must increase after the cursor")
            if event_task_id != task_id:
                raise ValueError("Athena event task_id differs from the requested task")
            if not isinstance(event_type, str) or not event_type or len(event_type) > 128:
                raise ValueError("Athena event type must be bounded text")
            last_sequence = sequence

            boundary = _BOUNDARIES.get(event_type)
            if boundary is None:
                continue
            # Athena can report both creation and start for one task. Keep one
            # canonical TASK_START; subsequent lifecycle notices are ordinary
            # observations, so the shared session sees a single task opening.
            if boundary is HarnessBoundary.TASK_START:
                if task_start_seen:
                    boundary = HarnessBoundary.OBSERVATION
                else:
                    task_start_seen = True
            evidence_refs = _event_evidence_refs(raw_event)
            event_action_id = hashlib.sha256(
                ("athena-action\0" + event_id).encode("utf-8")).hexdigest()
            validator_id = None
            validation_passed = None
            criterion_ids: tuple[str, ...] = ()
            if boundary is HarnessBoundary.VALIDATION_COMPLETED:
                payload = _field(raw_event, "payload") or {}
                if not isinstance(payload, Mapping):
                    raise TypeError("Athena validation payload must be a mapping")
                raw_validator_id = payload.get("validator_id")
                validator_id = (raw_validator_id if isinstance(raw_validator_id, str)
                                and raw_validator_id else
                                "athena-validation-" + hashlib.sha256(
                                    event_id.encode("utf-8")).hexdigest()[:32])
                raw_status = payload.get("status")
                validation_passed = (isinstance(raw_status, str)
                                      and raw_status in {
                                          "passed", "verified", "succeeded"})
                raw_criteria = payload.get("criterion_ids")
                if raw_criteria is None and isinstance(payload.get("criterion"), str):
                    raw_criteria = (payload["criterion"],)
                if raw_criteria is not None:
                    if (not isinstance(raw_criteria, Sequence)
                            or isinstance(raw_criteria, (str, bytes, bytearray))):
                        raise ValueError("Athena validation criteria must be a sequence")
                    criterion_ids = tuple(raw_criteria)
            control_data = None
            if boundary is HarnessBoundary.ACTION_PROPOSED:
                # The public event exposes a capability request boundary but
                # not a trusted criterion/effect mapping. Keep the projection
                # deliberately unknown so capability negotiation cannot treat
                # it as an authorized action candidate.
                control_data = ActionProposal(
                    action_id=event_action_id,
                    action_class="unknown",
                    tool_name="athena_capability",
                    operation="invoke",
                    purpose="Unclassified Athena capability request",
                    affected_criterion_ids=(),
                    mutating=True,
                    reversible=False,
                    external=True,
                    relation_certainty="unknown",
                )
            elif boundary is HarnessBoundary.ACTION_COMPLETED:
                control_data = RuntimeObservationProjection(
                    action_id=event_action_id,
                    action_index=sequence,
                    criterion_status={},
                    action_completed=True,
                    evidence_refs=evidence_refs,
                )
            yield HarnessBoundaryEvent(
                boundary=boundary,
                event_id=event_id,
                sequence=sequence,
                action_id=event_action_id if control_data is not None else None,
                validator_id=validator_id,
                validation_passed=validation_passed,
                criterion_ids=criterion_ids,
                evidence_refs=evidence_refs,
                control_data=control_data,
            )

    async def observe_task(self, task_id: str, session: Any, *,
                           after_sequence: int = 0,
                           max_events: int = 4096) -> HarnessDispatchSummary:
        """Observe a durable Athena stream through the shared lifecycle API.

        The public ``TaskSpec`` contributes only objective and acceptance
        criterion descriptions/required flags. Arbitrary metadata, task prompt
        copies, model events, and raw tool arguments are not projected. The
        injected session need only expose ``dispatch(event)``; capability
        gating and lifecycle checks remain centralized in the runtime.
        """
        contract = await self.get_task_contract(task_id)
        events = self._events_with_task_contract(
            self.stream_events(task_id, after_sequence=after_sequence), contract)
        return await dispatch_harness_events(
            session, events, max_events=max_events)

    async def get_task_contract(self, task_id: str) -> TaskContract:
        """Project Athena's public TaskSpec into the neutral task contract."""
        _validate_task_id(task_id)
        raw_task = await self.task_port.get_task(task_id)
        if raw_task is None:
            raise ValueError("Athena returned no TaskSpec for the requested task")
        if _field(raw_task, "id") != task_id:
            raise ValueError("Athena TaskSpec id differs from the requested task")
        objective = _field(raw_task, "objective")
        raw_criteria = _field(raw_task, "acceptance_criteria")
        if raw_criteria is None:
            raw_criteria = ()
        if (not isinstance(raw_criteria, Sequence)
                or isinstance(raw_criteria, (str, bytes, bytearray))
                or len(raw_criteria) > 64):
            raise ValueError(
                "Athena TaskSpec acceptance criteria exceed the runtime contract bound")

        claims: dict[str, str] = {}
        required: list[str] = []
        for raw_criterion in raw_criteria:
            criterion_id = _field(raw_criterion, "id")
            description = _field(raw_criterion, "description")
            if not isinstance(criterion_id, str):
                raise ValueError("Athena criterion id must be text")
            criterion_required = _field(raw_criterion, "required")
            if not _has_field(raw_criterion, "required"):
                criterion_required = True
            if type(criterion_required) is not bool:
                raise ValueError("Athena criterion required flag must be boolean")
            if criterion_id in claims:
                raise ValueError("Athena TaskSpec criterion IDs must be unique")
            claims[criterion_id] = description
            if criterion_required:
                required.append(criterion_id)

        # Athena's current public TaskSpec has no dedicated non-goals field.
        # Read only an explicitly named structural field if a future public
        # TaskSpec adds one; never inspect its open-ended metadata mapping.
        raw_non_goals = _field(raw_task, "non_goals")
        if raw_non_goals is None:
            raw_non_goals = ()
        if (not isinstance(raw_non_goals, Sequence)
                or isinstance(raw_non_goals, (str, bytes, bytearray))
                or len(raw_non_goals) > 64):
            raise ValueError("Athena TaskSpec non-goals must be a bounded sequence")

        return TaskContract(
            task_id=task_id,
            objective=objective,
            criterion_claims=claims,
            non_goals=tuple(raw_non_goals),
            required_criterion_ids=tuple(required),
        )

    @staticmethod
    async def _events_with_task_contract(events: Any, contract: TaskContract
                                         ) -> AsyncIterable[HarnessBoundaryEvent]:
        """Attach task intent to the first canonical start and preserve stream order."""
        injected = False
        async for event in events:
            if not injected and event.boundary is HarnessBoundary.TASK_START:
                event = HarnessBoundaryEvent(
                    boundary=event.boundary,
                    event_id=event.event_id,
                    sequence=event.sequence,
                    action_id=event.action_id,
                    validator_id=event.validator_id,
                    validation_passed=event.validation_passed,
                    criterion_ids=event.criterion_ids,
                    evidence_refs=event.evidence_refs,
                    control_data=contract,
                )
                injected = True
            yield event
        if not injected:
            raise ValueError("Athena event stream has no canonical task-start boundary")

    async def get_result(self, task_id: str) -> AthenaTaskResultProjection | None:
        """Project status and evidence references, dropping prose and payload."""
        _validate_task_id(task_id)
        raw_result = await self.task_port.get_result(task_id)
        if raw_result is None:
            return None
        result_task_id = _field(raw_result, "task_id")
        if result_task_id != task_id:
            raise ValueError("Athena result task_id differs from the requested task")
        status = _field(raw_result, "status")
        if not isinstance(status, str) or not _STATUS_RE.fullmatch(status):
            raise ValueError("Athena result status must be bounded status text")
        refs = _result_evidence_refs(raw_result)
        return AthenaTaskResultProjection(task_id, status, refs)


def _validate_task_id(task_id: str) -> None:
    if not isinstance(task_id, str) or not _TASK_ID_RE.fullmatch(task_id):
        raise ValueError("Athena task_id must be a bounded identifier")


def _field(value: Any, name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _has_field(value: Any, name: str) -> bool:
    if isinstance(value, Mapping):
        return name in value
    return hasattr(value, name)


def _event_evidence_refs(event: Any) -> tuple[str, ...]:
    payload = _field(event, "payload")
    if payload is None:
        return ()
    if not isinstance(payload, Mapping):
        raise TypeError("Athena event payload must be a mapping")
    refs = payload.get("evidence_refs", ())
    return _normalize_refs(refs, "Athena event")


def _normalize_refs(value: Any, source: str) -> tuple[str, ...]:
    if (not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray))
            or len(value) > _MAX_EVIDENCE_REFS):
        raise ValueError(f"{source} evidence references must be a bounded sequence")
    refs = tuple(value)
    if (any(not isinstance(ref, str) or not ref.strip()
            or len(ref) > _MAX_REFERENCE_LENGTH for ref in refs)
            or len(refs) != len(set(refs))):
        raise ValueError(f"{source} evidence references are malformed")
    return refs


def _result_evidence_refs(result: Any) -> tuple[str, ...]:
    refs: list[str] = []
    for collection_name, field_name, prefix in (
            ("evidence", "ref", "context"), ("artifacts", "uri", "artifact")):
        collection = _field(result, collection_name)
        if collection is None:
            collection = ()
        if (not isinstance(collection, Sequence)
                or isinstance(collection, (str, bytes, bytearray))):
            raise TypeError(f"Athena result {collection_name} must be a sequence")
        if len(refs) + len(collection) > _MAX_EVIDENCE_REFS:
            raise ValueError("Athena result evidence reference set exceeds its bound")
        for item in collection:
            value = _field(item, field_name)
            if (not isinstance(value, str) or not value.strip()
                    or len(value) > _MAX_REFERENCE_LENGTH - len(prefix) - 1):
                raise ValueError("Athena result evidence reference is malformed")
            refs.append(f"{prefix}:{value}")
    if len(refs) != len(set(refs)):
        raise ValueError("Athena result evidence references must be unique")
    return tuple(refs)


__all__ = ["ATHENA_TASK_PORT_CONTRACT_VERSION", "AthenaRuntimeAdapter",
           "AthenaTaskPort", "AthenaTaskResultProjection"]
