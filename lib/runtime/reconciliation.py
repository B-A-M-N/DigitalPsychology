"""Recover pending host transactions and reconcile local CFW/CSW state."""
from __future__ import annotations

from typing import Any, Mapping

from ..behavior_state import BehaviorState, GoalSetSnapshot
from ..control_protocol import (PROTOCOL_VERSION, ControlAbortRequest,
                                ControlCommitRequest)
from ..runtime.cfw_projection import build_cfw_behavior_projection

class HostReconciler:
    """Responsibility-focused host bridge collaborator."""

    def __init__(self, bridge: Any) -> None:
        self.host = bridge

    def _expire_local_leases(self, host_action_index: int, *,
                             expire_cfw: bool = False) -> bool:
        """Advance CSW-owned DP leases even when the private DP process is down."""
        changed = False
        expired_authorities = 0
        self.host._last_lease_expiry_failed = False
        if expire_cfw:
            expire_controls = getattr(self.host.cfw, "expire_local_behavior_leases", None)
            if callable(expire_controls):
                try:
                    cfw_changed = bool(expire_controls(max(0, host_action_index)))
                    changed = cfw_changed or changed
                    expired_authorities += int(cfw_changed)
                    self.host._offline_controls_expired = (
                        self.host._offline_controls_expired or cfw_changed)
                except Exception as exc:
                    self.host._last_lease_expiry_failed = True
                    self.host._fault = f"CFW local lease expiry failed at the host action boundary: {exc}"
                    self.host._notify_fault(self.host._fault)
                    self.host._needs_reconciliation = True
        expire = getattr(self.host.csw, "expire_leases", None)
        if not callable(expire):
            if changed:
                self.host._needs_reconciliation = True
            self._record_expirations_during_outage(expired_authorities)
            return changed
        try:
            before, _ = self.host._csw_snapshot()
            expire(host_action_index=host_action_index)
            after, _ = self.host._csw_snapshot()
        except Exception as exc:
            self.host._last_lease_expiry_failed = True
            self.host._fault = f"CSW lease expiry failed at the host action boundary: {exc}"
            self.host._notify_fault(self.host._fault)
            self.host._needs_reconciliation = True
            self._record_expirations_during_outage(expired_authorities)
            return changed
        changed = (before.revision != after.revision
                   or before.snapshot_id != after.snapshot_id or changed)
        if before.revision != after.revision or before.snapshot_id != after.snapshot_id:
            expired_authorities += 1
        if changed:
            self.host._needs_reconciliation = True
        self._record_expirations_during_outage(expired_authorities)
        return changed

    def _record_expirations_during_outage(self, count: int) -> None:
        if count <= 0:
            return
        try:
            if self.host.production_metrics.period_active("dp_outage"):
                self.host.production_metrics.increment(
                    "lease_expirations_during_outage", count)
        except Exception:
            pass

    async def _reconcile_locked(self) -> Mapping[str, Any]:
        snapshot, candidates = self.host._csw_snapshot()
        response = await self.host.client.reconcile_control_session({
            "protocol_version": PROTOCOL_VERSION,
            "control_session_id": self.host.identity.scope,
            "cfw_lifecycle_revision": self.host._current_cfw_revision(),
            "csw_snapshot": snapshot.to_dict(),
        })
        pending = response.get("pending_decisions") or ()
        if not isinstance(pending, (tuple, list)):
            raise RuntimeError("DP reconciliation returned malformed pending decisions")
        if not pending:
            latest = response.get("latest_control_tick")
            if isinstance(latest, Mapping):
                tick_id = latest.get("tick_id")
                status = latest.get("status")
                if isinstance(tick_id, str) and status == "committed":
                    self.host.actuator.finalize_selection_checkpoint(tick_id=tick_id)
                elif isinstance(tick_id, str) and status == "aborted":
                    restored = self.host.actuator.restore_selection_checkpoint(
                        tick_id=tick_id,
                        expected_selection_revision=snapshot.revision)
                    if restored is not None:
                        snapshot, _ = self.host._csw_snapshot()
                        response = await self.host.client.reconcile_control_session({
                            "protocol_version": PROTOCOL_VERSION,
                            "control_session_id": self.host.identity.scope,
                            "cfw_lifecycle_revision": self.host._current_cfw_revision(),
                            "csw_snapshot": snapshot.to_dict(),
                        })
                        pending = response.get("pending_decisions") or ()
                        if not isinstance(pending, (tuple, list)):
                            raise RuntimeError(
                                "DP reconciliation returned malformed pending decisions")
        self.host._state = BehaviorState.from_mapping(response["behavior_state"])
        self.host._action_index = int(response["host_action_index"])
        self.host._cfw_projection_action_index = int(response.get(
            "cfw_projection_action_index", max(0, self.host._action_index)))
        self.host._boundary_sequence = int(response.get("boundary_sequence", -1))
        self.host.goal_set = self.host._reported_goal_set(response)
        unresolved = False
        for item in pending:
            unresolved = await self.host._recover_pending(item) or unresolved
            snapshot, _ = self.host._csw_snapshot()
            response = await self.host.client.reconcile_control_session({
                "protocol_version": PROTOCOL_VERSION,
                "control_session_id": self.host.identity.scope,
                "cfw_lifecycle_revision": self.host._current_cfw_revision(),
                "csw_snapshot": snapshot.to_dict(),
            })
            self.host._state = BehaviorState.from_mapping(response["behavior_state"])
            self.host._action_index = int(response["host_action_index"])
            self.host._cfw_projection_action_index = int(response.get(
                "cfw_projection_action_index", max(0, self.host._action_index)))
            self.host._boundary_sequence = int(response.get("boundary_sequence", -1))
            self.host.goal_set = self.host._reported_goal_set(response)
        if not response.get("pending_decisions"):
            committed_signal_cursor = response.get("committed_cfw_signal_cursor", 0)
            if type(committed_signal_cursor) is not int or committed_signal_cursor < 0:
                raise RuntimeError("DP reconciliation returned an invalid CFW signal cursor")
            self.host._acknowledge_cfw_signals(committed_signal_cursor)
            expected = build_cfw_behavior_projection(
                self.host._state,
                host_action_index=self.host._cfw_projection_action_index).to_dict()
            self.host.actuator.reconcile_behavior_projection(expected)
        if not unresolved:
            self.host._fault = None
            self.host._needs_reconciliation = False
            self.host._clear_degradation_periods()
        else:
            self.host._needs_reconciliation = True
        return self.host._status(response)

    async def _recover_pending(self, prepared: Mapping[str, Any]) -> bool:
        directives = self.host._directives(prepared.get("directives"))
        recovery = self.host.actuator.reconcile_prepared(
            prepared,
            committed_behavior_state=self.host._state.to_dict(),
            committed_projection_basis={
                "cfw_projection_action_index": self.host._cfw_projection_action_index,
            },
        )
        if recovery.applied:
            receipt = ControlCommitRequest(
                control_session_id=self.host.identity.scope,
                tick_id=str(prepared["tick_id"]),
                decision_id=str(prepared["decision_id"]),
                basis_digest=str(prepared["basis_digest"]),
                applied_directive_ids=recovery.applied_directive_ids,
                cfw_lifecycle_revision=recovery.cfw_lifecycle_revision,
                csw_snapshot=recovery.csw_snapshot,
            )
            committed = await self.host.client.commit_control_tick(receipt.to_dict())
            self.host._accept_committed(committed, self.host.goal_set)
            if directives:
                self.host.actuator.finalize_selection_checkpoint(
                    tick_id=str(prepared["tick_id"]))
            return False

        abort = ControlAbortRequest(
            control_session_id=self.host.identity.scope,
            tick_id=str(prepared["tick_id"]),
            decision_id=str(prepared["decision_id"]),
            basis_digest=str(prepared["basis_digest"]),
            applied_directive_ids=(),
            rollback_completed=recovery.rollback_completed,
            failure_class="host_restart_reconciliation",
            cfw_lifecycle_revision=recovery.cfw_lifecycle_revision,
            csw_snapshot=recovery.csw_snapshot,
        )
        await self.host.client.abort_control_tick(abort.to_dict())
        if recovery.rollback_errors:
            self.host._fault = (
                "pending DP actuation could not be fully rolled back: "
                + "; ".join(recovery.rollback_errors))
            self.host._notify_fault(self.host._fault)
        return not recovery.rollback_completed

    def _accept_committed(self, result: Mapping[str, Any],
                          goal_set: GoalSetSnapshot) -> None:
        if result.get("status") != "committed":
            raise RuntimeError("DP did not commit the host control decision")
        self.host._state = BehaviorState.from_mapping(result["behavior_state"])
        self.host._action_index = int(result["host_action_index"])
        self.host._cfw_projection_action_index = int(result.get(
            "cfw_projection_action_index", result.get(
                "candidate_action_index", max(0, self.host._action_index))))
        self.host._boundary_sequence = int(result.get(
            "boundary_sequence", self.host._boundary_sequence))
        self.host.goal_set = goal_set
