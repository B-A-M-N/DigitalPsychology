"""Automatic host-side invocation of the private DigitalPsychology MCP plane."""
from __future__ import annotations

import asyncio
from dataclasses import replace
import uuid
from typing import Any, Mapping, Sequence

from ..behavior_state import (BehaviorState, CSWBehaviorSnapshot,
                              FrameworkSignal, GoalSetSnapshot)
from ..control_protocol import (PROTOCOL_VERSION, ControlAbortRequest,
                                ControlCommitRequest, ControlSessionDescriptor,
                                ControlTickRequest)
from ..outcomes import HostOutcomeFact
from ..runtime.contracts import RuntimeIdentity, RuntimeObservation
from ..runtime.projection import project_behavior_context
from ..runtime.adapters.standalone import NullCSWWorkflowPort
from .actuator import ActuationFailure, ControlActuator
from .mcp_client import MCPControlClient, MCPControlUnavailable


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
        self.fault_handler = fault_handler
        self._cfw_components = self._available_frameworks()
        self._csw_identity = dict(csw_authority_identity or self._authority_identity())
        self._state = BehaviorState()
        self._action_index = -1
        self._opened = False
        self._remote_open = False
        self._closed = False
        self._fault: str | None = None
        self._needs_reconciliation = False
        self._lock = asyncio.Lock()

    @property
    def fault(self) -> str | None:
        return self._fault

    @property
    def behavior_state(self) -> BehaviorState:
        return self._state

    async def open(self) -> Mapping[str, Any]:
        async with self._lock:
            if self._closed:
                raise RuntimeError("host control bridge is closed")
            if self._opened:
                if not self._remote_open:
                    await self._connect_or_degrade_locked()
                return self._status()
            self._opened = True
            await self._connect_or_degrade_locked()
            return self._status()

    async def after_action(self, observation: RuntimeObservation, *,
                           goal_set: GoalSetSnapshot | None = None,
                           outcome_facts: Sequence[HostOutcomeFact] = ()
                           ) -> Mapping[str, Any]:
        """Run after a completed host-observed action or evidence boundary."""
        async with self._lock:
            self._require_open()
            if not isinstance(observation, RuntimeObservation):
                raise TypeError("host action observation is malformed")
            if not self._remote_open or self._needs_reconciliation:
                if not await self._connect_or_degrade_locked(
                        host_action_index=observation.action_index):
                    self._expire_local_leases(observation.action_index)
                    return self._status()
            if not observation.action_completed:
                raise ValueError("DP control runs after completed action boundaries")
            goals = goal_set or observation.goal_set or self.goal_set
            if not isinstance(goals, GoalSetSnapshot):
                raise ValueError("host must provide a complete goal-set snapshot")
            if goals.revision < self.goal_set.revision:
                raise ValueError("host goal-set revision moved backwards")
            observation = replace(observation, goal_set=goals)
            if observation.action_index <= self._action_index:
                raise ValueError("host action index must advance DP control state")
            snapshot, candidates = self._csw_snapshot()
            request = ControlTickRequest(
                control_session_id=self.identity.scope,
                tick_id=f"control-tick-{uuid.uuid4().hex}",
                expected_behavior_revision=self._state.revision,
                cfw_lifecycle_revision=self._current_cfw_revision(),
                observation=observation,
                goal_set=goals,
                cfw_signals=self._cfw_signals(observation),
                csw_snapshot=snapshot,
                outcome_facts=tuple(outcome_facts),
            )
            try:
                prepared = await self.client.prepare_control_tick(request.to_dict())
            except Exception as exc:
                if self._transport_failure(exc):
                    self._needs_reconciliation = True
                    self._expire_local_leases(observation.action_index)
                    return self._degraded("DP control prepare unavailable", exc)
                raise
            if prepared.get("status") == "committed":
                result = prepared.get("committed_result")
                if not isinstance(result, Mapping):
                    raise RuntimeError("DP returned a committed tick without its receipt")
                self._accept_committed(result, goals)
                return self._status(result)
            if prepared.get("status") == "aborted":
                raise RuntimeError("DP returned an aborted control tick for a new action")
            if prepared.get("status") != "prepared":
                raise RuntimeError("DP returned an unknown control transaction status")

            try:
                application = self.actuator.apply(prepared)
            except ActuationFailure as exc:
                current_snapshot, _ = self._csw_snapshot()
                abort = ControlAbortRequest(
                    control_session_id=self.identity.scope,
                    tick_id=str(prepared["tick_id"]),
                    decision_id=str(prepared["decision_id"]),
                    basis_digest=str(prepared["basis_digest"]),
                    applied_directive_ids=exc.applied_directive_ids,
                    rollback_completed=exc.rollback_completed,
                    failure_class=type(exc.__cause__ or exc).__name__,
                    cfw_lifecycle_revision=self._current_cfw_revision(),
                    csw_snapshot=current_snapshot,
                )
                try:
                    await self.client.abort_control_tick(abort.to_dict())
                except Exception as abort_error:
                    if self._transport_failure(abort_error):
                        self._needs_reconciliation = True
                        return self._degraded(
                            "DP abort receipt unavailable; reconciliation required",
                            abort_error,
                        )
                    raise
                self._fault = (str(exc) if exc.rollback_completed else
                               "control actuation failed; authority reconciliation required")
                self._needs_reconciliation = not exc.rollback_completed
                self._notify_fault(self._fault)
                return self._status()
            except Exception:
                raise

            commit = ControlCommitRequest(
                control_session_id=self.identity.scope,
                tick_id=str(prepared["tick_id"]),
                decision_id=str(prepared["decision_id"]),
                basis_digest=str(prepared["basis_digest"]),
                applied_directive_ids=application.applied_directive_ids,
                cfw_lifecycle_revision=application.cfw_lifecycle_revision,
                csw_snapshot=application.csw_snapshot,
            )
            try:
                result = await self.client.commit_control_tick(commit.to_dict())
            except Exception as exc:
                if self._transport_failure(exc):
                    # The host has already applied the prepared state. Do not
                    # compensate based on a lost response; restart reconciliation
                    # determines whether DP committed it.
                    self._needs_reconciliation = True
                    return self._degraded(
                        "DP commit acknowledgement unavailable; reconciliation required",
                        exc,
                    )
                raise
            self._accept_committed(result, goals)
            self._fault = None
            self._needs_reconciliation = False
            return self._status(result)

    async def reconcile(self) -> Mapping[str, Any]:
        async with self._lock:
            self._require_open()
            return await self._reconcile_locked()

    async def before_model_call(self, context: str, *, tool_capable: bool = False,
                                action_hint: Mapping[str, Any] | None = None) -> str:
        async with self._lock:
            self._require_open()
            if not self._remote_open or self._needs_reconciliation:
                await self._connect_or_degrade_locked()
            snapshot, _ = self._csw_snapshot()
            projection = project_behavior_context(
                self._state, snapshot, objective=self.objective,
                goal_set=self.goal_set,
                cfw_connected=bool(self._cfw_components),
                csw_connected=self.csw is not None,
                outcome_evidence_connected=self.outcome_evidence_connected,
            )
            enriched = f"{context}\n{projection}" if context else projection
            if self.cfw is None:
                return enriched
            return self.cfw.before_model_call(
                enriched, tool_capable=tool_capable, action_hint=action_hint,
                active_statework_ids=snapshot.active_stateworks,
            )

    async def before_tool_planning(self, context: str, *,
                                   action_hint: Mapping[str, Any] | None = None) -> str:
        async with self._lock:
            self._require_open()
            if not self._remote_open or self._needs_reconciliation:
                await self._connect_or_degrade_locked()
            snapshot, _ = self._csw_snapshot()
            projection = project_behavior_context(
                self._state, snapshot, objective=self.objective,
                goal_set=self.goal_set,
                cfw_connected=bool(self._cfw_components),
                csw_connected=self.csw is not None,
                outcome_evidence_connected=self.outcome_evidence_connected,
            )
            enriched = f"{context}\n{projection}" if context else projection
            if self.cfw is None:
                return enriched
            return self.cfw.before_tool_planning(
                enriched, action_hint=action_hint,
                active_statework_ids=snapshot.active_stateworks,
            )

    async def close(self) -> None:
        async with self._lock:
            if self._closed:
                return
            if self._remote_open:
                try:
                    await self.client.close_control_session({
                        "protocol_version": PROTOCOL_VERSION,
                        "control_session_id": self.identity.scope,
                    })
                except Exception as exc:
                    if not self._transport_failure(exc):
                        raise
            close = getattr(self.client, "close", None)
            if callable(close):
                try:
                    await close()
                except Exception as exc:
                    if not self._transport_failure(exc):
                        raise
            self._closed = True
            self._opened = False
            self._remote_open = False

    async def _connect_or_degrade_locked(self, *,
                                         host_action_index: int | None = None) -> bool:
        try:
            start = getattr(self.client, "start", None)
            if callable(start):
                await start()
            if not self._remote_open:
                response = await self.client.open_control_session(
                    self._descriptor().to_dict())
                if response.get("control_session_id") != self.identity.scope:
                    raise RuntimeError("DP MCP opened a different host control session")
                self._state = BehaviorState.from_mapping(response["behavior_state"])
                self._action_index = int(response["host_action_index"])
                self._remote_open = True
                self._needs_reconciliation = True
            if self._needs_reconciliation:
                await self._reconcile_locked()
            if host_action_index is not None and self._remote_open:
                if self._expire_local_leases(host_action_index):
                    await self._reconcile_locked()
            return not self._needs_reconciliation
        except Exception as exc:
            if not self._transport_failure(exc):
                raise
            self._opened = True
            self._degraded("DP control MCP unavailable; retaining the last committed behavior",
                           exc)
            return False

    def _expire_local_leases(self, host_action_index: int) -> bool:
        """Advance CSW-owned DP leases even when the private DP process is down."""
        expire = getattr(self.csw, "expire_leases", None)
        if not callable(expire):
            return False
        try:
            expire(host_action_index=host_action_index)
        except Exception as exc:
            self._fault = f"CSW lease expiry failed at the host action boundary: {exc}"
            self._notify_fault(self._fault)
            self._needs_reconciliation = True
            return False
        self._needs_reconciliation = True
        return True

    async def _reconcile_locked(self) -> Mapping[str, Any]:
        snapshot, candidates = self._csw_snapshot()
        response = await self.client.reconcile_control_session({
            "protocol_version": PROTOCOL_VERSION,
            "control_session_id": self.identity.scope,
            "cfw_lifecycle_revision": self._current_cfw_revision(),
            "csw_snapshot": snapshot.to_dict(),
        })
        pending = response.get("pending_decisions") or ()
        if not isinstance(pending, (tuple, list)):
            raise RuntimeError("DP reconciliation returned malformed pending decisions")
        self._state = BehaviorState.from_mapping(response["behavior_state"])
        self._action_index = int(response["host_action_index"])
        self.goal_set = self._reported_goal_set(response)
        unresolved = False
        for item in pending:
            unresolved = await self._recover_pending(item) or unresolved
            snapshot, _ = self._csw_snapshot()
            response = await self.client.reconcile_control_session({
                "protocol_version": PROTOCOL_VERSION,
                "control_session_id": self.identity.scope,
                "cfw_lifecycle_revision": self._current_cfw_revision(),
                "csw_snapshot": snapshot.to_dict(),
            })
            self._state = BehaviorState.from_mapping(response["behavior_state"])
            self._action_index = int(response["host_action_index"])
            self.goal_set = self._reported_goal_set(response)
        if not response.get("pending_decisions"):
            actual = self.actuator.applied_behavior_state()
            if self._cfw_components and not self._same_cfw_projection(
                    actual, self._state.to_dict()):
                self.actuator.synchronize_behavior_state(self._state.to_dict())
        if not unresolved:
            self._fault = None
            self._needs_reconciliation = False
        else:
            self._needs_reconciliation = True
        return self._status(response)

    async def _recover_pending(self, prepared: Mapping[str, Any]) -> bool:
        basis = dict(prepared.get("basis") or {})
        candidate = dict(prepared.get("candidate_behavior_state") or {})
        directives = self._directives(prepared.get("directives"))
        expected_active = set(basis.get("csw_active_stateworks") or ())
        activations = {
            item["target"]: item for item in directives
            if item["operation"] == "prefer_statework"
        }
        releases = {
            item["target"]: item for item in directives
            if item["operation"] == "release_statework"
        }
        for directive in directives:
            if directive["operation"] == "release_statework":
                expected_active.discard(directive["target"])
            elif directive["operation"] == "prefer_statework":
                expected_active.add(directive["target"])
        snapshot, candidates = self._csw_snapshot()
        actual_cfw = self.actuator.applied_behavior_state()
        cfw_matches = (not self._cfw_components or (
            actual_cfw == candidate
            and self._current_cfw_revision() == int(candidate["revision"])))
        csw_matches = set(snapshot.active_stateworks) == expected_active
        owns_activation = getattr(self.csw, "activation_matches", None)
        for statework_id, directive in activations.items():
            if statework_id in snapshot.active_stateworks and (
                    not callable(owns_activation)
                    or not owns_activation(
                        statework_id, intervention_id=directive["intervention_id"])):
                csw_matches = False
        if cfw_matches and csw_matches:
            receipt = ControlCommitRequest(
                control_session_id=self.identity.scope,
                tick_id=str(prepared["tick_id"]),
                decision_id=str(prepared["decision_id"]),
                basis_digest=str(prepared["basis_digest"]),
                applied_directive_ids=tuple(item["directive_id"] for item in directives),
                cfw_lifecycle_revision=self._current_cfw_revision(),
                csw_snapshot=snapshot,
            )
            committed = await self.client.commit_control_tick(receipt.to_dict())
            self._accept_committed(committed, self.goal_set)
            return False

        rollback_errors = []
        desired_active = set(basis.get("csw_active_stateworks") or ())
        for statework_id in sorted(set(snapshot.active_stateworks) - desired_active):
            directive = activations.get(statework_id)
            if directive is None:
                rollback_errors.append(
                    f"unrequested CSW activation {statework_id} preserved during recovery")
                continue
            if (not callable(owns_activation) or not owns_activation(
                    statework_id, intervention_id=directive["intervention_id"])):
                rollback_errors.append(
                    f"CSW activation {statework_id} has another owner and was preserved")
                continue
            try:
                snapshot = self.csw.deactivate_statework(
                    statework_id,
                    expected_selection_revision=snapshot.revision,
                    candidate_statework_ids=candidates,
                    intervention_id=directive["intervention_id"],
                )
            except Exception as exc:
                rollback_errors.append(f"CSW release recovery failed: {exc}")
        for statework_id in sorted(desired_active - set(snapshot.active_stateworks)):
            directive = releases.get(statework_id)
            if directive is None:
                rollback_errors.append(
                    f"unrequested CSW release {statework_id} was not reversed")
                continue
            try:
                snapshot = self.csw.activate_statework(
                    statework_id,
                    expected_selection_revision=snapshot.revision,
                    candidate_statework_ids=candidates,
                    intervention_id=directive["intervention_id"],
                    expires_at_action=(int(basis["host_action_index"])
                                       + int(directive["expires_after_actions"])),
                )
            except Exception as exc:
                rollback_errors.append(f"CSW activation recovery failed: {exc}")
        committed_state = BehaviorState.from_mapping(self._state.to_dict())
        if self._cfw_components:
            try:
                self.actuator.restore_behavior_state(
                    committed_state.to_dict(),
                    expected_current_revision=self._current_cfw_revision(),
                )
            except Exception as exc:
                rollback_errors.append(f"CFW state recovery failed: {exc}")
        snapshot, _ = self._csw_snapshot()
        abort = ControlAbortRequest(
            control_session_id=self.identity.scope,
            tick_id=str(prepared["tick_id"]),
            decision_id=str(prepared["decision_id"]),
            basis_digest=str(prepared["basis_digest"]),
            applied_directive_ids=(),
            rollback_completed=not rollback_errors,
            failure_class="host_restart_reconciliation",
            cfw_lifecycle_revision=self._current_cfw_revision(),
            csw_snapshot=snapshot,
        )
        await self.client.abort_control_tick(abort.to_dict())
        if rollback_errors:
            self._fault = "pending DP actuation could not be fully rolled back: " + "; ".join(
                rollback_errors)
            self._notify_fault(self._fault)
        return bool(rollback_errors)

    def _accept_committed(self, result: Mapping[str, Any],
                          goal_set: GoalSetSnapshot) -> None:
        if result.get("status") != "committed":
            raise RuntimeError("DP did not commit the host control decision")
        self._state = BehaviorState.from_mapping(result["behavior_state"])
        self._action_index = int(result["host_action_index"])
        self.goal_set = goal_set

    def _status(self, result: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return {
            "status": "degraded" if self._fault else "ready",
            "control_session_id": self.identity.scope,
            "behavior_revision": self._state.revision,
            "host_action_index": self._action_index,
            "behavior_state": self._state.to_dict(),
            "result": dict(result) if result is not None else None,
            "fault": self._fault,
        }

    def _degraded(self, message: str, failure: Exception) -> Mapping[str, Any]:
        self._fault = f"{message}: {failure}"
        self._notify_fault(self._fault)
        return self._status()

    def _notify_fault(self, message: str) -> None:
        if callable(self.fault_handler):
            self.fault_handler(message)

    def _descriptor(self) -> ControlSessionDescriptor:
        return ControlSessionDescriptor(
            identity=self.identity,
            objective=self.objective,
            objective_criteria=self.objective_criteria,
            initial_goal_set=self.goal_set,
            available_frameworks=self._cfw_components,
            csw_authority_identity=self._csw_identity,
            host_capabilities={
                "cfw_connected": bool(self._cfw_components),
                "csw_connected": self.csw is not None,
                "outcome_evidence_connected": self.outcome_evidence_connected,
                "host_initiated": True,
            },
        )

    def _available_frameworks(self) -> tuple[str, ...]:
        if self.cfw is None:
            return ()
        values = self.cfw.available_component_ids()
        return tuple(sorted(str(item).lower() for item in values))

    def _authority_identity(self) -> dict[str, str]:
        if self.csw is None:
            return {}
        provider = getattr(self.csw, "authority_identity", None)
        if callable(provider):
            return dict(provider())
        snapshot, _ = self._csw_snapshot()
        return {
            "adapter_kind": "csw-workflow-port",
            "scope_hash": self.identity.scope,
            "capability_hash": snapshot.snapshot_id,
        }

    def _csw_snapshot(self) -> tuple[CSWBehaviorSnapshot, tuple[str, ...]]:
        if self.csw is None:
            return NullCSWWorkflowPort().behavioral_snapshot(), ()
        snapshot = self.csw.behavioral_snapshot()
        available = getattr(self.csw, "available_statework_ids", None)
        if callable(available):
            candidates = tuple(sorted(set(available()) |
                                      set(snapshot.active_stateworks) |
                                      {item.statework_id
                                       for item in snapshot.eligible_stateworks}))
            snapshot = self.csw.behavioral_snapshot(
                candidate_statework_ids=candidates)
        else:
            candidates = tuple(sorted(set(snapshot.active_stateworks) |
                                      {item.statework_id
                                       for item in snapshot.eligible_stateworks}))
        return snapshot, candidates

    def _cfw_signals(self, observation: RuntimeObservation) -> tuple[FrameworkSignal, ...]:
        if self.cfw is None:
            return ()
        return tuple(self.cfw.collect_behavior_signals(observation))

    def _current_cfw_revision(self) -> int:
        if not self._cfw_components:
            return 0
        revision = self.actuator.lifecycle_revision()
        if revision < -1:
            raise RuntimeError("CFW lifecycle revision is invalid")
        return revision

    @staticmethod
    def _reported_goal_set(response: Mapping[str, Any]) -> GoalSetSnapshot:
        value = response.get("goal_set")
        if not isinstance(value, Mapping):
            raise RuntimeError("DP reconciliation omitted the complete goal set")
        from ..control_protocol import parse_goal_set
        return parse_goal_set(value)

    @staticmethod
    def _same_cfw_projection(actual: Mapping[str, Any] | None,
                             expected: Mapping[str, Any]) -> bool:
        if actual is None:
            return False
        actual_projection = dict(actual)
        expected_projection = dict(expected)
        # CSW owns workflow selection. CFW's revisioned overlay carries DP
        # policy but does not duplicate that independent authority's state.
        actual_projection.pop("active_stateworks", None)
        expected_projection.pop("active_stateworks", None)
        return actual_projection == expected_projection

    @staticmethod
    def _directives(raw: Any) -> tuple[dict[str, Any], ...]:
        if not isinstance(raw, (tuple, list)) or len(raw) > 32:
            raise ValueError("pending control directives are malformed")
        return tuple(dict(item) for item in raw if isinstance(item, Mapping))

    @staticmethod
    def _transport_failure(error: Exception) -> bool:
        return isinstance(error, (MCPControlUnavailable, ConnectionError, OSError,
                                  TimeoutError, asyncio.TimeoutError))

    def _require_open(self) -> None:
        if not self._opened or self._closed:
            raise RuntimeError("host control bridge is not open")


__all__ = ["HostControlBridge"]
