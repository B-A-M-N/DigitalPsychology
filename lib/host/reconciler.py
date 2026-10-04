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
        self.host._last_lease_expiry_failed = False
        if expire_cfw:
            expire_controls = getattr(self.host.cfw, "expire_local_behavior_leases", None)
            if callable(expire_controls):
                try:
                    cfw_changed = bool(expire_controls(max(0, host_action_index)))
                    changed = cfw_changed or changed
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
            return changed
        changed = (before.revision != after.revision
                   or before.snapshot_id != after.snapshot_id or changed)
        if changed:
            self.host._needs_reconciliation = True
        return changed

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
            actual = self.host.actuator.applied_behavior_projection()
            expected = build_cfw_behavior_projection(
                self.host._state,
                host_action_index=self.host._cfw_projection_action_index).to_dict()
            if self.host._cfw_components and not self.host._same_cfw_projection(
                    actual, expected):
                self.host.actuator.synchronize_behavior_projection(expected)
        if not unresolved:
            self.host._fault = None
            self.host._needs_reconciliation = False
        else:
            self.host._needs_reconciliation = True
        return self.host._status(response)

    async def _recover_pending(self, prepared: Mapping[str, Any]) -> bool:
        basis = dict(prepared.get("basis") or {})
        candidate = dict(prepared.get("candidate_behavior_state") or {})
        directives = self.host._directives(prepared.get("directives"))
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
        snapshot, candidates = self.host._csw_snapshot()
        for retained_id, required_by in snapshot.dependency_retention.items():
            if (retained_id not in set(basis.get("csw_active_stateworks") or ())
                    or not set(required_by).issubset(expected_active)):
                raise RuntimeError(
                    "CSW dependency retention does not match the pending DP decision")
            expected_active.add(retained_id)
        actual_cfw = self.host.actuator.applied_behavior_projection()
        expected_cfw = build_cfw_behavior_projection(
            candidate, host_action_index=int(basis.get(
                "cfw_projection_action_index", basis["host_action_index"]))).to_dict()
        cfw_matches = (not self.host._cfw_components or (
            self.host._same_cfw_projection(actual_cfw, expected_cfw)
            and self.host._current_cfw_revision() == int(candidate["revision"])))
        csw_matches = set(snapshot.active_stateworks) == expected_active
        owns_activation = getattr(self.host.csw, "activation_matches", None)
        for statework_id, directive in activations.items():
            if statework_id in snapshot.active_stateworks and (
                    not callable(owns_activation)
                    or not owns_activation(
                        statework_id, intervention_id=directive["intervention_id"])):
                csw_matches = False
        if cfw_matches and csw_matches:
            receipt = ControlCommitRequest(
                control_session_id=self.host.identity.scope,
                tick_id=str(prepared["tick_id"]),
                decision_id=str(prepared["decision_id"]),
                basis_digest=str(prepared["basis_digest"]),
                applied_directive_ids=tuple(item["directive_id"] for item in directives),
                cfw_lifecycle_revision=self.host._current_cfw_revision(),
                csw_snapshot=snapshot,
            )
            committed = await self.host.client.commit_control_tick(receipt.to_dict())
            self.host._accept_committed(committed, self.host.goal_set)
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
                snapshot = self.host.csw.deactivate_statework(
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
                snapshot = self.host.csw.activate_statework(
                    statework_id,
                    expected_selection_revision=snapshot.revision,
                    candidate_statework_ids=candidates,
                    intervention_id=directive["intervention_id"],
                    expires_at_action=self._directive_expiry(directive),
                )
            except Exception as exc:
                rollback_errors.append(f"CSW activation recovery failed: {exc}")
        committed_state = BehaviorState.from_mapping(self.host._state.to_dict())
        if self.host._cfw_components:
            try:
                committed_projection = build_cfw_behavior_projection(
                    committed_state,
                    host_action_index=self.host._cfw_projection_action_index).to_dict()
                self.host.actuator.restore_behavior_projection(
                    committed_projection,
                    expected_current_revision=self.host._current_cfw_revision(),
                )
            except Exception as exc:
                rollback_errors.append(f"CFW state recovery failed: {exc}")
        snapshot, _ = self.host._csw_snapshot()
        abort = ControlAbortRequest(
            control_session_id=self.host.identity.scope,
            tick_id=str(prepared["tick_id"]),
            decision_id=str(prepared["decision_id"]),
            basis_digest=str(prepared["basis_digest"]),
            applied_directive_ids=(),
            rollback_completed=not rollback_errors,
            failure_class="host_restart_reconciliation",
            cfw_lifecycle_revision=self.host._current_cfw_revision(),
            csw_snapshot=snapshot,
        )
        await self.host.client.abort_control_tick(abort.to_dict())
        if rollback_errors:
            self.host._fault = "pending DP actuation could not be fully rolled back: " + "; ".join(
                rollback_errors)
            self.host._notify_fault(self.host._fault)
        return bool(rollback_errors)

    @staticmethod
    def _directive_expiry(directive: Mapping[str, Any]) -> int:
        expiry = directive.get("expires_at_action")
        if type(expiry) is not int or expiry < 0:
            raise ValueError("CSW recovery directive lacks its absolute lease expiry")
        return expiry

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
