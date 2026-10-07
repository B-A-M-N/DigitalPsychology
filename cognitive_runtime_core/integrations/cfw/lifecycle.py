"""Connect an application-owned task lifecycle to DP's private host bridge.

This module does not construct or expose a harness server. The trusted
application host constructs DigitalPsychology's HostControlBridge with its
CSW authority and private control client, then supplies bounded host-authored
projections for actions and evidence.
"""
from __future__ import annotations

import inspect
import hashlib
import json
import math
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from cognitive_runtime_core.completion import CompletionAuthorityReceipt
from cognitive_runtime_core.harness import (
    ActionProposal,
    CompletionProposal,
    HarnessBoundary,
    HarnessBoundaryEvent,
    ModelBoundaryProjection,
    RuntimeObservationProjection,
    TaskContract,
)


class DigitalPsychologyHarnessLifecycle:
    """Consume neutral lifecycle events through DP's private host bridge.

    By default, opening requires CFW ``combined_enforcing`` mode, pinned CFW
    CSW workflow identities, and a DP CSW port. Set ``require_shared_csw`` to
    false only for an explicitly CFW+DP task without CSW workflows.

    ``component_adapter_factory`` is selected by the trusted composition root
    and passed through the task's generic component-adapter port; this module
    does not import or choose a concrete CFW adapter.

    ``candidate_factory`` converts a bounded neutral action proposal into DP's
    validated ActionCandidate. ``observation_factory`` converts bounded
    runtime projections into DP RuntimeObservation values. Neither callback
    receives raw harness arguments or model output.
    """

    completion_authority_ids = ("digital-psychology",)

    def __init__(self, bridge: Any = None, *,
                 bridge_factory: Callable[..., Any] | None = None,
                 component_adapter_factory: Callable[..., Any] | None = None,
                 require_shared_csw: bool = True,
                 candidate_factory: Callable[..., Any],
                 observation_factory: Callable[..., Any]) -> None:
        if (bridge is None) == (bridge_factory is None):
            raise TypeError("provide exactly one bridge or bridge_factory")
        if not callable(candidate_factory) or not callable(observation_factory):
            raise TypeError("host action and evidence projection factories are required")
        if (component_adapter_factory is not None
                and not callable(component_adapter_factory)):
            raise TypeError("component adapter factory must be callable")
        if type(require_shared_csw) is not bool:
            raise TypeError("require_shared_csw must be a boolean")
        self.bridge = bridge
        self.bridge_factory = bridge_factory
        self.component_adapter_factory = component_adapter_factory
        self.require_shared_csw = require_shared_csw
        self.candidate_factory = candidate_factory
        self.observation_factory = observation_factory
        self.request: Any = None
        self.task_handle: Any = None
        self._opened = False
        self._closed = False
        self._bridge_ready = False
        self._runtime_mode: str | None = None
        self._behavior_adapter: Any = None
        self._workflow_ids: tuple[str, ...] = ()
        self._cfw_authority_verified = False
        self._shared_csw_authority_verified = False
        self._intervention_origins: Counter[str] = Counter()
        self._known_intervention_ids: set[str] = set()
        self._pending_actions: dict[str, tuple[ActionProposal, str]] = {}

    async def bind_task_context(self, **context: Any) -> Mapping[str, Any]:
        """Bind the application-owned task ports before the neutral start event.

        The arguments are local integration context supplied by the CFW host;
        none of them are serialized into the harness-neutral event stream.
        """
        contract = context.pop("task_contract", None)
        if not isinstance(contract, TaskContract):
            raise TypeError("runtime task context requires a shared TaskContract")
        request = context.get("request")
        if getattr(request, "task_id", None) != contract.task_id:
            raise ValueError("runtime task binding differs from the task contract")
        opened = await self.open_task(**context)
        self._task_contract = contract
        self._resume = context.get("resume") is True
        return opened

    async def observe_boundary(self, event: HarnessBoundaryEvent) -> Mapping[str, Any]:
        """Apply one canonical lifecycle event to DP without host event names."""
        if not isinstance(event, HarnessBoundaryEvent):
            raise TypeError("DP lifecycle accepts canonical boundary events only")
        if event.boundary is HarnessBoundary.TASK_START:
            contract = event.control_data
            if not isinstance(contract, TaskContract):
                raise TypeError("task start requires a shared task contract")
            if getattr(self, "_task_contract", None) != contract:
                raise RuntimeError("DP lifecycle task binding differs from task start")
            if getattr(self, "_resume", False):
                return {"status": "ready", "decision": "task_resumed"}
            return await self.task_started()
        if not self._opened or self._closed:
            raise RuntimeError("DP lifecycle task has not been opened")
        if event.boundary is HarnessBoundary.MODEL_BOUNDARY:
            projection = event.control_data
            if not isinstance(projection, ModelBoundaryProjection):
                return {"status": "degraded", "decision": "model_context_unavailable"}
            context = await self.before_model_call(
                context=projection.context, tool_capable=projection.tool_capable)
            return {"status": "ready", "context_projection": context}
        if event.boundary is HarnessBoundary.ACTION_PROPOSED:
            proposal = event.control_data
            if not isinstance(proposal, ActionProposal):
                return {"status": "degraded", "decision": "action_projection_unavailable"}
            action = {
                "tool_binding_id": proposal.operation,
                "tool_name": proposal.tool_name,
                "operation": proposal.operation,
                "parameter_digest": proposal.parameter_digest,
                "affected_criterion_ids": tuple(proposal.affected_criterion_ids),
            }
            decision = await self.before_action(
                action=action, binding=proposal.operation,
                invocation_id=proposal.action_id)
            if isinstance(decision, Mapping):
                action_request_id = decision.get("action_request_id")
                if isinstance(action_request_id, str) and action_request_id:
                    self._pending_actions[proposal.action_id] = (
                        proposal, action_request_id[:256])
                    if len(self._pending_actions) > 1024:
                        self._pending_actions.pop(next(iter(self._pending_actions)))
            return decision
        if event.boundary in {HarnessBoundary.ACTION_COMPLETED,
                              HarnessBoundary.OBSERVATION}:
            projection = event.control_data
            if not isinstance(projection, RuntimeObservationProjection):
                return {"status": "degraded", "decision": "observation_unavailable"}
            pending = self._pending_actions.pop(projection.action_id, None)
            proposal = pending[0] if pending is not None else None
            action_request_id = pending[1] if pending is not None else projection.action_id
            projected = projection.to_dict()
            encoded = json.dumps(projected, sort_keys=True, separators=(",", ":"),
                                 ensure_ascii=True, allow_nan=False).encode("ascii")
            digest = hashlib.sha256(encoded).hexdigest()
            evidence_ref = (projection.evidence_refs[0]
                            if projection.evidence_refs else "")
            observation = self.observation_factory(
                boundary=("post_action" if projection.action_completed else "observation"),
                action=({"tool_binding_id": proposal.operation,
                         "tool_name": proposal.tool_name}
                        if proposal is not None else {}),
                binding=proposal.operation if proposal is not None else "observation",
                invocation_id=projection.action_id,
                action_request_id=action_request_id,
                actual_result=projected, result_digest=digest,
                evidence_ref=evidence_ref, projection=projection,
                request=self.request, task_handle=self.task_handle,
                bridge=self.bridge)
            return self._require_ready(
                "action observation", await self._call_tracked(
                    "after_action", origin="post_action", observation=observation))
        if event.boundary is HarnessBoundary.VALIDATION_COMPLETED:
            refs = tuple(event.evidence_refs)
            evidence_by_criterion = {criterion: refs
                                     for criterion in event.criterion_ids if refs}
            return await self.observe_validator(
                validator_id=event.validator_id or "unknown-validator",
                result={"status": "passed" if event.validation_passed else "failed",
                        "passed": event.validation_passed},
                criterion_evidence_refs=evidence_by_criterion)
        if event.boundary is HarnessBoundary.COMPLETION_PROPOSED:
            proposal = event.control_data
            if not isinstance(proposal, CompletionProposal):
                return {"status": "degraded", "decision": "completion_projection_unavailable"}
            outcomes = [{"validator_id": item.validator_id,
                         "criterion_ids": list(item.criterion_ids),
                         "passed": item.passed,
                         "evidence_ref": item.evidence_ref}
                        for item in proposal.validator_outcomes]
            boundary = await self.before_completion(
                validator_id="completion-boundary",
                result={"status": "passed", "validators": outcomes},
                criterion_evidence_refs=proposal.criterion_evidence_refs)
            decision = boundary.get("decision")
            allowed = decision not in {"block", "deny", "reject"}
            raw_reasons = boundary.get("reason_codes", ())
            reasons = (tuple(dict.fromkeys(
                item.strip()[:128] for item in raw_reasons
                if isinstance(item, str) and item.strip()))[:32]
                       if isinstance(raw_reasons, (tuple, list)) else ())
            if not allowed and not reasons:
                reasons = ("dp_completion_denied",)
            state = getattr(self.bridge, "behavior_state", None)
            revision_value = getattr(state, "revision", None)
            revision = (str(revision_value)
                        if type(revision_value) is int and revision_value >= 0
                        else None)
            receipt = CompletionAuthorityReceipt(
                authority_id="digital-psychology", allowed=allowed,
                revision=revision, reason_codes=reasons[:32])
            return {**boundary, "authority_receipts": [receipt.to_dict()]}
        if event.boundary in {HarnessBoundary.WORKFLOW_CHANGED,
                              HarnessBoundary.OPERATOR_OBSERVATION}:
            boundary = ("workflow" if event.boundary is HarnessBoundary.WORKFLOW_CHANGED
                        else "operator")
            refs = tuple(event.evidence_refs)
            observation = self.observation_factory(
                boundary=boundary, evidence_refs=refs,
                request=self.request, task_handle=self.task_handle,
                bridge=self.bridge)
            return self._require_ready(
                boundary, await self._call_tracked(
                    "observe_boundary", origin="runtime_behavior",
                    boundary_kind=boundary, observation=observation))
        if event.boundary is HarnessBoundary.TASK_END:
            await self.close()
            return {"status": "ready", "decision": "task_closed"}
        if event.boundary is HarnessBoundary.PLANNING_BOUNDARY:
            return {"status": "ready", "decision": "planning_boundary_observed"}
        if event.boundary in {HarnessBoundary.ACTION_AUTHORIZED,
                              HarnessBoundary.ACTION_REJECTED}:
            return {"status": "ready", "decision": "action_outcome_observed"}
        return {"status": "degraded", "decision": "boundary_unsupported"}

    def _intervention_ids(self) -> set[str]:
        state = getattr(self.bridge, "behavior_state", None)
        if state is None:
            return set()
        ids = set()
        for field in ("active_interventions", "intervention_history"):
            for item in getattr(state, field, ()) or ():
                value = getattr(item, "intervention_id", None)
                if isinstance(value, str):
                    ids.add(value)
        return ids

    async def _call_tracked(self, method: str, *, origin: str,
                            **kwargs: Any) -> Any:
        result = await self._call(method, **kwargs)
        after = self._intervention_ids()
        newly_observed = after - self._known_intervention_ids
        self._intervention_origins[origin] += len(newly_observed)
        self._known_intervention_ids = after
        return result

    async def _call(self, method: str, **kwargs: Any) -> Any:
        callback = getattr(self.bridge, method, None)
        if not callable(callback):
            raise RuntimeError(  # noqa: TRY004 -- missing protocol method is configuration.
                f"DigitalPsychology bridge lacks {method}")
        result = callback(**kwargs)
        return await result if inspect.isawaitable(result) else result

    @staticmethod
    def _objective(request: Any) -> str:
        contract = getattr(request, "goal_contract", None)
        if isinstance(contract, Mapping):
            value = contract.get("objective")
        else:
            value = getattr(contract, "objective", "")
        return value if isinstance(value, str) else ""

    @staticmethod
    def _criterion_ids(request: Any) -> tuple[str, ...]:
        contract = getattr(request, "goal_contract", None)
        if isinstance(contract, Mapping):
            criteria = contract.get("acceptance_criteria", ())
        else:
            criteria = getattr(contract, "acceptance_criteria", ())
        result = []
        for item in criteria or ():
            value = (item.get("criterion_id") if isinstance(item, Mapping)
                     else getattr(item, "criterion_id", None))
            if isinstance(value, str) and value:
                result.append(value)
        return tuple(result)

    @staticmethod
    def _require_ready(name: str, result: Any) -> Mapping[str, Any]:
        if not isinstance(result, Mapping):
            raise TypeError(f"DigitalPsychology {name} boundary returned no status")
        if result.get("status") not in {"ready", "reconciled", "ok"}:
            raise RuntimeError(f"DigitalPsychology {name} boundary is not ready")
        if result.get("action_authorized") is False:
            raise RuntimeError(f"DigitalPsychology blocked {name}")
        return result

    async def open_task(self, *, request: Any, runtime: Any, task_handle: Any,
                        host_handle: Any, session_id: str, session_nonce: str,
                        resume: bool, runtime_mode: str) -> Mapping[str, Any]:
        if not callable(self.component_adapter_factory):
            raise RuntimeError(
                "host must select a component adapter for the CFW task")
        create_component_adapter = getattr(
            task_handle, "create_component_adapter", None)
        if not callable(create_component_adapter):
            raise TypeError("task handle has no component adapter port")

        objective = self._objective(request)
        criteria = self._criterion_ids(request)
        if self.require_shared_csw:
            if runtime_mode != "combined_enforcing":
                raise RuntimeError(
                    "CFW+CSW+DP requires combined_enforcing runtime mode")
            if not objective:
                raise RuntimeError("CFW+CSW+DP requires a host-owned task objective")
            if not criteria:
                raise RuntimeError("CFW+CSW+DP requires host-owned acceptance criteria")

        behavior_adapter = create_component_adapter(
            self.component_adapter_factory)
        expected_authority = behavior_adapter.authority_identity()
        if self.require_shared_csw:
            workflow_identities = behavior_adapter.workflow_authority_identities()
            if not workflow_identities:
                raise RuntimeError(
                    "CFW task has no shared CSW workflow authority bindings")
            self._workflow_ids = tuple(sorted(workflow_identities))

        if self.bridge is None:
            created = self.bridge_factory(
                request=request, runtime=runtime, task_handle=task_handle,
                host_handle=host_handle, session_id=session_id,
                session_nonce=session_nonce, resume=resume,
                runtime_mode=runtime_mode)
            self.bridge = (await created if inspect.isawaitable(created) else created)
            if self.bridge is None:
                raise RuntimeError("DigitalPsychology bridge factory returned no bridge")
        identity = getattr(self.bridge, "identity", None)
        if (identity is None or getattr(identity, "session_id", None) != session_id
                or getattr(identity, "task_id", None) != request.task_id
                or getattr(identity, "agent_instance_id", None)
                != getattr(task_handle, "agent_instance_id", None)):
            raise RuntimeError(
                "DigitalPsychology bridge identity does not match the CFW task")

        if objective and getattr(self.bridge, "objective", None) != objective:
            raise RuntimeError(
                "DigitalPsychology objective does not match the CFW task contract")
        dp_criteria = set(getattr(self.bridge, "objective_criteria", ()))
        if set(criteria) != dp_criteria:
            raise RuntimeError(
                "DigitalPsychology objective criteria do not match the CFW task")

        if self.require_shared_csw:
            if getattr(self.bridge, "csw", None) is None:
                raise RuntimeError("DigitalPsychology bridge has no CSW authority port")
        cfw_port = getattr(self.bridge, "cfw", None)
        actual_authority_provider = getattr(cfw_port, "authority_identity", None)
        actual_authority = (actual_authority_provider()
                            if callable(actual_authority_provider) else None)
        if not isinstance(expected_authority, Mapping) or actual_authority != expected_authority:
            raise RuntimeError(
                "DigitalPsychology bridge is connected to a different CFW task")

        self.request = request
        self.task_handle = task_handle
        self._runtime_mode = runtime_mode
        self._behavior_adapter = behavior_adapter
        self._cfw_authority_verified = True
        # The adapter calls close on every open failure. Mark ownership before
        # opening so a partially initialized private control client is released.
        self._opened = True
        opened = await self._call_tracked("open", origin="startup")
        self._require_ready("open", opened)
        if getattr(self.bridge, "_needs_reconciliation", False):
            raise RuntimeError("DigitalPsychology bridge requires reconciliation")
        self._bridge_ready = True
        self._shared_csw_authority_verified = bool(
            self.require_shared_csw and self._workflow_ids
            and getattr(self.bridge, "csw", None) is not None)
        return opened

    def status_snapshot(self) -> Mapping[str, Any]:
        """Return bounded operator evidence for the private triad connection."""
        state = getattr(self.bridge, "behavior_state", None)
        behavior_revision = getattr(state, "revision", None)
        if type(behavior_revision) is not int or behavior_revision < 0:
            behavior_revision = None
        cfw_revision = getattr(self._behavior_adapter, "lifecycle_revision", None)
        if type(cfw_revision) is not int or cfw_revision < 0:
            cfw_revision = None

        intervention_count = None
        intervention_metrics = None
        active = getattr(state, "active_interventions", None)
        history = getattr(state, "intervention_history", None)
        if isinstance(active, (tuple, list)) and isinstance(history, (tuple, list)):
            intervention_count = len(self._intervention_ids())
            active_ids = {
                value for item in active
                if isinstance((value := getattr(item, "intervention_id", None)), str)}
            outcome_by_id = {
                value: getattr(item, "outcome", None) for item in history
                if isinstance((value := getattr(item, "intervention_id", None)), str)}
            outcomes = tuple(outcome_by_id.values())
            created_by_origin = {
                key: int(self._intervention_origins.get(key, 0))
                for key in ("startup", "runtime_behavior", "post_action", "completion")}
            intervention_metrics = {
                "unique_created": sum(created_by_origin.values()),
                "active": len(active_ids),
                "applications": len(active_ids | set(outcome_by_id)),
                "closed": len(outcome_by_id),
                "effective": sum(outcome == "effective" for outcome in outcomes),
                "ineffective": sum(outcome == "ineffective" for outcome in outcomes),
                "unknown": sum(outcome not in {"effective", "ineffective"}
                               for outcome in outcomes),
                "created_by_origin": created_by_origin,
                "semantics": (
                    "unique_created counts distinct IDs first observed by this bridge; "
                    "applications counts distinct IDs retained in active state or outcome "
                    "history; closed/outcome counts cover retained history only"),
            }

        metrics: dict[str, Any] = {}
        metrics_provider = getattr(self.bridge, "production_metrics_snapshot", None)
        if callable(metrics_provider):
            try:
                raw = metrics_provider()
            except Exception:  # noqa: BLE001 -- metrics are best-effort diagnostics.
                raw = None
            if isinstance(raw, Mapping):
                counters = raw.get("counters")
                timers = raw.get("timers")
                if isinstance(counters, Mapping):
                    metrics["counters"] = {
                        str(key): value for key, value in counters.items()
                        if isinstance(key, str) and type(value) is int and value >= 0
                    }
                if isinstance(timers, Mapping):
                    safe_timers: dict[str, dict[str, int | float]] = {}
                    for key, timer in timers.items():
                        if not isinstance(key, str) or not isinstance(timer, Mapping):
                            continue
                        fields = {name: timer[name] for name in
                                  ("count", "total_ms", "max_ms")
                                  if name in timer and type(timer[name]) in (int, float)
                                  and math.isfinite(float(timer[name]))
                                  and timer[name] >= 0}
                        if fields:
                            safe_timers[key] = fields
                    metrics["timers"] = safe_timers

        fault = getattr(self.bridge, "fault", None)
        fault_reason = (" ".join(fault.split())[:512]
                        if isinstance(fault, str) and fault else None)
        return {
            "status": ("ready" if self._bridge_ready and not self._closed
                       else "closed" if self._closed else "not_ready"),
            "private_dp_bridge_open": self._bridge_ready and not self._closed,
            "cfw_authority_verified": self._cfw_authority_verified,
            "runtime_mode": self._runtime_mode,
            "shared_csw_authority_verified": self._shared_csw_authority_verified,
            "shared_csw_workflow_count": len(self._workflow_ids),
            "shared_csw_workflow_ids": list(self._workflow_ids),
            "cfw_behavior_revision": cfw_revision,
            "dp_behavior_revision": behavior_revision,
            "dp_intervention_count": intervention_count,
            "dp_intervention_count_semantics": (
                "unique intervention IDs in current active state plus retained history"),
            "dp_intervention_creations_by_origin": {
                key: int(self._intervention_origins.get(key, 0))
                for key in ("startup", "runtime_behavior", "post_action",
                            "completion")},
            "dp_intervention_creations_semantics": (
                "unique intervention IDs first observed after bridge calls by origin; "
                "startup includes persisted IDs first visible when the bridge opens"),
            "dp_intervention_metrics": intervention_metrics,
            "dp_faulted": isinstance(fault, str) and bool(fault),
            "dp_fault_reason": fault_reason,
            "production_metrics": metrics,
        }

    async def task_started(self) -> Mapping[str, Any]:
        return self._require_ready("task start", await self._call_tracked(
            "task_started", origin="startup"))

    async def before_model_call(self, *, context: str,
                                tool_capable: bool) -> str:
        if not self._opened or self._closed:
            raise RuntimeError("DigitalPsychology bridge is not open")
        result = await self._call_tracked(
            "before_model_call", origin="runtime_behavior", context=context,
            tool_capable=tool_capable)
        if not isinstance(result, str):
            raise TypeError("DigitalPsychology returned invalid model context")
        return result

    async def before_action(self, *, action: Mapping[str, Any], binding: str,
                            invocation_id: str) -> Mapping[str, Any]:
        candidate = self.candidate_factory(
            action=dict(action), binding=binding, invocation_id=invocation_id,
            request=self.request, task_handle=self.task_handle)
        result = await self._call_tracked(
            "before_action_candidate", origin="runtime_behavior", candidate=candidate,
            cfw_action_request=dict(action))
        if not isinstance(result, Mapping):
            raise TypeError("DigitalPsychology returned an invalid action decision")
        return result

    async def after_action(self, *, action: Mapping[str, Any], binding: str,
                           invocation_id: str, action_request_id: str,
                           actual_result: Mapping[str, Any], result_digest: str,
                           evidence_ref: str) -> Mapping[str, Any]:
        observation = self.observation_factory(
            boundary="post_action", action=dict(action), binding=binding,
            invocation_id=invocation_id, action_request_id=action_request_id,
            actual_result=dict(actual_result), result_digest=result_digest,
            evidence_ref=evidence_ref, request=self.request,
            task_handle=self.task_handle, bridge=self.bridge)
        return self._require_ready(
            "post-action", await self._call_tracked(
                "after_action", origin="post_action", observation=observation))

    async def observe_validator(self, *, validator_id: str,
                                result: Mapping[str, Any],
                                criterion_evidence_refs: Mapping[str, Sequence[str]]) -> Mapping[str, Any]:
        return await self._observe("validator", validator_id, result,
                                   criterion_evidence_refs)

    async def before_completion(self, *, validator_id: str,
                                result: Mapping[str, Any],
                                criterion_evidence_refs: Mapping[str, Sequence[str]]) -> Mapping[str, Any]:
        boundary = await self._observe("pre_completion", validator_id, result,
                                       criterion_evidence_refs)
        return {**boundary, "decision": boundary.get("decision", "allow")}

    async def _observe(self, boundary: str, validator_id: str,
                       result: Mapping[str, Any],
                       criterion_evidence_refs: Mapping[str, Sequence[str]]) -> Mapping[str, Any]:
        observation = self.observation_factory(
            boundary=boundary, validator_id=validator_id,
            validator_result=dict(result),
            criterion_evidence_refs={key: tuple(refs)
                                     for key, refs in criterion_evidence_refs.items()},
            request=self.request, task_handle=self.task_handle,
            bridge=self.bridge)
        response = await self._call_tracked(
            "observe_boundary", origin="completion", boundary_kind=boundary,
            observation=observation)
        return self._require_ready(boundary, response)

    async def reconcile(self) -> Mapping[str, Any]:
        result = self._require_ready("reconciliation", await self._call_tracked(
            "reconcile", origin="startup"))
        return result

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.bridge is not None:
            await self._call("close")
        self._opened = False
        self._bridge_ready = False


DigitalPsychologyBridgeHost = DigitalPsychologyHarnessLifecycle

__all__ = ["DigitalPsychologyBridgeHost", "DigitalPsychologyHarnessLifecycle"]
