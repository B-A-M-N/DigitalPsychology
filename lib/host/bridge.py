"""Automatic host-side invocation of the private DigitalPsychology MCP plane."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace
import uuid
from typing import Any, Mapping, Sequence

from ..behavior_state import (BehaviorState, CSWBehaviorSnapshot,
                              FrameworkSignal, GoalSetSnapshot)
from ..control_protocol import (CONTROL_BOUNDARY_KINDS, PROTOCOL_VERSION, ControlAbortRequest,
                                ControlCommitRequest, ControlSessionDescriptor,
                                ControlTickRequest)
from ..outcomes import HostOutcomeFact
from ..runtime.contracts import ActionCandidate, RuntimeIdentity, RuntimeObservation
from ..runtime.projection import (AuditProjection, build_behavior_projection,
                                  cached_replace_model_projection)
from ..runtime.adapters.standalone import NullCSWWorkflowPort
from .actuator import ActuationFailure, ControlActuator
from .mcp_client import MCPControlClient, MCPControlUnavailable
from .system_one_audit import summarize_system_one_observation
from .lifecycle_hooks import HostLifecycleHooks
from .transaction_coordinator import HostTransactionCoordinator
from .reconciler import HostReconciler
from .projection_manager import HostProjectionManager
from .degradation import HostDegradation


class HostControlBridge:
    """Run DP at host boundaries and apply output through local CFW/CSW ports.

    The bridge is constructed and called by the trusted host lifecycle. Its
    private MCP child and transaction methods are not an agent tool surface.
    """

    def __init__(self, *, identity: RuntimeIdentity, objective: str,
                 objective_criteria: Sequence[str], initial_goal_set: GoalSetSnapshot,
                 cfw: Any = None, csw: Any = None,
                 client: Any = None,
                 csw_authority_identity: Mapping[str, str] | None = None,
                 outcome_evidence_connected: bool = False,
                 host_policy_context: Mapping[str, Any] | None = None,
                 host_has_stable_objective_context: bool = False,
                 fault_handler: Any = None) -> None:
        self.identity = identity
        self.objective = objective
        self.objective_criteria = tuple(objective_criteria)
        self.goal_set = initial_goal_set
        self.cfw = cfw
        self.csw = csw
        self.client = client or MCPControlClient()
        self.actuator = ControlActuator(cfw=cfw, csw=csw)
        self.outcome_evidence_connected = bool(outcome_evidence_connected)
        self.host_policy_context = dict(host_policy_context or {})
        if len(self.host_policy_context) > 32:
            raise ValueError("host policy context exceeds its field bound")
        if not isinstance(host_has_stable_objective_context, bool):
            raise ValueError("stable objective context flag must be boolean")
        self.host_has_stable_objective_context = host_has_stable_objective_context
        self.fault_handler = fault_handler
        self.lifecycle_hooks = HostLifecycleHooks(self)
        self.transaction_coordinator = HostTransactionCoordinator(self)
        self.reconciler = HostReconciler(self)
        self.projection_manager = HostProjectionManager(self)
        self.degradation = HostDegradation(self)
        self._cfw_components = self._available_frameworks()
        self._csw_identity = dict(csw_authority_identity or self._authority_identity())
        self._state = BehaviorState()
        self._action_index = -1
        self._boundary_sequence = -1
        self._opened = False
        self._remote_open = False
        self._closed = False
        self._fault: str | None = None
        self._needs_reconciliation = False
        self._offline_controls_expired = False
        self._latest_observation: RuntimeObservation | None = None
        self._latest_system_one_observation: Mapping[str, Any] | None = None
        self._audit_projection = AuditProjection(metadata={})
        self._last_model_projection_digest: str | None = None
        self._lock = asyncio.Lock()

    @property
    def fault(self) -> str | None:
        return self._fault

    @property
    def behavior_state(self) -> BehaviorState:
        return self._state

    @property
    def audit_projection(self) -> Mapping[str, Any]:
        return self._audit_projection.metadata

    @property
    def system_one_observation(self) -> Mapping[str, Any] | None:
        """Return the last bounded semantic-provider receipt to the trusted host."""
        return (deepcopy(dict(self._latest_system_one_observation))
                if self._latest_system_one_observation is not None else None)

    async def open(self) -> Mapping[str, Any]:
        return await self.lifecycle_hooks.open()

    async def after_action(self, observation: RuntimeObservation, *,
                           goal_set: GoalSetSnapshot | None = None,
                           outcome_facts: Sequence[HostOutcomeFact] = ()
                           ) -> Mapping[str, Any]:
        """Run after a completed host-observed action or evidence boundary."""
        return await self.lifecycle_hooks.after_action(observation, goal_set=goal_set, outcome_facts=outcome_facts)

    async def task_started(self) -> Mapping[str, Any]:
        return await self.lifecycle_hooks.task_started()

    async def before_action_candidate(self, candidate: ActionCandidate | Mapping[str, Any], *,
                                      cfw_action_request: Mapping[str, Any] | None = None,
                                      goal_set: GoalSetSnapshot | None = None
                                      ) -> Mapping[str, Any]:
        return await self.lifecycle_hooks.before_action_candidate(candidate, cfw_action_request=cfw_action_request, goal_set=goal_set)

    async def _record_candidate_rejection(
            self, candidate: ActionCandidate, decision: str, *, stage: str,
            goal_set: GoalSetSnapshot | None) -> Mapping[str, Any]:
        return await self.lifecycle_hooks._record_candidate_rejection(candidate, decision, stage=stage, goal_set=goal_set)

    async def observe_boundary(self, boundary_kind: str, *,
                               observation: RuntimeObservation | None = None,
                               goal_set: GoalSetSnapshot | None = None,
                               outcome_facts: Sequence[HostOutcomeFact] = ()
                               ) -> Mapping[str, Any]:
        """Submit validator, workflow, operator, or pre-completion evidence."""
        return await self.lifecycle_hooks.observe_boundary(boundary_kind, observation=observation, goal_set=goal_set, outcome_facts=outcome_facts)

    async def _control_boundary(self, boundary_kind: str,
                                observation: RuntimeObservation | None = None, *,
                                goal_set: GoalSetSnapshot | None = None,
                                outcome_facts: Sequence[HostOutcomeFact] = ()
                                ) -> Mapping[str, Any]:
        return await self.transaction_coordinator._control_boundary(boundary_kind, observation, goal_set=goal_set, outcome_facts=outcome_facts)

    def _boundary_observation(self, boundary_kind: str, *,
                              goal_set: GoalSetSnapshot | None = None,
                              candidate_action: ActionCandidate | None = None,
                              candidate_preflight_result: str | None = None
                              ) -> RuntimeObservation:
        return self.transaction_coordinator._boundary_observation(boundary_kind, goal_set=goal_set, candidate_action=candidate_action, candidate_preflight_result=candidate_preflight_result)

    async def _apply_tick_locked(self, observation: RuntimeObservation,
                                 goals: GoalSetSnapshot, *,
                                 boundary_kind: str,
                                 outcome_facts: Sequence[HostOutcomeFact] = ()
                                 ) -> Mapping[str, Any]:
        return await self.transaction_coordinator._apply_tick_locked(observation, goals, boundary_kind=boundary_kind, outcome_facts=outcome_facts)

    async def reconcile(self) -> Mapping[str, Any]:
        async with self._lock:
            self._require_open()
            return await self._reconcile_locked()

    async def before_model_call(self, context: str, *, tool_capable: bool = False,
                                action_hint: Mapping[str, Any] | None = None) -> str:
        return await self.lifecycle_hooks.before_model_call(context, tool_capable=tool_capable, action_hint=action_hint)

    async def before_tool_planning(self, context: str, *,
                                   action_hint: Mapping[str, Any] | None = None
                                   ) -> str:
        return await self.lifecycle_hooks.before_tool_planning(context, action_hint=action_hint)

    async def close(self) -> None:
        return await self.lifecycle_hooks.close()

    async def _connect_or_degrade_locked(self, *,
                                         host_action_index: int | None = None) -> bool:
        return await self.lifecycle_hooks._connect_or_degrade_locked(host_action_index=host_action_index)

    def _expire_local_leases(self, host_action_index: int, *,
                             expire_cfw: bool = False) -> bool:
        """Advance CSW-owned DP leases even when the private DP process is down."""
        return self.reconciler._expire_local_leases(host_action_index, expire_cfw=expire_cfw)

    async def _reconcile_locked(self) -> Mapping[str, Any]:
        return await self.reconciler._reconcile_locked()

    async def _recover_pending(self, prepared: Mapping[str, Any]) -> bool:
        return await self.reconciler._recover_pending(prepared)

    def _accept_committed(self, result: Mapping[str, Any],
                          goal_set: GoalSetSnapshot) -> None:
        return self.reconciler._accept_committed(result, goal_set)

    def _status(self, result: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return self.degradation._status(result)

    def _degraded(self, message: str, failure: Exception) -> Mapping[str, Any]:
        return self.degradation._degraded(message, failure)

    def _notify_fault(self, message: str) -> None:
        return self.degradation._notify_fault(message)

    def _descriptor(self) -> ControlSessionDescriptor:
        return self.projection_manager._descriptor()

    def _project_model_context(self, context: str, projection: Any) -> str:
        return self.projection_manager._project_model_context(context, projection)

    def _record_system_one_observation(self, prepared: Mapping[str, Any], *,
                                       tick_status: str) -> None:
        """Keep provider evidence in host audit state, outside model context."""
        return self.projection_manager._record_system_one_observation(prepared, tick_status=tick_status)

    def _available_frameworks(self) -> tuple[str, ...]:
        return self.projection_manager._available_frameworks()

    def _authority_identity(self) -> dict[str, str]:
        return self.projection_manager._authority_identity()

    def _csw_snapshot(self) -> tuple[CSWBehaviorSnapshot, tuple[str, ...]]:
        return self.projection_manager._csw_snapshot()

    def _cfw_signals(self, observation: RuntimeObservation) -> tuple[FrameworkSignal, ...]:
        return self.projection_manager._cfw_signals(observation)

    def _current_cfw_revision(self) -> int:
        return self.projection_manager._current_cfw_revision()

    @staticmethod
    def _reported_goal_set(response: Mapping[str, Any]) -> GoalSetSnapshot:
        return HostProjectionManager._reported_goal_set(response)

    @staticmethod
    def _same_cfw_projection(actual: Mapping[str, Any] | None,
                             expected: Mapping[str, Any]) -> bool:
        return HostProjectionManager._same_cfw_projection(actual, expected)

    @staticmethod
    def _directives(raw: Any) -> tuple[dict[str, Any], ...]:
        return HostProjectionManager._directives(raw)

    @staticmethod
    def _transport_failure(error: Exception) -> bool:
        return HostDegradation._transport_failure(error)

    def _require_open(self) -> None:
        return self.lifecycle_hooks._require_open()


__all__ = ["HostControlBridge"]
