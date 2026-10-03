"""Prepare, apply, commit, and abort host boundary transactions."""
from __future__ import annotations

from dataclasses import replace
import uuid
from typing import Any, Mapping, Sequence

from ..behavior_state import FrameworkSignal, GoalSetSnapshot
from ..control_protocol import (CONTROL_BOUNDARY_KINDS, ControlAbortRequest,
                                ControlCommitRequest, ControlTickRequest)
from ..outcomes import HostOutcomeFact
from ..runtime.contracts import ActionCandidate, RuntimeObservation
from .actuator import ActuationFailure

class HostTransactionCoordinator:
    """Responsibility-focused host bridge collaborator."""

    def __init__(self, bridge: Any) -> None:
        self.host = bridge

    async def _control_boundary(self, boundary_kind: str,
                                observation: RuntimeObservation | None = None, *,
                                goal_set: GoalSetSnapshot | None = None,
                                outcome_facts: Sequence[HostOutcomeFact] = ()
                                ) -> Mapping[str, Any]:
        if boundary_kind not in CONTROL_BOUNDARY_KINDS:
            raise ValueError("unsupported host control boundary kind")
        async with self.host._lock:
            self.host._require_open()
            if observation is None:
                observation = self.host._boundary_observation(
                    boundary_kind, goal_set=goal_set)
            if not isinstance(observation, RuntimeObservation):
                raise TypeError("host control-boundary observation is malformed")
            is_pre_action = boundary_kind in {"pre_action", "pre_action_rejected"}
            if observation.action_completed != (boundary_kind == "post_action"):
                raise ValueError("only post-action boundaries confirm a completed host action")
            if is_pre_action != (observation.candidate_action is not None):
                raise ValueError("candidate boundary must contain exactly one action candidate")
            if ((boundary_kind == "pre_action_rejected")
                    != (observation.candidate_preflight_result is not None)):
                raise ValueError("rejected candidate boundary must contain the CFW gate result")
            if not self.host._remote_open or self.host._needs_reconciliation:
                if not await self.host._connect_or_degrade_locked(
                        host_action_index=(observation.action_index
                                           if boundary_kind in {"pre_action", "post_action"}
                                           else None)):
                    if boundary_kind in {"pre_action", "post_action"}:
                        self.host._expire_local_leases(
                            observation.action_index, expire_cfw=True)
                    return self.host._status()
            if (boundary_kind in {"pre_action", "post_action"}
                    and self.host._expire_local_leases(observation.action_index)):
                await self.host._reconcile_locked()
            goals = goal_set or observation.goal_set or self.host.goal_set
            if not isinstance(goals, GoalSetSnapshot):
                raise ValueError("host must provide a complete goal-set snapshot")
            if goals.revision < self.host.goal_set.revision:
                raise ValueError("host goal-set revision moved backwards")
            observation = replace(observation, goal_set=goals)
            if boundary_kind in {"pre_action", "post_action"}:
                if observation.action_index <= self.host._action_index:
                    raise ValueError("action boundary index must advance DP action state")
            elif boundary_kind == "pre_action_rejected":
                if observation.action_index != max(0, self.host._action_index):
                    raise ValueError("rejected candidates cannot advance the host action index")
            elif observation.action_index != max(0, self.host._action_index):
                raise ValueError("non-action boundary must refer to the current DP action index")
            result = await self.host._apply_tick_locked(
                observation, goals, boundary_kind=boundary_kind,
                outcome_facts=outcome_facts)
            if result.get("status") == "ready" and boundary_kind == "post_action":
                self.host._latest_observation = observation
            return result

    def _boundary_observation(self, boundary_kind: str, *,
                              goal_set: GoalSetSnapshot | None = None,
                              candidate_action: ActionCandidate | None = None,
                              candidate_preflight_result: str | None = None
                              ) -> RuntimeObservation:
        if boundary_kind == "pre_action":
            action_index = max(1, self.host._action_index + 1)
            prior = self.host._latest_observation
            return RuntimeObservation(
                action_index=action_index, action_completed=False,
                actions_since_goal_progress=(prior.actions_since_goal_progress if prior else 0),
                actions_since_artifact_change=(
                    prior.actions_since_artifact_change if prior else None),
                verification_actions_since_artifact_change=(
                    prior.verification_actions_since_artifact_change if prior else None),
                equivalent_retry_count=(prior.equivalent_retry_count if prior else 0),
                context_pressure=prior.context_pressure if prior else 0.0,
                goal_set=goal_set or self.host.goal_set,
                active_trigger_codes=(prior.active_trigger_codes if prior else ()),
                approach_epoch=(candidate_action.approach_epoch if candidate_action
                                and candidate_action.approach_epoch else
                                prior.approach_epoch if prior else ""),
                candidate_action=candidate_action,
            )
        prior = self.host._latest_observation
        return RuntimeObservation(
            action_index=max(0, self.host._action_index),
            action_completed=False,
            actions_since_goal_progress=(prior.actions_since_goal_progress if prior else 0),
            actions_since_artifact_change=(
                prior.actions_since_artifact_change if prior else None),
            verification_actions_since_artifact_change=(
                prior.verification_actions_since_artifact_change if prior else None),
            equivalent_retry_count=(prior.equivalent_retry_count if prior else 0),
            context_pressure=prior.context_pressure if prior else 0.0,
            goal_set=goal_set or self.host.goal_set,
            active_trigger_codes=(prior.active_trigger_codes if prior else ()),
            approach_epoch=prior.approach_epoch if prior else "",
            candidate_action=candidate_action,
            candidate_preflight_result=candidate_preflight_result,
        )

    async def _apply_tick_locked(self, observation: RuntimeObservation,
                                 goals: GoalSetSnapshot, *,
                                 boundary_kind: str,
                                 outcome_facts: Sequence[HostOutcomeFact] = ()
                                 ) -> Mapping[str, Any]:
        snapshot, _candidates = self.host._csw_snapshot()
        self.host._boundary_sequence += 1
        cfw_signals = list(self.host._cfw_signals(observation))
        if observation.candidate_preflight_result is not None:
            cfw_signals = cfw_signals[:31]
            # A criterion-free diagnostic/support candidate is legal, so the
            # first affected criterion may be absent rather than indexed.
            candidate = observation.candidate_action
            candidate_criteria = (tuple(candidate.affected_criterion_ids)
                                  if candidate is not None else ())
            criterion_id = candidate_criteria[0] if candidate_criteria else None
            cfw_signals.append(FrameworkSignal(
                source="cfw_action_gate",
                code="candidate_rejected." + observation.candidate_preflight_result,
                severity="critical", criterion_id=criterion_id,
            ))
        request = ControlTickRequest(
            control_session_id=self.host.identity.scope,
            tick_id=f"control-tick-{uuid.uuid4().hex}",
            expected_behavior_revision=self.host._state.revision,
            cfw_lifecycle_revision=self.host._current_cfw_revision(),
            observation=observation,
            goal_set=goals,
            cfw_signals=tuple(cfw_signals),
            csw_snapshot=snapshot,
            outcome_facts=tuple(outcome_facts),
            boundary_kind=boundary_kind,
            boundary_sequence=self.host._boundary_sequence,
        )
        try:
            prepared = await self.host.client.prepare_control_tick(request.to_dict())
        except Exception as exc:
            if self.host._transport_failure(exc):
                self.host._needs_reconciliation = True
                if boundary_kind in {"pre_action", "post_action"}:
                    self.host._expire_local_leases(
                        observation.action_index, expire_cfw=True)
                return self.host._degraded("DP control prepare unavailable", exc)
            raise
        if prepared.get("status") == "committed":
            result = prepared.get("committed_result")
            if not isinstance(result, Mapping):
                raise RuntimeError("DP returned a committed tick without its receipt")
            self.host._accept_committed(result, goals)
            self.host._record_system_one_observation(prepared, tick_status="committed")
            self.host._offline_controls_expired = False
            return self.host._status(result)
        if prepared.get("status") == "aborted":
            raise RuntimeError("DP returned an aborted control tick for a new action")
        if prepared.get("status") != "prepared":
            raise RuntimeError("DP returned an unknown control transaction status")

        try:
            application = self.host.actuator.apply(prepared)
        except ActuationFailure as exc:
            current_snapshot, _ = self.host._csw_snapshot()
            abort = ControlAbortRequest(
                control_session_id=self.host.identity.scope,
                tick_id=str(prepared["tick_id"]),
                decision_id=str(prepared["decision_id"]),
                basis_digest=str(prepared["basis_digest"]),
                applied_directive_ids=exc.applied_directive_ids,
                rollback_completed=exc.rollback_completed,
                failure_class=type(exc.__cause__ or exc).__name__,
                cfw_lifecycle_revision=self.host._current_cfw_revision(),
                csw_snapshot=current_snapshot,
            )
            try:
                await self.host.client.abort_control_tick(abort.to_dict())
            except Exception as abort_error:
                if self.host._transport_failure(abort_error):
                    self.host._needs_reconciliation = True
                    return self.host._degraded(
                        "DP abort receipt unavailable; reconciliation required",
                        abort_error,
                    )
                raise
            self.host._fault = (str(exc) if exc.rollback_completed else
                           "control actuation failed; authority reconciliation required")
            self.host._needs_reconciliation = not exc.rollback_completed
            self.host._notify_fault(self.host._fault)
            return self.host._status()

        commit = ControlCommitRequest(
            control_session_id=self.host.identity.scope,
            tick_id=str(prepared["tick_id"]),
            decision_id=str(prepared["decision_id"]),
            basis_digest=str(prepared["basis_digest"]),
            applied_directive_ids=application.applied_directive_ids,
            cfw_lifecycle_revision=application.cfw_lifecycle_revision,
            csw_snapshot=application.csw_snapshot,
        )
        try:
            result = await self.host.client.commit_control_tick(commit.to_dict())
        except Exception as exc:
            if self.host._transport_failure(exc):
                # Reconciliation decides whether an already applied tick committed.
                self.host._needs_reconciliation = True
                return self.host._degraded(
                    "DP commit acknowledgement unavailable; reconciliation required",
                    exc,
                )
            raise
        self.host._accept_committed(result, goals)
        self.host._record_system_one_observation(prepared, tick_status="committed")
        self.host._offline_controls_expired = False
        self.host._fault = None
        self.host._needs_reconciliation = False
        return self.host._status(result)
