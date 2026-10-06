from __future__ import annotations

import asyncio
from dataclasses import replace
import json

import pytest

from lib.behavior_state import (BehaviorIntervention, BehaviorState,
                                BehaviorDirective, CSWBehaviorSnapshot,
                                FrameworkSignal, GoalItemSnapshot,
                                GoalSetSnapshot, InterventionOutcome,
                                InterventionTarget, ModeLease)
from lib.control_protocol import (ControlAbortRequest, ControlCommitRequest,
                                  ControlTickRequest, PROTOCOL_VERSION)
from lib.control.service import ControlPlaneService
from lib.control.transaction_service import _lease_or_evaluation_due
from lib.host import HostControlBridge, InProcessControlClient
from lib.runtime.contracts import RuntimeIdentity, RuntimeObservation
from lib.runtime.store import BehaviorStateStore
from lib.service import DigitalPsychologyService
from lib.system_one_backends import DisabledSystemOneBackend
from lib.system_one_service import SystemOneService


class _CountingClient(InProcessControlClient):
    def __init__(self, control_service):
        super().__init__(control_service)
        self.prepare_count = 0
        self.commit_count = 0

    async def prepare_control_tick(self, request):
        self.prepare_count += 1
        return await super().prepare_control_tick(request)

    async def commit_control_tick(self, receipt):
        self.commit_count += 1
        return await super().commit_control_tick(receipt)


def _runtime(tmp_path):
    identity = RuntimeIdentity(
        namespace_id="tenant-a", application_id="codex",
        application_instance_id="codex-local", agent_instance_id="agent-1",
        session_id="session-noop", task_id="task-noop", attempt_id="attempt-1",
        workflow_id="workflow-1", subject_ref="repo:example", segment_id="segment-0",
        agent_id="codex", behavioral_subject="task-noop",
        goal_contract_hash="a" * 64,
    )
    goals = GoalSetSnapshot(1, (GoalItemSnapshot("criterion-visual", status="READY"),))
    store = BehaviorStateStore(tmp_path / "behavior.sqlite3")
    decision_service = DigitalPsychologyService(
        event_db=tmp_path / "events.sqlite3",
        system_one_service=SystemOneService(
            DisabledSystemOneBackend(), mode="disabled"))
    service = ControlPlaneService(
        decision_service=decision_service, state_store=store)
    client = _CountingClient(service)
    bridge = HostControlBridge(
        identity=identity, objective="Render the requested page",
        objective_criteria=("criterion-visual",), initial_goal_set=goals,
        client=client,
    )
    return bridge, client, service, store, goals


def _commit_prepared(service, prepared, *, scope, csw_snapshot=None,
                     cfw_lifecycle_revision=0):
    receipt = ControlCommitRequest(
        control_session_id=scope, tick_id=prepared["tick_id"],
        decision_id=prepared["decision_id"],
        basis_digest=prepared["basis_digest"],
        applied_directive_ids=tuple(
            item["directive_id"] for item in prepared["directives"]),
        cfw_lifecycle_revision=cfw_lifecycle_revision,
        csw_snapshot=csw_snapshot or CSWBehaviorSnapshot(revision=0),
    )
    return service.commit_control_tick(receipt.to_dict())


def _abort_prepared(service, prepared, *, scope, csw_snapshot):
    receipt = ControlAbortRequest(
        control_session_id=scope, tick_id=prepared["tick_id"],
        decision_id=prepared["decision_id"],
        basis_digest=prepared["basis_digest"], applied_directive_ids=(),
        rollback_completed=True, failure_class="NoOpGuardCoverage",
        cfw_lifecycle_revision=0, csw_snapshot=csw_snapshot,
    )
    return service.abort_control_tick(receipt.to_dict())


async def _reach_unchanged(bridge):
    for _ in range(6):
        result = await bridge.observe_boundary("operator")
        if result.get("status") == "unchanged":
            return result
        assert result.get("status") == "ready", result
    pytest.fail("repeated stable non-action boundary never returned unchanged")


def test_repeated_non_action_boundary_returns_unchanged_without_revision_advance(
        tmp_path):
    async def run():
        bridge, client, _service, _store, _goals = _runtime(tmp_path)
        try:
            assert (await bridge.open())["status"] == "ready"
            result = await _reach_unchanged(bridge)
            assert result["status"] == "unchanged"
            assert result["behavior_revision"] == bridge.behavior_state.revision
            assert result["result"]["status"] == "unchanged"
            assert client.prepare_count > client.commit_count
            assert client.prepare_count - client.commit_count == 1
        finally:
            await bridge.close()

    asyncio.run(run())


@pytest.mark.parametrize("changed_input", ["pressure", "signal", "evidence"])
def test_changed_inputs_bypass_unchanged_result(tmp_path, changed_input):
    async def run():
        bridge, _client, service, store, goals = _runtime(tmp_path)
        try:
            assert (await bridge.open())["status"] == "ready"
            await _reach_unchanged(bridge)
            record = store.load(bridge.identity.scope)
            observation = bridge._boundary_observation("operator")
            signal_values = ()
            cursor = 0
            if changed_input == "pressure":
                observation = replace(observation, context_pressure=0.9)
            elif changed_input == "signal":
                signal_values = (FrameworkSignal(
                    source="owl", code="owl.new_observation",
                    severity="warning", criterion_id="criterion-visual"),)
                cursor = 1
            else:
                observation = replace(observation, evidence_refs=("host-proof-1",))

            request = ControlTickRequest(
                control_session_id=bridge.identity.scope,
                tick_id=f"bypass-{changed_input}",
                expected_behavior_revision=record.state.revision,
                cfw_lifecycle_revision=0, observation=observation,
                goal_set=goals, cfw_signals=signal_values,
                csw_snapshot=bridge._csw_snapshot()[0],
                cfw_signal_cursor=cursor,
                boundary_kind="operator",
                boundary_sequence=bridge._boundary_sequence + 1,
            )
            prepared = service.prepare_control_tick(request.to_dict())
            assert prepared["status"] == "prepared"
            _commit_prepared(service, prepared, scope=bridge.identity.scope,
                             csw_snapshot=request.csw_snapshot)
        finally:
            await bridge.close()

    asyncio.run(run())


def test_post_action_boundary_never_uses_unchanged_shortcut(tmp_path):
    async def run():
        bridge, _client, _service, _store, goals = _runtime(tmp_path)
        try:
            assert (await bridge.open())["status"] == "ready"
            await _reach_unchanged(bridge)
            result = await bridge.after_action(RuntimeObservation(
                action_index=1, action_completed=True, goal_set=goals))
            assert result["status"] == "ready", result.get("fault")
            assert result["result"]["status"] == "committed"
        finally:
            await bridge.close()

    asyncio.run(run())


def test_stale_boundary_sequence_cannot_use_unchanged_shortcut(tmp_path):
    async def run():
        bridge, _client, service, store, goals = _runtime(tmp_path)
        try:
            assert (await bridge.open())["status"] == "ready"
            await _reach_unchanged(bridge)
            record = store.load(bridge.identity.scope)
            stale_sequence = store.latest_boundary_sequence(bridge.identity.scope)
            request = ControlTickRequest(
                control_session_id=bridge.identity.scope,
                tick_id="stale-boundary-sequence",
                expected_behavior_revision=record.state.revision,
                cfw_lifecycle_revision=0,
                observation=bridge._boundary_observation("operator"),
                goal_set=goals, cfw_signals=(),
                csw_snapshot=bridge._csw_snapshot()[0],
                boundary_kind="operator", boundary_sequence=stale_sequence,
            )
            with pytest.raises(RuntimeError, match="control boundary sequence is stale"):
                service.prepare_control_tick(request.to_dict())
        finally:
            await bridge.close()

    asyncio.run(run())


def test_pending_outbox_event_prevents_unchanged_and_is_retried(tmp_path, monkeypatch):
    async def run():
        bridge, client, service, store, _goals = _runtime(tmp_path)
        delivered = []
        event_id = "pending-noop-review-event"
        event = {"event_id": event_id, "scope": bridge.identity.scope,
                 "category": "decision", "event_type": "intervention_outcome"}
        try:
            assert (await bridge.open())["status"] == "ready"
            await _reach_unchanged(bridge)
            record = store.load(bridge.identity.scope)
            with store._connect() as database:
                database.execute(
                    "INSERT INTO runtime_event_outbox "
                    "(scope, event_id, event_json, action_index) VALUES (?, ?, ?, ?)",
                    (bridge.identity.scope, event_id,
                     json.dumps(event, sort_keys=True, separators=(",", ":")),
                     max(0, record.action_index)),
                )

            def dispatch(pending_event):
                delivered.append(dict(pending_event))
                return pending_event["event_id"]

            monkeypatch.setattr(
                service.decision_service, "dispatch_runtime_event", dispatch)
            previous_commits = client.commit_count
            result = await bridge.observe_boundary("operator")
            assert result["status"] == "ready"
            assert client.commit_count == previous_commits + 1
            assert delivered == [event]
            assert store.pending_outbox(bridge.identity.scope) == ()
        finally:
            await bridge.close()

    asyncio.run(run())


def test_partial_abort_blocks_control_ticks_until_host_reconciliation(tmp_path):
    async def run():
        bridge, _client, service, store, goals = _runtime(tmp_path)
        try:
            assert (await bridge.open())["status"] == "ready"
            request = ControlTickRequest(
                control_session_id=bridge.identity.scope,
                tick_id="partial-abort-before-reconcile",
                expected_behavior_revision=bridge.behavior_state.revision,
                cfw_lifecycle_revision=0,
                observation=bridge._boundary_observation("operator"),
                goal_set=goals, cfw_signals=(),
                csw_snapshot=bridge._csw_snapshot()[0],
                boundary_kind="operator",
                boundary_sequence=bridge._boundary_sequence + 1,
            )
            prepared = service.prepare_control_tick(request.to_dict())
            assert prepared["status"] == "prepared"
            abort = ControlAbortRequest(
                control_session_id=bridge.identity.scope,
                tick_id=prepared["tick_id"], decision_id=prepared["decision_id"],
                basis_digest=prepared["basis_digest"], applied_directive_ids=(),
                rollback_completed=False, failure_class="partial_test_apply",
                cfw_lifecycle_revision=0, csw_snapshot=request.csw_snapshot,
            )
            assert service.abort_control_tick(abort.to_dict())[
                "requires_reconciliation"] is True

            retry = replace(
                request, tick_id="partial-abort-retry",
                boundary_sequence=store.latest_boundary_sequence(
                    bridge.identity.scope) + 1)
            with pytest.raises(RuntimeError, match="requires reconciliation"):
                service.prepare_control_tick(retry.to_dict())

            reconciliation = service.reconcile_control_session({
                "protocol_version": PROTOCOL_VERSION,
                "control_session_id": bridge.identity.scope,
                "cfw_lifecycle_revision": 0,
                "csw_snapshot": request.csw_snapshot.to_dict(),
            })
            assert reconciliation["pending_decisions"] == []
            assert not store.control_reconciliation_required(bridge.identity.scope)
            prepared_after_reconcile = service.prepare_control_tick(retry.to_dict())
            assert prepared_after_reconcile["status"] == "prepared"
            _abort_prepared(
                service, prepared_after_reconcile,
                scope=bridge.identity.scope, csw_snapshot=request.csw_snapshot)
        finally:
            await bridge.close()

    asyncio.run(run())


def test_required_laya_query_bypasses_unchanged_shortcut(tmp_path):
    async def run():
        bridge, _client, service, store, goals = _runtime(tmp_path)
        try:
            assert (await bridge.open())["status"] == "ready"
            await _reach_unchanged(bridge)
            record = store.load(bridge.identity.scope)
            request = bridge.transaction_coordinator._boundary_observation("operator")
            tick = ControlTickRequest(
                control_session_id=bridge.identity.scope,
                tick_id="laya-query-bypass",
                expected_behavior_revision=record.state.revision,
                cfw_lifecycle_revision=0, observation=request,
                goal_set=goals, cfw_signals=(),
                csw_snapshot=bridge._csw_snapshot()[0],
                boundary_kind="operator",
                boundary_sequence=bridge._boundary_sequence + 1,
            )
            engine = service.engine

            class _QueryAttemptEngine:
                def prepare(self, descriptor, runtime_record, control_request):
                    preparation = engine.prepare(
                        descriptor, runtime_record, control_request)
                    return replace(
                        preparation, system_one_query_attempted=True,
                        system_one_frame={"query": "attempted"})

            service.engine = _QueryAttemptEngine()
            prepared = service.prepare_control_tick(tick.to_dict())
            assert prepared["status"] == "prepared"
            _commit_prepared(service, prepared, scope=bridge.identity.scope,
                             csw_snapshot=tick.csw_snapshot)
        finally:
            await bridge.close()

    asyncio.run(run())


@pytest.mark.parametrize("guard", [
    "starvation_prevented", "directive", "preferred_statework",
    "intervention_outcome", "state_change",
])
def test_nontrivial_preparation_guards_bypass_unchanged_result(tmp_path, guard):
    async def run():
        bridge, _client, service, store, goals = _runtime(tmp_path)
        try:
            assert (await bridge.open())["status"] == "ready"
            await _reach_unchanged(bridge)
            record = store.load(bridge.identity.scope)
            observation = bridge._boundary_observation("operator")
            snapshot = bridge._csw_snapshot()[0]
            request = ControlTickRequest(
                control_session_id=bridge.identity.scope,
                tick_id=f"guard-{guard}",
                expected_behavior_revision=record.state.revision,
                cfw_lifecycle_revision=0, observation=observation,
                goal_set=goals, cfw_signals=(), csw_snapshot=snapshot,
                boundary_kind="operator",
                boundary_sequence=bridge._boundary_sequence + 1,
            )
            engine = service.engine

            class _GuardEngine:
                def prepare(self, descriptor, runtime_record, control_request):
                    preparation = engine.prepare(
                        descriptor, runtime_record, control_request)
                    decision = preparation.decision
                    if guard == "starvation_prevented":
                        return replace(
                            preparation, starvation_prevented=("criterion-visual",))
                    if guard == "directive":
                        directive = BehaviorDirective(
                            directive_id="guard-directive", operation="set_mode",
                            target="cognitive_mode", value="VERIFY", cause="guard-test",
                            expected_effect="verify the requested work",
                            scope=bridge.identity.scope, expires_after_actions=1,
                            intervention_id="guard-intervention", expires_at_action=1,
                        )
                        decision = replace(decision, directives=(directive,))
                    elif guard == "preferred_statework":
                        decision = replace(decision, preferred_stateworks=("repair",))
                    elif guard == "intervention_outcome":
                        outcome = InterventionOutcome(
                            intervention_id="guard-intervention", cause="guard-test",
                            outcome="inconclusive", observed_revision=record.state.revision,
                        )
                        decision = replace(decision, intervention_outcomes=(outcome,))
                    elif guard == "state_change":
                        changed_state = replace(decision.state, cognitive_mode="VERIFY")
                        decision = replace(decision, state=changed_state)
                    return replace(preparation, decision=decision)

            service.engine = _GuardEngine()
            prepared = service.prepare_control_tick(request.to_dict())
            assert prepared["status"] == "prepared"
            _abort_prepared(service, prepared, scope=bridge.identity.scope,
                            csw_snapshot=snapshot)
        finally:
            await bridge.close()

    asyncio.run(run())


def test_due_leases_or_intervention_evaluations_block_shortcut():
    mode_state = BehaviorState(cognitive_mode="VERIFY", mode_lease=ModeLease(
        mode="VERIFY", entered_action=2, minimum_actions=1, max_actions=3,
        cause="verification_required", exit_condition="verification_result"))
    assert _lease_or_evaluation_due(mode_state, action_index=5)

    lease_state = BehaviorState(
        focus_criteria=("criterion-visual",),
        attention_lease={
            "lease_id": "attention-1", "criterion_ids": ["criterion-visual"],
            "issued_revision": 1, "goal_set_revision": 1, "start_action": 2,
            "max_actions": 3, "reason": "priority_and_starvation",
        },
    )
    assert _lease_or_evaluation_due(lease_state, action_index=5)

    intervention = BehaviorIntervention(
        intervention_id="intervention-1", cause="equivalent_retry",
        expected_effect="Change approach", start_revision=1,
        scope="scope-a", expires_after_actions=2, actions_elapsed=0,
        targets=(InterventionTarget("mode", "DELIVER", "RECOVER"),),
        target_criteria=("criterion-visual",), baseline_action_index=2,
        evaluation_horizon_actions=2,
    )
    intervention_state = BehaviorState(active_interventions=(intervention,))
    assert _lease_or_evaluation_due(intervention_state, action_index=4)
