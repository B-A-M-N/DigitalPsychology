"""Trusted host lifecycle callbacks around DP control."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Sequence

from ..behavior_state import BehaviorState, GoalSetSnapshot
from ..control_protocol import PROTOCOL_VERSION
from ..outcomes import HostOutcomeFact
from ..runtime.contracts import ActionCandidate, RuntimeObservation
from ..runtime.projection import AuditProjection, build_behavior_projection

class HostLifecycleHooks:
    """Responsibility-focused host bridge collaborator."""

    def __init__(self, bridge: Any) -> None:
        self.host = bridge

    async def open(self) -> Mapping[str, Any]:
        async with self.host._lock:
            if self.host._closed:
                raise RuntimeError("host control bridge is closed")
            if self.host._opened:
                if not self.host._remote_open:
                    await self.host._connect_or_degrade_locked()
                return self.host._status()
            self.host._opened = True
            await self.host._connect_or_degrade_locked()
            return self.host._status()

    async def after_action(self, observation: RuntimeObservation, *,
                           goal_set: GoalSetSnapshot | None = None,
                           outcome_facts: Sequence[HostOutcomeFact] = ()
                           ) -> Mapping[str, Any]:
        """Run after a completed host-observed action or evidence boundary."""
        return await self.host._control_boundary(
            "post_action", observation, goal_set=goal_set,
            outcome_facts=outcome_facts)

    async def task_started(self) -> Mapping[str, Any]:
        return await self.host._control_boundary("task_start")

    async def before_action_candidate(self, candidate: ActionCandidate | Mapping[str, Any], *,
                                      cfw_action_request: Mapping[str, Any] | None = None,
                                      goal_set: GoalSetSnapshot | None = None
                                      ) -> Mapping[str, Any]:
        value = (candidate if isinstance(candidate, ActionCandidate)
                 else ActionCandidate(**dict(candidate))
                 if isinstance(candidate, Mapping) else None)
        if value is None:
            raise TypeError("pre-action boundary requires a validated ActionCandidate")
        if not set(value.affected_criterion_ids).issubset(self.host.objective_criteria):
            raise ValueError("candidate action criteria escape the control objective")
        await self._prepare_candidate_boundary()
        cfw_connected = bool(self.host._cfw_components)
        action_request = dict(cfw_action_request) if cfw_action_request is not None else None
        preflight: Mapping[str, Any] | None = None
        gate_receipts: dict[str, Mapping[str, Any]] = {}
        self.host._pending_cfw_gate_decisions.clear()
        if cfw_connected:
            if action_request is None:
                raise ValueError(
                    "combined CFW+DP action boundaries require the original host action request")
            preflight_action = getattr(self.host.cfw, "preflight_action", None)
            authorize_action = getattr(self.host.cfw, "authorize_action", None)
            if not callable(preflight_action) or not callable(authorize_action):
                raise RuntimeError("CFW component port lacks its host action-gate adapter")
            preflight = preflight_action(action_request)
            if not isinstance(preflight, Mapping):
                raise RuntimeError("CFW preflight returned a malformed host receipt")
            gate_receipts["preflight"] = self._validated_gate_receipt(
                preflight.get("gate_receipt"), "preflight")
            if preflight.get("decision") != "allow":
                return await self.host._record_candidate_rejection(
                    value, str(preflight.get("decision") or "block"),
                    stage="cfw_preflight", goal_set=goal_set,
                    preflight_decision=str(preflight.get("decision") or "block"),
                    candidate_gate_receipts=gate_receipts)

        observation = self.host._boundary_observation(
            "pre_action", goal_set=goal_set, candidate_action=value,
            candidate_gate_receipts=(gate_receipts or None),
            cfw_preflight_decision=(str(preflight["decision"])
                                    if preflight is not None else None))
        control = await self.host._control_boundary(
            "pre_action", observation, goal_set=goal_set)
        if not cfw_connected:
            return {**control, "action_authorized": None}

        authorization = self.host.cfw.authorize_action(action_request)
        if not isinstance(authorization, Mapping):
            raise RuntimeError("CFW final action gate returned a malformed host receipt")
        if authorization.get("action_digest") != preflight.get("action_digest"):
            raise RuntimeError("CFW action changed between preflight and the final gate")
        final_receipt = self._validated_gate_receipt(
            authorization.get("gate_receipt"), "final")
        if final_receipt["action_digest"] != preflight.get("action_digest"):
            raise RuntimeError("CFW final gate receipt refers to another action")
        decision = str(authorization.get("decision") or "block")
        if decision != "allow":
            rejected = await self.host._record_candidate_rejection(
                value, decision, stage="cfw_final_gate", goal_set=goal_set,
                preflight_decision=str(preflight["decision"]),
                candidate_gate_receipts={**gate_receipts, "final": final_receipt})
            return {**rejected, "pre_action_control": dict(control)}
        request_id = authorization.get("action_request_id")
        if not isinstance(request_id, str) or not request_id:
            raise RuntimeError("CFW final action gate omitted its correlation ID")
        self.host._pending_cfw_gate_decisions = {
            "preflight": str(preflight["decision"]), "final": decision,
        }
        return {**control, "action_authorized": True,
                "action_request_id": request_id}

    async def _prepare_candidate_boundary(self) -> int:
        """Expire candidate-scoped controls before any CFW gate evaluates it."""
        async with self.host._lock:
            self.host._require_open()
            if not self.host._remote_open or self.host._needs_reconciliation:
                await self.host._connect_or_degrade_locked()
            action_index = max(1, self.host._action_index + 1)
            changed = self.host._expire_local_leases(
                action_index, expire_cfw=True)
            if changed and self.host._remote_open:
                await self.host._connect_or_degrade_locked()
            return action_index

    async def _record_candidate_rejection(
            self, candidate: ActionCandidate, decision: str, *, stage: str,
            goal_set: GoalSetSnapshot | None,
            preflight_decision: str | None = None,
            candidate_gate_receipts: Mapping[str, Any]) -> Mapping[str, Any]:
        observation = self.host._boundary_observation(
            "pre_action_rejected", goal_set=goal_set,
            candidate_action=candidate, candidate_preflight_result=decision,
            candidate_rejection_stage=stage,
            candidate_gate_receipts=candidate_gate_receipts,
            cfw_preflight_decision=preflight_decision or (
                decision if stage == "cfw_preflight" else "allow"),
            cfw_final_decision=(decision if stage == "cfw_final_gate" else None))
        control = await self.host._control_boundary(
            "pre_action_rejected", observation, goal_set=goal_set)
        return {"status": "rejected", "stage": stage, "decision": decision,
                "action_authorized": False, "control": dict(control)}

    def _validated_gate_receipt(self, value: Any, stage: str) -> Mapping[str, Any]:
        if not isinstance(value, Mapping) or value.get("stage") != stage:
            raise RuntimeError("CFW action gate omitted its stage receipt")
        identity = self.host._cfw_authority_identity()
        receipt = dict(value)
        if set(receipt) != {"authority_id", "adapter_kind", "adapter_protocol_version",
                            "stage", "action_digest", "decision", "reason_codes"}:
            raise RuntimeError("CFW action gate receipt fields are invalid")
        if (receipt.get("authority_id") != identity.get("authority_id")
                or receipt.get("adapter_kind") != identity.get("adapter_kind")
                or receipt.get("adapter_protocol_version") != "1.2.0"):
            raise RuntimeError("CFW action gate receipt authority differs from the session")
        return receipt

    async def observe_boundary(self, boundary_kind: str, *,
                               observation: RuntimeObservation | None = None,
                               goal_set: GoalSetSnapshot | None = None,
                               outcome_facts: Sequence[HostOutcomeFact] = ()
                               ) -> Mapping[str, Any]:
        """Submit validator, workflow, operator, or pre-completion evidence."""
        if boundary_kind not in {"validator", "workflow", "operator", "pre_completion"}:
            raise ValueError("observe_boundary requires a validator, workflow, operator, or pre-completion boundary")
        return await self.host._control_boundary(
            boundary_kind, observation, goal_set=goal_set,
            outcome_facts=outcome_facts)

    async def before_model_call(self, context: str, *, tool_capable: bool = False,
                                action_hint: Mapping[str, Any] | None = None) -> str:
        await self.host._control_boundary("pre_model_call")
        async with self.host._lock:
            self.host._require_open()
            snapshot, _ = self.host._csw_snapshot()
            projection_state, local_controls = (
                self.host.projection_manager._offline_behavior_state())
            projection = build_behavior_projection(
                projection_state, snapshot, objective=self.host.objective,
                goal_set=self.host.goal_set,
                cfw_connected=bool(self.host._cfw_components),
                csw_connected=self.host.csw is not None,
                outcome_evidence_connected=self.host.outcome_evidence_connected,
                host_has_stable_objective_context=self.host.host_has_stable_objective_context,
            )
            audit = dict(projection.audit.metadata)
            if self.host._offline_controls_expired:
                audit["offline_controls_expired"] = True
                audit["committed_behavior_revision"] = self.host._state.revision
                audit["effective_cfw_controls"] = local_controls
            if self.host._latest_system_one_observation is not None:
                audit["system_one"] = deepcopy(
                    dict(self.host._latest_system_one_observation))
            self.host._audit_projection = AuditProjection(metadata=audit)
            enriched = self.host._project_model_context(context, projection.model)
            if self.host.cfw is None:
                return enriched
            return self.host.cfw.before_model_call(
                enriched, tool_capable=tool_capable, action_hint=action_hint,
                active_statework_ids=snapshot.active_stateworks,
            )

    async def before_tool_planning(self, context: str, *,
                                   action_hint: Mapping[str, Any] | None = None
                                   ) -> str:
        await self.host._control_boundary("pre_model_call")
        async with self.host._lock:
            self.host._require_open()
            snapshot, _ = self.host._csw_snapshot()
            projection_state, local_controls = (
                self.host.projection_manager._offline_behavior_state())
            projection = build_behavior_projection(
                projection_state, snapshot, objective=self.host.objective,
                goal_set=self.host.goal_set,
                cfw_connected=bool(self.host._cfw_components),
                csw_connected=self.host.csw is not None,
                outcome_evidence_connected=self.host.outcome_evidence_connected,
                host_has_stable_objective_context=self.host.host_has_stable_objective_context,
            )
            audit = dict(projection.audit.metadata)
            if self.host._offline_controls_expired:
                audit["offline_controls_expired"] = True
                audit["committed_behavior_revision"] = self.host._state.revision
                audit["effective_cfw_controls"] = local_controls
            if self.host._latest_system_one_observation is not None:
                audit["system_one"] = deepcopy(
                    dict(self.host._latest_system_one_observation))
            self.host._audit_projection = AuditProjection(metadata=audit)
            enriched = self.host._project_model_context(context, projection.model)
            if self.host.cfw is None:
                return enriched
            return self.host.cfw.before_tool_planning(
                enriched, action_hint=action_hint,
                active_statework_ids=snapshot.active_stateworks,
            )

    async def close(self) -> None:
        async with self.host._lock:
            if self.host._closed:
                return
            if self.host._remote_open:
                try:
                    await self.host.client.close_control_session({
                        "protocol_version": PROTOCOL_VERSION,
                        "control_session_id": self.host.identity.scope,
                    })
                except Exception as exc:
                    if not self.host._transport_failure(exc):
                        raise
            close = getattr(self.host.client, "close", None)
            if callable(close):
                try:
                    await close()
                except Exception as exc:
                    if not self.host._transport_failure(exc):
                        raise
            self.host._closed = True
            self.host._opened = False
            self.host._remote_open = False

    async def _connect_or_degrade_locked(self, *,
                                         host_action_index: int | None = None) -> bool:
        local_expiry_changed = False
        if host_action_index is not None:
            local_expiry_changed = self.host._expire_local_leases(
                host_action_index, expire_cfw=True)
        try:
            start = getattr(self.host.client, "start", None)
            if callable(start):
                await start()
            if not self.host._remote_open:
                response = await self.host.client.open_control_session(
                    self.host._descriptor().to_dict())
                if response.get("control_session_id") != self.host.identity.scope:
                    raise RuntimeError("DP MCP opened a different host control session")
                self.host._state = BehaviorState.from_mapping(response["behavior_state"])
                self.host._action_index = int(response["host_action_index"])
                self.host._boundary_sequence = int(response.get("boundary_sequence", -1))
                self.host._remote_open = True
                self.host._needs_reconciliation = True
            if self.host._needs_reconciliation or local_expiry_changed:
                await self.host._reconcile_locked()
            return not self.host._needs_reconciliation
        except Exception as exc:
            if not self.host._transport_failure(exc):
                raise
            self.host._opened = True
            self.host._degraded("DP control MCP unavailable; retaining the last committed behavior",
                           exc)
            return False

    def _require_open(self) -> None:
        if not self.host._opened or self.host._closed:
            raise RuntimeError("host control bridge is not open")
