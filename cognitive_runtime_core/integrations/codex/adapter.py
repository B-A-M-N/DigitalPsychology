"""Normalize Codex App Server lifecycle points for the shared runtime.

This adapter accepts only bounded host contracts and projections. Raw App
Server requests, tool arguments, and model output stay with the transport host.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from ...harness import (
    ActionProposal,
    CognitiveRuntimeHarnessSession,
    CompletionProposal,
    HARNESS_ADAPTER_PROTOCOL_VERSION,
    HarnessAdapterIdentity,
    HarnessBoundary,
    HarnessBoundaryEvent,
    HarnessCapabilities,
    ModelBoundaryProjection,
    RuntimeObservationProjection,
    TaskContract,
)

_MAX_ACTION_ARGUMENT_BYTES = 32_768
CODEX_ADAPTER_VERSION = "0.2.0"


def _canonical_digest(value: Any, *, max_bytes: int = _MAX_ACTION_ARGUMENT_BYTES) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=True, allow_nan=False).encode("ascii")
    if len(encoded) > max_bytes:
        raise ValueError("Codex lifecycle projection exceeds its size bound")
    return hashlib.sha256(encoded).hexdigest()


class CodexHarnessAdapter:
    """Translate Codex host lifecycle calls into canonical runtime events.

    The connected application owns the runtime session and supplies its pinned
    adapter identity. This class only projects Codex boundary observations.
    """

    def __init__(self, session: CognitiveRuntimeHarnessSession) -> None:
        if not isinstance(session, CognitiveRuntimeHarnessSession):
            raise TypeError("Codex adapter requires a shared runtime harness session")
        self.session = session
        self._sequence = 0
        self._closed = False
        self._validation_events: dict[tuple[str, str], None] = {}

    @staticmethod
    def identity_for_client(client: Any, *, host_version: str
                            ) -> HarnessAdapterIdentity:
        """Pin Codex boundary claims to the inspected client feature catalog."""
        catalog = getattr(client, "codex_feature_catalog", None)
        catalog_pin: dict[str, str] = {}
        resolved_host_version = host_version
        if isinstance(catalog, Mapping) and catalog:
            try:
                catalog_pin["codex_feature_catalog_sha256"] = _canonical_digest(
                    dict(catalog), max_bytes=262_144)
            except (TypeError, ValueError):
                catalog_pin = {}
            catalog_version = catalog.get("codex_cli_version")
            if (resolved_host_version == "unknown"
                    and isinstance(catalog_version, str)
                    and catalog_version.strip()):
                resolved_host_version = catalog_version
        capabilities = HarnessCapabilities(
            model_boundary=True,
            planning_boundary=False,
            pre_action=True,
            post_action=True,
            action_blocking=True,
            context_projection=True,
            validator_boundary=True,
            completion_interception=True,
            multi_agent_identity=False,
        )
        verified = (getattr(client, "host_policy_verified", False) is True
                    and bool(catalog_pin)
                    and isinstance(resolved_host_version, str)
                    and resolved_host_version != "unknown")
        return HarnessAdapterIdentity(
            harness_id="codex-app-server",
            adapter_version=CODEX_ADAPTER_VERSION,
            host_version=resolved_host_version,
            protocol_version=HARNESS_ADAPTER_PROTOCOL_VERSION,
            capabilities=capabilities,
            catalog_pin=catalog_pin,
            verified=verified,
        )

    async def start_task(self, contract: TaskContract) -> Mapping[str, Any]:
        return await self._dispatch(HarnessBoundary.TASK_START,
                                    source_id=contract.task_id, contract=contract)

    async def model_boundary(self, *, transport_event_id: str,
                             context: str = "",
                             tool_capable: bool = True) -> Mapping[str, Any]:
        return await self._dispatch(HarnessBoundary.MODEL_BOUNDARY,
                                    source_id=transport_event_id,
                                    control_data=ModelBoundaryProjection(
                                        context=context, tool_capable=tool_capable))

    async def planning_boundary(self, *, transport_event_id: str) -> Mapping[str, Any]:
        return await self._dispatch(HarnessBoundary.PLANNING_BOUNDARY,
                                    source_id=transport_event_id)

    async def action_proposed(self, *, invocation_id: str, tool_name: str,
                              operation: str, purpose: str,
                              arguments: Mapping[str, Any],
                              affected_criterion_ids: Sequence[str],
                              action_class: str = "unknown",
                              mutating: bool = True,
                              reversible: bool = False,
                              external: bool = False,
                              proposed_reason: str = "",
                              target_kind: str = "workspace",
                              relation_certainty: str = "explicit"
                              ) -> Mapping[str, Any]:
        if not isinstance(arguments, Mapping):
            raise TypeError("Codex action arguments must be a mapping")
        criteria = tuple(affected_criterion_ids)
        if not criteria and action_class.lower().replace("-", "_") not in {
                "diagnostic", "support", "read", "observation", "observational",
                "unknown", "unclassified"}:
            action_class = "unknown"
        if not criteria:
            relation_certainty = "unknown"
        proposal = ActionProposal(
            action_id=self._stable_id("action", invocation_id),
            action_class=action_class,
            tool_name=tool_name,
            operation=operation,
            purpose=purpose,
            affected_criterion_ids=criteria,
            parameter_digest=_canonical_digest(dict(arguments)),
            mutating=mutating,
            reversible=reversible,
            external=external,
            proposed_reason=proposed_reason,
            target_kind=target_kind,
            relation_certainty=relation_certainty,
        )
        return await self._dispatch(HarnessBoundary.ACTION_PROPOSED,
                                    source_id=invocation_id, control_data=proposal)

    async def action_completed(self, *, invocation_id: str, action_index: int,
                               criterion_status: Mapping[str, str],
                               action_completed: bool = True,
                               progress_made: bool = False,
                               regression_observed: bool = False,
                               artifact_changed: bool = False,
                               evidence_refs: Sequence[str] = (),
                               criterion_evidence_refs: Mapping[
                                   str, Sequence[str]] | None = None,
                               context_pressure: float = 0.0,
                               actions_since_goal_progress: int = 0,
                               equivalent_retry_count: int = 0,
                               approach_epoch: str = "") -> Mapping[str, Any]:
        observation = RuntimeObservationProjection(
            action_id=self._stable_id("action", invocation_id),
            action_index=action_index,
            criterion_status=criterion_status,
            action_completed=action_completed,
            progress_made=progress_made,
            regression_observed=regression_observed,
            artifact_changed=artifact_changed,
            evidence_refs=evidence_refs,
            criterion_evidence_refs=criterion_evidence_refs or {},
            context_pressure=context_pressure,
            actions_since_goal_progress=actions_since_goal_progress,
            equivalent_retry_count=equivalent_retry_count,
            approach_epoch=approach_epoch,
        )
        boundary = (HarnessBoundary.ACTION_COMPLETED if action_completed
                    else HarnessBoundary.OBSERVATION)
        return await self._dispatch(boundary, source_id=invocation_id,
                                    control_data=observation)

    async def action_authorized(self, *, invocation_id: str) -> Mapping[str, Any]:
        """Report that the connected application authority approved an action."""
        return await self._dispatch(
            HarnessBoundary.ACTION_AUTHORIZED, source_id=invocation_id,
            action_id=self._stable_id("action", invocation_id))

    async def action_rejected(self, *, invocation_id: str) -> Mapping[str, Any]:
        """Report that an application authority rejected a proposed action."""
        return await self._dispatch(
            HarnessBoundary.ACTION_REJECTED, source_id=invocation_id,
            action_id=self._stable_id("action", invocation_id))

    async def validation_completed(self, *, validator_id: str,
                                   criterion_ids: Sequence[str],
                                   evidence_refs: Sequence[str] = (),
                                   invocation_id: str = "",
                                   passed: bool | None = None) -> Mapping[str, Any]:
        normalized_criteria = tuple(criterion_ids)
        normalized_refs = tuple(evidence_refs)
        if passed is None:
            passed = bool(normalized_refs)
        if type(passed) is not bool:
            raise ValueError("validator outcome must be boolean")
        key = (validator_id, invocation_id)
        if key in self._validation_events:
            return {"status": "ready", "decision": "duplicate_validation_evidence"}
        result = await self._dispatch(
            HarnessBoundary.VALIDATION_COMPLETED, source_id=validator_id,
            validator_id=validator_id, validation_passed=passed,
            criterion_ids=normalized_criteria, evidence_refs=normalized_refs)
        self._validation_events[key] = None
        if len(self._validation_events) > 2048:
            self._validation_events.pop(next(iter(self._validation_events)))
        return result

    async def workflow_changed(self, *, source_id: str,
                               evidence_refs: Sequence[str] = ()) -> Mapping[str, Any]:
        return await self._dispatch(HarnessBoundary.WORKFLOW_CHANGED,
                                    source_id=source_id, evidence_refs=evidence_refs)

    async def operator_observation(self, *, source_id: str,
                                   evidence_refs: Sequence[str] = ()) -> Mapping[str, Any]:
        return await self._dispatch(HarnessBoundary.OPERATOR_OBSERVATION,
                                    source_id=source_id, evidence_refs=evidence_refs)

    async def completion_proposed(self, proposal: CompletionProposal
                                  ) -> Mapping[str, Any]:
        return await self._dispatch(HarnessBoundary.COMPLETION_PROPOSED,
                                    source_id=proposal.proposal_id,
                                    control_data=proposal)

    async def task_end(self, *, source_id: str = "terminal") -> Mapping[str, Any]:
        result = await self._dispatch(HarnessBoundary.TASK_END, source_id=source_id)
        await self.session.close()
        self._closed = True
        return result

    async def reconcile(self) -> Mapping[str, Any]:
        return await self.session.reconcile()

    async def close(self) -> None:
        if self._closed:
            return
        await self.session.close()
        self._closed = True

    async def _dispatch(self, boundary: HarnessBoundary, *, source_id: str = "",
                        contract: TaskContract | None = None,
                        control_data: Any = None,
                        action_id: str | None = None,
                        validator_id: str | None = None,
                        validation_passed: bool | None = None,
                        criterion_ids: Sequence[str] = (),
                        evidence_refs: Sequence[str] = ()) -> Mapping[str, Any]:
        if self._closed:
            raise RuntimeError("Codex harness adapter is closed")
        self._sequence += 1
        event = HarnessBoundaryEvent(
            boundary=boundary,
            event_id=f"harness:{self._sequence}:{self._stable_id('event', source_id)}",
            sequence=self._sequence,
            action_id=action_id,
            validator_id=validator_id,
            validation_passed=validation_passed,
            criterion_ids=criterion_ids,
            evidence_refs=evidence_refs,
            control_data=contract if boundary is HarnessBoundary.TASK_START else control_data,
        )
        return await self.session.dispatch(event)

    @staticmethod
    def _stable_id(kind: str, value: str) -> str:
        if not isinstance(value, str) or not value:
            raise ValueError("Codex lifecycle identity must be non-empty text")
        return hashlib.sha256(f"codex:{kind}\0{value}".encode("utf-8")).hexdigest()[:32]


__all__ = ["CodexHarnessAdapter"]
