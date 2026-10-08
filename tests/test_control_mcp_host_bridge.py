from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
import pytest

from lib.behavior_state import (BehaviorState, CSWBehaviorSnapshot, GoalItemSnapshot,
                                GoalSetSnapshot, StateWorkOption)
from lib.control_protocol import (PROTOCOL_VERSION, ControlAbortRequest,
                                  ControlCommitRequest, ControlSessionDescriptor,
                                  ControlTickRequest)
from lib.host import (HostControlBridge, InProcessControlClient, MCPControlClient,
                      MCPControlUnavailable)
from lib.host.mcp_client import ClientSession, StdioServerParameters, stdio_client
from lib.control.service import ControlPlaneService
from lib.runtime.store import BehaviorStateStore
from lib.service import DigitalPsychologyService
from lib.system_one_backends import DisabledSystemOneBackend
from lib.system_one_service import SystemOneService
from lib.telemetry.production_metrics import ProductionMetrics
from lib.runtime import (ActionCandidate, CFWRuntimeAdapter, RuntimeIdentity,
                         RuntimeObservation)
from lib.runtime.harness import (HARNESS_ADAPTER_PROTOCOL_VERSION,
                                 HarnessAdapterIdentity, HarnessCapabilities)
from lib.runtime.cfw_projection import build_cfw_behavior_projection


ROOT = Path(__file__).resolve().parents[1]


class _CFWPort:
    def __init__(self, *, preflight_decision="allow", final_decision="allow") -> None:
        self.signals = [{
            "source": "owl", "code": "owl.approach_failed", "active": True,
            "severity": "warning", "criterion_id": "criterion-visual",
            "evidence_refs": [],
        }]
        self.controls: dict[str, dict] = {}
        self.signal_ack_cursor = 0
        self._state = None
        self.preflight_decision = preflight_decision
        self.final_decision = final_decision
        self.action_gate_calls = []
        self.preflight_fuse_modes = []
        self.local_fuse_mode = "normal"
        self.local_fuse_expiry = None
        self.effective_projection = None
        self._projection = None

    def authority_identity(self):
        contract = self.task_behavior_contract()
        return {"adapter_kind": "cfw-component-runtime", "authority_id": "test-cfw",
                "adapter_protocol_version": "1.2.0",
                "behavior_projection_version": "2.1.0",
                "scope_hash": "a" * 64, "capability_hash": "b" * 64,
                "task_behavior_contract_hash": contract["contract_hash"]}

    def task_behavior_contract(self):
        selected = sorted(self.available_component_ids())
        baselines = {item: ("mandatory" if item == "ward" else "normal")
                     for item in selected}
        body = {
            "protocol_version": "1.0.0",
            "pipeline_manifest_version": "test",
            "pipeline_manifest_hash": "c" * 64,
            "selected_components": selected,
            "baseline_floors": baselines,
            "suppression_modes": {
                item: ([] if item == "ward" else ["off", "passive"])
                for item in selected},
            "transient_operation_modes": {
                item: ([mode for mode in ("reset", "reopen", "recovery")
                        if item in {"owl", "anchor"}]
                       if item != "ward" else [])
                for item in selected},
        }
        digest = hashlib.sha256(json.dumps(
            body, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False).encode()).hexdigest()
        return {**body, "contract_hash": digest}

    @staticmethod
    def _action_digest(action):
        body = json.dumps(dict(action), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(body.encode()).hexdigest()

    def preflight_action(self, action):
        self.action_gate_calls.append("preflight")
        self.preflight_fuse_modes.append(self.local_fuse_mode)
        return {"action_digest": self._action_digest(action),
                "decision": self.preflight_decision,
                "gate_receipt": self._gate_receipt(
                    action, "preflight", self.preflight_decision)}

    def authorize_action(self, action):
        self.action_gate_calls.append("authorize")
        return {"action_digest": self._action_digest(action),
                "action_request_id": "action-request-host-1",
                "decision": self.final_decision,
                "gate_receipt": self._gate_receipt(action, "final", self.final_decision)}

    def _gate_receipt(self, action, stage, decision):
        return {"authority_id": "test-cfw",
                "adapter_kind": "cfw-component-runtime",
                "adapter_protocol_version": "1.2.0", "stage": stage,
                "action_digest": self._action_digest(action), "decision": decision,
                "reason_codes": ["test.action_policy." + decision]}

    def available_component_ids(self):
        return ("owl", "anchor", "fuse", "ward", "sispis")

    def expire_local_behavior_leases(self, action_index):
        if (self.local_fuse_expiry is not None
                and self.local_fuse_expiry < action_index):
            self.local_fuse_mode = "normal"
            self.local_fuse_expiry = None
            return True
        return False

    def collect_behavior_signals(self, _observation):
        return tuple(self.signals)

    def peek_behavior_signals(self, *, after_cursor=None, maximum=32):
        after = self.signal_ack_cursor if after_cursor is None else after_cursor
        selected = self.signals[after:after + maximum] if after < len(self.signals) else []
        return {"after_cursor": after, "next_cursor": after + len(selected),
                "signals": [dict(item) for item in selected]}

    def acknowledge_behavior_signals(self, *, through_cursor):
        self.signal_ack_cursor = max(self.signal_ack_cursor, through_cursor)

    @property
    def lifecycle_revision(self):
        return int(self._state["behavior_revision"]) if self._state is not None else -1

    @property
    def applied_behavior_projection(self):
        return dict(self._projection) if self._projection is not None else None

    @property
    def effective_behavior_projection(self):
        value = self.effective_projection or self._projection
        return dict(value) if value is not None else None

    def prepare_behavior_directives(self, directives):
        return (dict(self.controls), dict(self._projection) if self._projection else None,
                tuple(dict(item) for item in directives))

    def apply_behavior_projection(self, prepared, projection):
        _controls, _prior, directives = prepared
        self._projection = dict(projection)
        self._state = dict(projection)
        self.controls = {**self.controls,
                         **{item["directive_id"]: item for item in directives}}
        return tuple(item["directive_id"] for item in directives)

    def synchronize_behavior_projection(self, projection):
        self._projection = dict(projection)
        self._state = dict(projection)

    def rollback_behavior_directives(self, prepared):
        controls, prior, _directives = prepared
        self.controls = dict(controls)
        self._projection = dict(prior) if prior is not None else None
        self._state = dict(prior) if prior is not None else None

    def restore_behavior_projection(self, projection, *, expected_current_revision):
        if self.lifecycle_revision != expected_current_revision:
            raise RuntimeError("stale CFW lifecycle revision")
        self._projection = dict(projection)
        self._state = dict(projection)

    def before_model_call(self, context, **_kwargs):
        return context

    def before_tool_planning(self, context, **_kwargs):
        return context


class _CSWPort:
    def __init__(self) -> None:
        self.revision = 0
        self.active: tuple[str, ...] = ()
        self.activation_interventions = {}
        self.lease_expiries = {}
        self.expiration_calls = []
        self._selection_checkpoints = {}
        self.options = (StateWorkOption(
            "repair", behavior_tags=("recovery",),
            purpose="Rebuild a failed approach from evidence."),)

    def available_statework_ids(self):
        return ("repair",)

    def authority_identity(self):
        return {"adapter_kind": "csw-workflow-authority", "authority_id": "test-csw",
                "adapter_protocol_version": "1.2.0", "workflow_port_version": "1.6.0",
                "registry_hash": "d" * 64,
                "scope_hash": "b" * 64, "capability_hash": "c" * 64}

    def behavioral_snapshot(self, *, candidate_statework_ids=None):
        candidates = set(candidate_statework_ids or ("repair",))
        eligible = tuple(item for item in self.options
                         if item.statework_id in candidates
                         and item.statework_id not in self.active)
        payload = json.dumps({"revision": self.revision,
                              "active": self.active,
                              "eligible": [item.statework_id for item in eligible]},
                             sort_keys=True, separators=(",", ":"))
        return CSWBehaviorSnapshot(
            revision=self.revision,
            active_stateworks=self.active,
            eligible_stateworks=eligible,
            workflow_states={item: "UNKNOWN" for item in self.active},
            snapshot_id=hashlib.sha256(payload.encode()).hexdigest(),
            registry_hash="d" * 64,
            workflow_revisions={item: 0 for item in self.active},
            eligible_steps={item: ("inspect",) for item in self.active},
        )

    def activate_statework(self, statework_id, *, expected_selection_revision,
                           candidate_statework_ids, intervention_id,
                           expires_at_action):
        assert intervention_id
        assert expires_at_action >= 0
        if expected_selection_revision != self.revision:
            raise RuntimeError("stale CSW selection revision")
        if statework_id not in candidate_statework_ids:
            raise ValueError("unbound StateWork")
        self.active = tuple(sorted(set(self.active) | {statework_id}))
        self.activation_interventions[statework_id] = intervention_id
        self.lease_expiries[statework_id] = expires_at_action
        self.revision += 1
        return self.behavioral_snapshot(candidate_statework_ids=candidate_statework_ids)

    def deactivate_statework(self, statework_id, *, expected_selection_revision,
                             candidate_statework_ids, intervention_id):
        assert intervention_id
        if expected_selection_revision != self.revision:
            raise RuntimeError("stale CSW selection revision")
        self.active = tuple(item for item in self.active if item != statework_id)
        self.activation_interventions.pop(statework_id, None)
        self.lease_expiries.pop(statework_id, None)
        self.revision += 1
        return self.behavioral_snapshot(candidate_statework_ids=candidate_statework_ids)

    def expire_leases(self, *, host_action_index):
        self.expiration_calls.append(host_action_index)
        expired = {statework_id for statework_id, expires_at in self.lease_expiries.items()
                   if expires_at <= host_action_index}
        self.active = tuple(item for item in self.active if item not in expired)
        for statework_id in expired:
            self.activation_interventions.pop(statework_id, None)
            self.lease_expiries.pop(statework_id, None)
        if expired:
            self.revision += 1
        return self.behavioral_snapshot()

    def activation_matches(self, statework_id, *, intervention_id):
        return self.activation_interventions.get(statework_id) == intervention_id

    def checkpoint_selection(self, *, tick_id, expected_selection_revision):
        if expected_selection_revision != self.revision or tick_id in self._selection_checkpoints:
            raise RuntimeError("stale or duplicate CSW selection checkpoint")
        self._selection_checkpoints[tick_id] = (
            self.active, dict(self.activation_interventions), dict(self.lease_expiries))

    def restore_selection_checkpoint(self, *, tick_id, expected_selection_revision):
        checkpoint = self._selection_checkpoints.get(tick_id)
        if checkpoint is None:
            return None
        if expected_selection_revision != self.revision:
            raise RuntimeError("stale CSW checkpoint restore")
        self.active, owners, expiries = checkpoint
        self.activation_interventions = owners
        self.lease_expiries = expiries
        self.revision += 1
        self._selection_checkpoints.pop(tick_id, None)
        return self.behavioral_snapshot()

    def finalize_selection_checkpoint(self, *, tick_id):
        self._selection_checkpoints.pop(tick_id, None)


def _identity(task_id="control-mcp-task"):
    return RuntimeIdentity(
        namespace_id="tenant-a", application_id="codex",
        application_instance_id="codex-local", agent_instance_id="agent-1",
        session_id="session-1", task_id=task_id, attempt_id="attempt-1",
        workflow_id="workflow-1", subject_ref="repo:example", segment_id="segment-0",
        agent_id="codex", behavioral_subject=task_id, goal_contract_hash="a" * 64,
    )


def _goal_set(revision=1):
    return GoalSetSnapshot(revision=revision, items=(GoalItemSnapshot(
        criterion_id="criterion-visual", status="READY",
        claim="Render the requested page at the target viewport."),))


class _CapturingControlClient(InProcessControlClient):
    def __init__(self, control_service, *, close_service=False):
        super().__init__(control_service, close_service=close_service)
        self.prepared_requests = []

    async def prepare_control_tick(self, request):
        self.prepared_requests.append(dict(request))
        return await super().prepare_control_tick(request)


class _FailOncePrepareClient(InProcessControlClient):
    def __init__(self, control_service, *, close_service=False):
        super().__init__(control_service, close_service=close_service)
        self.fail_next_prepare = True

    async def prepare_control_tick(self, request):
        if self.fail_next_prepare:
            self.fail_next_prepare = False
            raise MCPControlUnavailable("simulated pre-commit transport failure")
        return await super().prepare_control_tick(request)


class _FailOnceSignalAckCFWPort(_CFWPort):
    def __init__(self):
        super().__init__()
        self.acknowledgement_attempts = 0

    def acknowledge_behavior_signals(self, *, through_cursor):
        self.acknowledgement_attempts += 1
        if self.acknowledgement_attempts == 1:
            raise OSError("simulated CFW signal acknowledgement interruption")
        super().acknowledge_behavior_signals(through_cursor=through_cursor)


def _client(tmp_path, *, client_type=MCPControlClient):
    env = dict(os.environ)
    env.update({
        "DIGITALPSYCHOLOGY_STATE_ROOT": str(tmp_path / "dp-state"),
        "DIGITALPSYCHOLOGY_EVENT_DB": str(tmp_path / "events.sqlite3"),
        "DIGITALPSYCHOLOGY_BEHAVIOR_STATE_DB": str(tmp_path / "behavior.sqlite3"),
        "DIGITALPSYCHOLOGY_SYSTEM_ONE_MODE": "disabled",
        "DIGITALPSYCHOLOGY_SEMANTIC_PROVIDER": "disabled",
    })
    return client_type(command=sys.executable, env=env, cwd=ROOT,
                       timeout_seconds=20)


def _in_process_client(tmp_path, *, client_type=InProcessControlClient,
                       close_service=True):
    decision_service = DigitalPsychologyService(
        event_db=tmp_path / "local-events.sqlite3",
        system_one_service=SystemOneService(
            DisabledSystemOneBackend(), mode="disabled"))
    control = ControlPlaneService(
        decision_service=decision_service,
        state_store=BehaviorStateStore(tmp_path / "local-behavior.sqlite3"))
    return client_type(control, close_service=close_service)


_MCP_STDIO_AVAILABLE = all(
    dependency is not None
    for dependency in (ClientSession, StdioServerParameters, stdio_client))


def _capture_private_mcp_processes(monkeypatch):
    """Record real MCP stdio children so qualification can kill one at a cut."""
    import mcp.client.stdio as mcp_stdio

    original = mcp_stdio._create_platform_compatible_process
    processes = []

    async def capture(*args, **kwargs):
        process = await original(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(mcp_stdio, "_create_platform_compatible_process", capture)
    return processes


async def _kill_private_mcp_child(processes):
    assert processes, "MCP client did not spawn a private stdio child"
    process = processes[-1]
    assert process.returncode is None, "private DP MCP child already exited"
    process.kill()
    returncode = await process.wait()
    assert returncode not in (None, 0), "private DP MCP child did not crash"


def _bridge(tmp_path, *, identity, goal_set, cfw_port, csw_port, client=None,
            host_policy_context=None):
    return HostControlBridge(
        identity=identity,
        objective="Render the requested page",
        objective_criteria=("criterion-visual",),
        initial_goal_set=goal_set,
        cfw=CFWRuntimeAdapter(cfw_port) if cfw_port is not None else None,
        csw=csw_port,
        client=client or _client(tmp_path),
        host_policy_context=host_policy_context,
    )


def test_harness_identity_is_pinned_and_same_open_session_pin_is_idempotent(tmp_path):
    async def run():
        harness = HarnessAdapterIdentity(
            harness_id="codex-app-server", adapter_version="1.0.0",
            host_version="codex-cli 0.160.0",
            protocol_version=HARNESS_ADAPTER_PROTOCOL_VERSION,
            capabilities=HarnessCapabilities(pre_action=True, post_action=True),
            catalog_pin={"catalog_version": "test"}, verified=False)
        bridge = _bridge(
            tmp_path, identity=_identity("harness-pin-task"),
            goal_set=_goal_set(), cfw_port=_CFWPort(), csw_port=_CSWPort())

        assert bridge.bind_harness_adapter_identity(harness.to_dict()) == harness.to_dict()
        await bridge.open()
        persisted = bridge._session_descriptor.host_capabilities[
            "harness_adapter_identity"]
        assert persisted == harness.to_dict()
        assert bridge.bind_harness_adapter_identity(persisted) == persisted

        conflicting = dict(harness.to_dict())
        conflicting["harness_id"] = "another-harness"
        with pytest.raises(RuntimeError, match="before DP session open"):
            bridge.bind_harness_adapter_identity(conflicting)
        await bridge.close()

    asyncio.run(run())


class _UnavailableControlClient:
    async def start(self):
        raise MCPControlUnavailable("server unavailable")

    async def close(self):
        return None


def test_dp_mcp_outage_keeps_host_runtime_usable(tmp_path):
    async def run():
        cfw = _CFWPort()
        cfw.local_fuse_mode = "constrained"
        cfw.local_fuse_expiry = 0
        cfw.effective_projection = {
            "behavior_revision": 7, "cognitive_mode": "VERIFY",
            "framework_modes": {"owl": "normal", "anchor": "passive",
                                "fuse": "normal", "ward": "mandatory",
                                "flow": "off", "sispis": "normal", "dox": "off"},
            "focus_criteria": ["criterion-visual"],
            "verification_intensity": "strict", "exploration_budget": "low",
            "retry_policy": "materially_different_only", "recovery_level": 1,
            "context_strategy": "criterion_focused", "response_strategy": "structured",
            "target_leases": [{"target": "control:response_strategy"}],
        }
        csw = _CSWPort()
        csw.active = ("repair",)
        csw.activation_interventions["repair"] = "intervention-offline"
        csw.lease_expiries["repair"] = 0
        bridge = HostControlBridge(
            identity=_identity("offline-task"),
            objective="Render the requested page",
            objective_criteria=("criterion-visual",),
            initial_goal_set=_goal_set(), cfw=CFWRuntimeAdapter(cfw), csw=csw,
            client=_UnavailableControlClient(),
            production_metrics=ProductionMetrics(
                tmp_path / "offline-production-metrics.sqlite3"),
        )
        opened = await bridge.open()
        assert opened["status"] == "degraded"
        context = await bridge.before_model_call("host context")
        assert "host context" in context
        assert csw.expiration_calls == []
        result = await bridge.after_action(RuntimeObservation(
            action_index=1, equivalent_retry_count=2,
            criterion_status={"criterion-visual": "unmet"},
        ))
        assert result["status"] == "degraded"
        assert cfw.applied_behavior_projection is None
        assert csw.expiration_calls == [1]
        assert csw.active == ()
        metrics = bridge.production_metrics_snapshot()
        assert metrics["counters"]["degraded_periods"] == 1
        assert metrics["counters"]["dp_outages"] == 1
        assert metrics["counters"]["lease_expirations_during_outage"] >= 1
        offline_context = await bridge.before_model_call("context after local expiry")
        assert "Current operating mode: verify." in offline_context
        assert "Center working context on the current focus criteria" in offline_context
        assert bridge.audit_projection["effective_cfw_controls"][
            "response_strategy"] == "structured"
        await bridge.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    ("cfw_connected", "csw_connected"),
    ((False, False), (True, False), (False, True), (True, True)),
)
def test_dp_host_bridge_subsystem_matrix(tmp_path, cfw_connected, csw_connected):
    async def run():
        goals = GoalSetSnapshot(1, (GoalItemSnapshot(
            "criterion-visual", status="READY",
            claim="Render the requested page at the target viewport.",
            equivalent_retry_count=2),))
        cfw = _CFWPort() if cfw_connected else None
        csw = _CSWPort() if csw_connected else None
        if cfw is not None:
            cfw.signals = []
        client = _in_process_client(tmp_path, close_service=True)
        bridge = _bridge(
            tmp_path,
            identity=_identity(
                f"subsystem-matrix-{cfw_connected}-{csw_connected}"),
            goal_set=goals, cfw_port=cfw, csw_port=csw, client=client)
        try:
            assert (await bridge.open())["status"] == "ready"
            result = await bridge.after_action(RuntimeObservation(
                action_index=1, equivalent_retry_count=2,
                criterion_status={"criterion-visual": "unmet"},
                goal_set=goals))
            assert result["status"] == "ready", result.get("fault")
            context = await bridge.before_model_call("subsystem matrix")
            assert "Current operating mode:" in context
            if cfw is not None:
                assert cfw.lifecycle_revision == bridge.behavior_state.revision
            if csw is not None:
                assert csw.active == ("repair",)
        finally:
            await bridge.close()
            await client.close()

    asyncio.run(run())


def test_two_agents_sharing_dp_process_keep_runtime_state_separate(tmp_path):
    from dataclasses import replace

    async def run():
        goals = _goal_set()
        identity_a = replace(
            _identity("shared-process-task-a"), agent_instance_id="agent-a",
            agent_id="codex-a", session_id="shared-session")
        identity_b = replace(
            _identity("shared-process-task-b"), agent_instance_id="agent-b",
            agent_id="codex-b", session_id="shared-session")
        cfw_a = _CFWPort()
        cfw_b = _CFWPort()
        cfw_b.signals = []
        csw_a = _CSWPort()
        csw_b = _CSWPort()
        csw_b.options = ()
        client = _in_process_client(tmp_path, close_service=False)
        bridge_a = _bridge(
            tmp_path, identity=identity_a, goal_set=goals,
            cfw_port=cfw_a, csw_port=csw_a, client=client)
        bridge_b = _bridge(
            tmp_path, identity=identity_b, goal_set=goals,
            cfw_port=cfw_b, csw_port=csw_b, client=client)
        try:
            assert (await bridge_a.open())["status"] == "ready"
            assert (await bridge_b.open())["status"] == "ready"

            result_a = await bridge_a.after_action(RuntimeObservation(
                action_index=1, criterion_status={"criterion-visual": "unmet"},
                goal_set=goals))
            assert result_a["status"] == "ready"
            state_a_after_action = bridge_a.behavior_state
            assert state_a_after_action.active_interventions
            assert state_a_after_action.active_stateworks == ("repair",)

            # The second host has not reached a boundary yet; its DP, CFW, and
            # CSW state must remain untouched despite sharing the DP service.
            assert bridge_b.behavior_state.revision == 0
            assert bridge_b.behavior_state.active_interventions == ()
            assert cfw_a.signal_ack_cursor == 1
            assert cfw_b.signal_ack_cursor == 0
            assert csw_b.active == ()

            result_b = await bridge_b.after_action(RuntimeObservation(
                action_index=1, criterion_status={"criterion-visual": "unmet"},
                goal_set=goals))
            assert result_b["status"] == "ready"
            assert bridge_b.behavior_state.active_interventions
            assert not ({item.intervention_id for item in
                         bridge_a.behavior_state.active_interventions}
                        & {item.intervention_id for item in
                           bridge_b.behavior_state.active_interventions})
            assert bridge_b.behavior_state.active_stateworks == ()
            assert bridge_a.behavior_state == state_a_after_action
            assert csw_a.active == ("repair",)
            assert csw_b.active == ()
            assert cfw_a.signal_ack_cursor == 1
            assert cfw_b.signal_ack_cursor == 0

            store = client.control_service.state_store
            record_a = store.load(identity_a.scope)
            record_b = store.load(identity_b.scope)
            assert record_a.state.active_interventions
            assert record_b.state.active_interventions
            assert not ({item.intervention_id for item in
                         record_a.state.active_interventions}
                        & {item.intervention_id for item in
                           record_b.state.active_interventions})
            assert record_a.state.active_stateworks == ("repair",)
            assert record_b.state.active_stateworks == ()
        finally:
            await bridge_a.close()
            await bridge_b.close()
            await client.close()

    asyncio.run(run())


def test_bridge_open_rejects_split_csw_authority_for_shared_workflow(tmp_path):
    async def run():
        identity = {
            "namespace_id": "tenant-a", "application_id": "assistant",
            "application_instance_id": "assistant-1", "workflow_id": "task-a",
            "subject_ref": "repo:shared", "registry_hash": "d" * 64,
            "authority_instance_id": "authority-a", "storage_identity": "store-a",
            "contract_version": "1.0.0",
        }
        cfw = _CFWPort()
        cfw.workflow_authority_identities = lambda: {"gitter": dict(identity)}
        csw = _CSWPort()
        csw.workflow_authority_identities = lambda: {
            "gitter": {**identity, "storage_identity": "store-b"}}
        bridge = _bridge(
            tmp_path, identity=_identity("authority-split"),
            goal_set=_goal_set(), cfw_port=cfw, csw_port=csw,
            client=_in_process_client(tmp_path))

        with pytest.raises(RuntimeError, match=
                           "CFW and DP are bound to different CSW workflow authorities"):
            await bridge.open()
        assert not bridge._remote_open
        await bridge.close()

    asyncio.run(run())


def test_bridge_open_requires_full_cfw_workflow_coverage(tmp_path):
    async def run():
        def authority_identity(statework_id):
            return {
                "namespace_id": "tenant-a",
                "application_id": "assistant",
                "application_instance_id": "assistant-1",
                "workflow_id": f"task-{statework_id}",
                "subject_ref": "repo:shared",
                "registry_hash": "d" * 64,
                "authority_instance_id": f"authority-{statework_id}",
                "storage_identity": f"store-{statework_id}",
                "contract_version": "1.0.0",
            }

        async def open_bridge(name, cfw_ids, csw_ids, *, mismatch=None,
                              expected_error=None):
            cfw = _CFWPort()
            cfw.workflow_authority_identities = lambda: {
                item: authority_identity(item) for item in cfw_ids}
            csw = _CSWPort()
            identities = {item: authority_identity(item) for item in csw_ids}
            if mismatch is not None:
                identities[mismatch] = {
                    **identities[mismatch], "storage_identity": "wrong-store"}
            csw.workflow_authority_identities = lambda: identities
            bridge = _bridge(
                tmp_path / name, identity=_identity(name), goal_set=_goal_set(),
                cfw_port=cfw, csw_port=csw,
                client=_in_process_client(tmp_path / name),
            )
            if expected_error:
                with pytest.raises(RuntimeError, match=expected_error):
                    await bridge.open()
                assert not bridge._remote_open
            else:
                await bridge.open()
                assert bridge._remote_open
            await bridge.close()

        await open_bridge("exact-coverage", {"A"}, {"A"})
        await open_bridge("additional-csw", {"A", "B"}, {"A", "B", "C"})
        await open_bridge(
            "partial-coverage", {"A", "B"}, {"A", "C"},
            expected_error="DP CSW authority does not cover CFW workflows: \\['B'\\]",
        )
        await open_bridge(
            "identity-mismatch", {"A"}, {"A"}, mismatch="A",
            expected_error=(
                "CFW and DP are bound to different CSW workflow authorities"),
        )

    asyncio.run(run())


def test_degraded_projection_overlays_cfw_controls_on_last_committed_dp_state(tmp_path):
    cfw = _CFWPort()
    cfw.effective_projection = {
        "cognitive_mode": "VERIFY",
        "focus_criteria": ["criterion-live"],
    }
    bridge = _bridge(tmp_path, identity=_identity("offline-state-overlay"),
                     goal_set=_goal_set(), cfw_port=cfw, csw_port=_CSWPort())
    bridge._offline_controls_expired = True
    bridge._state = BehaviorState(
        revision=8, cognitive_mode="RECOVER", focus_criteria=("criterion-last",),
        recovery_level=2, attention_last_seen={"criterion-last": 7})

    projected, controls = bridge.projection_manager._offline_behavior_state()

    assert projected.revision == 8
    assert projected.cognitive_mode == "VERIFY"
    assert projected.focus_criteria == ("criterion-live",)
    assert projected.recovery_level == 2
    assert dict(projected.attention_last_seen) == {"criterion-last": 7}
    assert controls["cognitive_mode"] == "VERIFY"


def test_candidate_gate_fails_closed_when_local_expiry_fails(tmp_path):
    async def run():
        cfw = _CFWPort()
        bridge = _bridge(tmp_path, identity=_identity("expiry-failure"),
                         goal_set=_goal_set(), cfw_port=cfw, csw_port=_CSWPort(),
                         client=_in_process_client(tmp_path))
        try:
            assert (await bridge.open())["status"] == "ready"

            def fail_expiry(_action_index):
                raise RuntimeError("simulated lease expiry failure")

            cfw.expire_local_behavior_leases = fail_expiry
            result = await bridge.before_action_candidate(
                ActionCandidate(
                    action_class="file_edit", proposed_purpose="Implement the criterion",
                    affected_criterion_ids=("criterion-visual",),
                    action_id="candidate-expiry-failure", tool_name="apply_patch",
                    operation="edit_file", mutating=True, reversible=True),
                cfw_action_request={"tool_binding_id": "apply_patch",
                                    "arguments": {"path": "view.py"}})

            assert result["action_authorized"] is False
            assert result["stage"] == "boundary_preparation"
            assert cfw.action_gate_calls == []
        finally:
            await bridge.close()

    asyncio.run(run())


def test_cfw_signal_batch_retries_until_commit_then_stops_replaying(tmp_path):
    control_client = _in_process_client(
        tmp_path, client_type=_FailOncePrepareClient, close_service=True)
    cfw = _CFWPort()
    goals = _goal_set()
    csw = _CSWPort()
    csw.options = ()  # isolate signal-driven recovery from route preference
    bridge = _bridge(tmp_path, identity=_identity("signal-retry-task"),
                     goal_set=goals, cfw_port=cfw, csw_port=csw,
                     client=control_client)

    async def run():
        try:
            assert (await bridge.open())["status"] == "ready"
            observation = RuntimeObservation(
                action_index=1, goal_set=goals,
                criterion_status={"criterion-visual": "unmet"})
            failed = await bridge.after_action(observation)
            assert failed["status"] == "degraded"
            assert cfw.signal_ack_cursor == 0
            pending = CFWRuntimeAdapter(cfw).peek_behavior_signals(
                after_cursor=None, maximum=32, observation=observation)
            assert len(pending.signals) == 1
            assert pending.next_cursor == 1

            committed = await bridge.after_action(observation)
            assert committed["status"] == "ready", committed.get("fault")
            assert cfw.signal_ack_cursor == 1
            original_intervention_ids = {
                item.intervention_id
                for item in bridge.behavior_state.active_interventions
            }
            # Exercise the committed cursor across a bounded soak. The signal
            # remains in CFW's retryable log, but must not be semantically
            # applied again after DP commits and acknowledges cursor 1.
            for action_index in range(2, 258):
                boundary = await bridge.after_action(RuntimeObservation(
                    action_index=action_index, goal_set=goals,
                    criterion_status={"criterion-visual": "unmet"}))
                assert boundary["status"] == "ready", boundary.get("fault")
                current_ids = {item.intervention_id for item in
                               bridge.behavior_state.active_interventions}
                assert current_ids.issubset(original_intervention_ids), [
                    (item.intervention_id, item.cause, item.condition_id)
                    for item in bridge.behavior_state.active_interventions]

            assert cfw.signal_ack_cursor == 1
            remaining = CFWRuntimeAdapter(cfw).peek_behavior_signals(
                after_cursor=None, maximum=32, observation=observation)
            assert remaining.signals == ()
            assert remaining.next_cursor == 1
        finally:
            await bridge.close()

    asyncio.run(run())


def test_cfw_signal_ack_failure_reconciles_once_after_host_bridge_restart(tmp_path):
    async def run():
        identity = _identity("signal-ack-restart-task")
        cfw = _FailOnceSignalAckCFWPort()
        csw = _CSWPort()
        csw.options = ()
        goals = _goal_set()
        first_client = _in_process_client(tmp_path, close_service=True)
        first_bridge = _bridge(
            tmp_path, identity=identity, goal_set=goals,
            cfw_port=cfw, csw_port=csw, client=first_client)
        assert (await first_bridge.open())["status"] == "ready"

        degraded = await first_bridge.after_action(RuntimeObservation(
            action_index=1, goal_set=goals,
            criterion_status={"criterion-visual": "unmet"},
        ))
        assert degraded["status"] == "degraded"
        assert cfw.signal_ack_cursor == 0
        assert cfw.acknowledgement_attempts == 1
        committed = first_client.control_service.state_store.load(identity.scope)
        assert committed.action_index == 1
        assert committed.cfw_signal_cursor == 1
        applied_interventions = {
            item.intervention_id for item in committed.state.active_interventions}

        # A fresh bridge/client pair models host and DP process recreation. The
        # original committed cursor lets reconciliation acknowledge the signal
        # before the next boundary can consume it again.
        resumed_client = _in_process_client(tmp_path, close_service=True)
        resumed_bridge = _bridge(
            tmp_path, identity=identity, goal_set=goals,
            cfw_port=cfw, csw_port=csw, client=resumed_client)
        try:
            assert (await resumed_bridge.open())["status"] == "ready"
            assert cfw.signal_ack_cursor == 1
            resumed = await resumed_bridge.after_action(RuntimeObservation(
                action_index=2, goal_set=goals,
                criterion_status={"criterion-visual": "unmet"},
            ))
            assert resumed["status"] == "ready", resumed.get("fault")
            current = {
                item.intervention_id
                for item in resumed_bridge.behavior_state.active_interventions}
            assert current.issubset(applied_interventions)
            # One restart reconciliation plus the next post-action's
            # idempotent acknowledgement of the already committed cursor.
            assert cfw.acknowledgement_attempts == 3
            assert cfw.signal_ack_cursor == 1
            persisted = resumed_client.control_service.state_store.load(identity.scope)
            assert persisted.cfw_signal_cursor == 1
        finally:
            await resumed_bridge.close()
            await resumed_client.close()
            # Do not close first_bridge: a crash/restart leaves its session open.

    asyncio.run(run())


def test_expired_cfw_lease_is_removed_before_candidate_preflight(tmp_path):
    async def run():
        cfw = _CFWPort(preflight_decision="block")
        cfw.signals = []
        bridge = _bridge(tmp_path, identity=_identity("candidate-expiry"),
                         goal_set=_goal_set(), cfw_port=cfw, csw_port=_CSWPort(),
                         client=_in_process_client(tmp_path))
        try:
            assert (await bridge.open())["status"] == "ready"
            first = await bridge.after_action(RuntimeObservation(
                action_index=1, goal_set=_goal_set(),
                criterion_status={"criterion-visual": "unmet"}))
            assert first["status"] == "ready"
            cfw.local_fuse_mode = "constrained"
            cfw.local_fuse_expiry = 1

            result = await bridge.before_action_candidate(
                ActionCandidate(
                    action_class="file_edit", proposed_purpose="Implement the criterion",
                    affected_criterion_ids=("criterion-visual",),
                    action_id="candidate-after-expiry", tool_name="apply_patch",
                    operation="edit_file", mutating=True, reversible=True),
                cfw_action_request={"tool_binding_id": "apply_patch",
                                    "arguments": {"path": "view.py"}})

            assert result["stage"] == "cfw_preflight"
            assert cfw.preflight_fuse_modes == ["normal"]
            assert bridge._action_index == 1
        finally:
            await bridge.close()

    asyncio.run(run())


@pytest.mark.skipif(not _MCP_STDIO_AVAILABLE,
                    reason="MCP stdio transport dependencies are unavailable")
def test_private_stdio_mcp_applies_cfw_and_csw_then_commits(tmp_path):
    async def run():
        cfw = _CFWPort()
        csw = _CSWPort()
        goals = _goal_set()
        identity = _identity()
        bridge = _bridge(tmp_path, identity=identity, goal_set=goals,
                         cfw_port=cfw, csw_port=csw)
        try:
            opened = await bridge.open()
            assert opened["status"] == "ready"
            result = await bridge.after_action(RuntimeObservation(
                action_index=1,
                equivalent_retry_count=2,
                criterion_status={"criterion-visual": "unmet"},
            ), goal_set=goals)
            assert result["status"] == "ready"
            assert result["behavior_state"]["cognitive_mode"] == "RECOVER"
            assert csw.active == ("repair",)
            assert cfw.lifecycle_revision == 1
            assert cfw.applied_behavior_projection["behavior_revision"] == 1
            assert cfw.controls
            context = await bridge.before_model_call("base host context")
            assert "CURRENT BEHAVIOR GUIDANCE" in context
            assert "base host context" in context
            second_context = await bridge.before_model_call("second model call")
            assert "second model call" in second_context
            trial = await bridge.client.behavioral_trial_record(
                identity.scope, trial_id="private-stdio-trial")
            assert trial["protocol_version"] == PROTOCOL_VERSION
            assert trial["trial_id"] == "private-stdio-trial"
            assert trial["evidence_source"] == "internal_host_telemetry"
            assert trial["equivalent_retries"] == 1
            assert trial["model_projection_events"] == 2
            assert trial["model_projection_updates"] == 1
            assert trial["unique_model_projection_digests"] == 2
            delivery = trial["runtime_event_delivery"]
            assert delivery["committed_count"] >= 1
            assert delivery["enqueued_count"] == delivery["committed_count"]
            assert delivery["pending_count"] == 0
            assert delivery["delivered_count"] == delivery["authenticated_count"]
            assert delivery["authenticated_status_available"] is True
            assert delivery["last_successful_delivery_at"]
            metrics = bridge.production_metrics_snapshot()
            assert metrics["counters"]["control_ticks_prepared"] >= 1
            assert metrics["counters"]["control_ticks_committed"] >= 1
            assert metrics["timers"]["control_tick_prepare_duration_ms"]["count"] >= 1
            assert metrics["timers"]["control_tick_commit_duration_ms"]["count"] >= 1
        finally:
            await bridge.close()

    asyncio.run(run())


def test_in_process_client_runs_the_same_host_bridge_transaction(tmp_path):
    async def run():
        identity = _identity("in-process-control-task")
        goals = _goal_set()
        decision_service = DigitalPsychologyService(
            event_db=tmp_path / "in-process-events.sqlite3",
            system_one_service=SystemOneService(
                DisabledSystemOneBackend(), mode="disabled"))
        control = ControlPlaneService(
            decision_service=decision_service,
            state_store=BehaviorStateStore(tmp_path / "in-process-behavior.sqlite3"))
        client = InProcessControlClient(control)
        cfw = _CFWPort()
        csw = _CSWPort()
        bridge = _bridge(tmp_path, identity=identity, goal_set=goals,
                         cfw_port=cfw, csw_port=csw, client=client)
        try:
            opened = await bridge.open()
            result = await bridge.after_action(RuntimeObservation(
                action_index=1, equivalent_retry_count=2,
                criterion_status={"criterion-visual": "unmet"},
            ), goal_set=goals)

            assert opened["status"] == "ready"
            assert result["status"] == "ready"
            assert result["behavior_state"]["cognitive_mode"] == "RECOVER"
            assert csw.active == ("repair",)
            assert cfw.lifecycle_revision == 1
        finally:
            await bridge.close()
            control.close()

    asyncio.run(run())


def test_private_control_plane_persists_actions_and_boundary_transitions_separately(
        tmp_path):
    async def run():
        identity = _identity("persisted-control-trace")
        goals = _goal_set()
        decision_service = DigitalPsychologyService(
            event_db=tmp_path / "trace-events.sqlite3",
            system_one_service=SystemOneService(
                DisabledSystemOneBackend(), mode="disabled"))
        store = BehaviorStateStore(tmp_path / "trace-behavior.sqlite3")
        control = ControlPlaneService(
            decision_service=decision_service, state_store=store)
        client = InProcessControlClient(control)
        bridge = _bridge(
            tmp_path, identity=identity, goal_set=goals,
            cfw_port=_CFWPort(preflight_decision="require_confirmation"),
            csw_port=_CSWPort(), client=client,
            host_policy_context={
                "configuration": "cfw+csw+dp+laya",
                "semantic_provider_status": "enabled",
                "semantic_model_hash": "d" * 64,
                "deliverable_kind": "visual_ui",
            })
        try:
            await bridge.open()
            rejected = await bridge.before_action_candidate(
                ActionCandidate(
                    action_class="external_write",
                    proposed_purpose="Publish the result",
                    affected_criterion_ids=("criterion-visual",),
                    action_id="rejected-publish", tool_name="deploy",
                    operation="publish", mutating=True, reversible=False,
                    external=True),
                cfw_action_request={
                    "tool_binding_id": "deploy", "arguments": {"target": "prod"}})
            assert rejected["stage"] == "cfw_preflight"
            for action_index in (1, 2):
                result = await bridge.after_action(RuntimeObservation(
                    action_index=action_index,
                    criterion_status={"criterion-visual": "unmet"},
                ), goal_set=goals)
                assert result["status"] == "ready"

            trial = control.behavioral_trial_record(
                identity.scope, trial_id="trace-trial")
            actions = store.behavioral_action_traces(identity.scope)
            transitions = store.committed_control_transitions(identity.scope)
            assert trial["committed_actions"] == 2
            assert [item["action_index"] for item in actions] == [1, 2]
            assert trial["control_transition_count"] == 3
            assert trial["cfw_preflight_rejections"] == 1
            assert trial["configuration"] == "cfw+csw+dp"
            assert trial["semantic_provider_status"] == "disabled"
            assert trial["semantic_model_hash"] is None
            assert [item["boundary_sequence"] for item in transitions] == [0, 1, 2]
            assert transitions[0]["boundary_kind"] == "pre_action_rejected"
            assert transitions[0]["cfw_rejection_stage"] == "cfw_preflight"
            assert transitions[0]["cfw_gate_receipts"]["preflight"][
                "authority_id"] == "test-cfw"
            assert transitions[0]["cfw_gate_receipts"]["preflight"][
                "reason_codes"] == ["test.action_policy.require_confirmation"]
            assert transitions[0]["justification_class"] != "hard_safety"
            assert all("basis" not in item for item in transitions)
            assert "semantic_query_attempted" in actions[-1]
            assert "semantic_query" not in actions[-1]
            assert "semantic_attempted" not in actions[-1]
        finally:
            await bridge.close()
            control.close()

    asyncio.run(run())


def test_model_and_tool_planning_boundaries_have_distinct_trace_kinds(tmp_path):
    async def run():
        goals = _goal_set()
        client = _in_process_client(
            tmp_path, client_type=_CapturingControlClient, close_service=False)
        bridge = _bridge(tmp_path, identity=_identity("planning-boundary-trace"),
                         goal_set=goals, cfw_port=_CFWPort(), csw_port=_CSWPort(),
                         client=client)
        try:
            await bridge.open()
            await bridge.before_model_call("model input")
            await bridge.before_tool_planning("tool planning input")
            assert [item["boundary_kind"] for item in client.prepared_requests] == [
                "pre_model_call", "pre_tool_planning"]
            assert [item["boundary_sequence"] for item in client.prepared_requests] == [
                0, 1]
            assert [item["observation"]["action_index"]
                    for item in client.prepared_requests] == [0, 0]
        finally:
            await bridge.close()
            await client.close()

    asyncio.run(run())


def test_model_projection_uses_descriptor_evidence_capability(
        tmp_path, monkeypatch):
    from lib.control import transaction_service

    observed = []
    original = transaction_service.build_behavior_projection

    def capture_projection(*args, **kwargs):
        observed.append(dict(kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(transaction_service, "build_behavior_projection",
                        capture_projection)

    async def run():
        goals = _goal_set()
        client = _in_process_client(tmp_path, close_service=False)
        bridge = HostControlBridge(
            identity=_identity("reconcile-evidence-capability"),
            objective="Render the requested page",
            objective_criteria=("criterion-visual",),
            initial_goal_set=goals, cfw=CFWRuntimeAdapter(_CFWPort()),
            csw=_CSWPort(), client=client,
            outcome_evidence_connected=False,
        )
        try:
            await bridge.open()
            observation = RuntimeObservation(
                action_index=1, criterion_status={"criterion-visual": "unmet"},
                goal_set=goals)
            snapshot, _ = bridge._csw_snapshot()
            request = ControlTickRequest(
                control_session_id=bridge.identity.scope,
                tick_id="reconcile-evidence-capability-probe",
                expected_behavior_revision=bridge.behavior_state.revision,
                cfw_lifecycle_revision=bridge._current_cfw_revision(),
                observation=observation, goal_set=goals,
                cfw_signals=bridge._cfw_signals(observation),
                csw_snapshot=snapshot)
            prepared = await client.prepare_control_tick(request.to_dict())
            assert observed[-1]["outcome_evidence_connected"] is False
            await client.abort_control_tick(ControlAbortRequest(
                control_session_id=bridge.identity.scope,
                tick_id=prepared["tick_id"], decision_id=prepared["decision_id"],
                basis_digest=prepared["basis_digest"], applied_directive_ids=(),
                rollback_completed=True, failure_class="test_cleanup",
                cfw_lifecycle_revision=bridge._current_cfw_revision(),
                csw_snapshot=snapshot).to_dict())
            reconciled = await client.reconcile_control_session({
                "protocol_version": PROTOCOL_VERSION,
                "control_session_id": bridge.identity.scope,
                "cfw_lifecycle_revision": bridge._current_cfw_revision(),
                "csw_snapshot": snapshot.to_dict(),
            })
            assert "projection" not in reconciled
        finally:
            await bridge.close()

    asyncio.run(run())


def test_cfw_task_floor_seeds_state_and_rejects_candidates_and_directives():
    from types import SimpleNamespace

    from lib.control.policy_selector import (
        BehaviorCandidate, BehaviorConstraintResolver, BehaviorPolicyConstraints,
    )
    from lib.runtime.cfw_behavior_contract import (
        cfw_suppression_modes, initial_behavior_state,
        validate_directives_against_cfw_contract,
        validate_state_against_cfw_contract,
    )

    port = _CFWPort()
    contract = port.task_behavior_contract()
    goal_set = _goal_set()
    descriptor = ControlSessionDescriptor(
        identity=_identity("task-baseline-contract"),
        objective="Render the requested page",
        objective_criteria=("criterion-visual",), initial_goal_set=goal_set,
        available_frameworks=tuple(port.available_component_ids()),
        cfw_authority_identity=port.authority_identity(),
        csw_authority_identity={},
        host_capabilities={
            "cfw_connected": True, "csw_connected": False,
            "outcome_evidence_connected": False,
            "cfw_behavior_contract": contract,
        },
    )
    initial = initial_behavior_state(descriptor)
    assert initial.framework_modes["anchor"] == "normal"
    stale = BehaviorState.from_mapping({
        **initial.to_dict(),
        "framework_modes": {**dict(initial.framework_modes), "owl": "off"},
    })
    with pytest.raises(PermissionError, match="OWL task baseline floor"):
        validate_state_against_cfw_contract(stale, descriptor)

    candidate = BehaviorCandidate.from_state(
        stale, focus_criteria=("criterion-visual",), statework_preferences=(),
        reason_codes=("test",), cause="test", expected_effect="test")
    constraints = BehaviorPolicyConstraints(
        current_state=initial, available_frameworks=descriptor.available_frameworks,
        goal_set=goal_set,
        cfw_suppression_modes=cfw_suppression_modes(descriptor))
    reason = BehaviorConstraintResolver().reject_reason(
        candidate, constraints, CSWBehaviorSnapshot(revision=0))
    assert reason == "cfw_task_baseline_floor:owl"

    with pytest.raises(PermissionError, match="OWL task baseline floor"):
        validate_directives_against_cfw_contract((SimpleNamespace(
            operation="set_framework", target="owl", value="off"),), descriptor)


def test_host_rejects_nonsequential_candidate_and_completed_action_indices(tmp_path):
    async def run():
        goals = _goal_set()
        bridge = _bridge(tmp_path, identity=_identity("sequential-index"),
                         goal_set=goals, cfw_port=_CFWPort(), csw_port=_CSWPort(),
                         client=_in_process_client(tmp_path))
        try:
            await bridge.open()
            candidate = ActionCandidate(
                action_class="read", proposed_purpose="inspect source",
                affected_criterion_ids=("criterion-visual",),
                action_id="candidate-25", tool_name="read", operation="inspect",
                mutating=False, reversible=True)
            bad_candidate = RuntimeObservation(
                action_index=25, action_completed=False, goal_set=goals,
                candidate_action=candidate)
            with pytest.raises(ValueError, match="next sequential action"):
                await bridge._control_boundary("pre_action", observation=bad_candidate)
            bad_completion = RuntimeObservation(action_index=25, goal_set=goals)
            with pytest.raises(ValueError, match="next sequential action"):
                await bridge.after_action(bad_completion, goal_set=goals)
        finally:
            await bridge.close()

    asyncio.run(run())


def test_validator_evidence_is_allowed_without_marking_an_action_complete():
    evidence = RuntimeObservation(
        action_index=2, action_completed=False,
        evidence_refs=("validator:criterion-visual:revision-1",))
    assert evidence.action_completed is False

    candidate = ActionCandidate(
        action_class="read", proposed_purpose="inspect source",
        affected_criterion_ids=("criterion-visual",), action_id="candidate-evidence",
        tool_name="read", operation="inspect", mutating=False, reversible=True)
    with pytest.raises(ValueError, match="candidate observations cannot claim action outcomes"):
        RuntimeObservation(action_index=3, action_completed=False,
                           candidate_action=candidate,
                           evidence_refs=("validator:criterion-visual:revision-1",))


@pytest.mark.skipif(not _MCP_STDIO_AVAILABLE,
                    reason="MCP stdio transport dependencies are unavailable")
def test_private_stdio_mcp_abort_response_keeps_protocol_version(tmp_path):
    async def run():
        identity = _identity("abort-protocol-task")
        goals = _goal_set()
        bridge = _bridge(tmp_path, identity=identity, goal_set=goals,
                         cfw_port=_CFWPort(), csw_port=_CSWPort())
        try:
            await bridge.open()
            observation = RuntimeObservation(
                action_index=1, equivalent_retry_count=2,
                criterion_status={"criterion-visual": "unmet"}, goal_set=goals)
            snapshot, _ = bridge._csw_snapshot()
            request = ControlTickRequest(
                control_session_id=identity.scope, tick_id="abort-protocol-tick",
                expected_behavior_revision=bridge.behavior_state.revision,
                cfw_lifecycle_revision=bridge._current_cfw_revision(),
                observation=observation, goal_set=goals,
                cfw_signals=bridge._cfw_signals(observation), csw_snapshot=snapshot)
            prepared = await bridge.client.prepare_control_tick(request.to_dict())
            abort = ControlAbortRequest(
                control_session_id=identity.scope, tick_id=prepared["tick_id"],
                decision_id=prepared["decision_id"],
                basis_digest=prepared["basis_digest"], applied_directive_ids=(),
                rollback_completed=True, failure_class="integration_test_abort",
                cfw_lifecycle_revision=bridge._current_cfw_revision(),
                csw_snapshot=snapshot)

            result = await bridge.client.abort_control_tick(abort.to_dict())

            assert result["protocol_version"] == PROTOCOL_VERSION
            assert result["status"] == "aborted"
        finally:
            await bridge.close()

    asyncio.run(run())


def test_system_one_receipt_stays_in_host_audit_not_model_context(tmp_path):
    async def run():
        bridge = HostControlBridge(
            identity=_identity("system-one-audit"),
            objective="Render the requested page",
            objective_criteria=("criterion-visual",),
            initial_goal_set=_goal_set(), cfw=CFWRuntimeAdapter(_CFWPort()),
            csw=_CSWPort(), client=_UnavailableControlClient(),
            production_metrics=ProductionMetrics(
                tmp_path / "system-one-production-metrics.sqlite3"),
        )
        try:
            await bridge.open()
            bridge._record_system_one_observation({
                "tick_id": "semantic-tick",
                "basis": {"system_one": {
                    "frame": {"questions": [{"question_id": "retry_relation"}]},
                    "result": {
                        "status": "shadow", "cache_hit": False,
                        "calibrated_question_ids": [],
                        "judgment": {
                            "backend_id": "digitalpsychology-laya-native",
                            "model_id": "laya-typed-decisions",
                            "model_revision": "pinned-revision",
                            "model_content_hash": "a" * 64,
                            "provider_protocol_hash": "b" * 64,
                            "calibration_bundle_hash": "",
                            "state_digest": "c" * 64,
                            "question_bundle_digest": "d" * 64,
                            "sequence": 1,
                            "answers": [{
                                "question_id": "retry_relation",
                                "selected": "materially_new",
                                "raw_probabilities": [{
                                    "label": "materially_new", "probability": 0.8}],
                                "calibrated_probabilities": None,
                            }],
                        },
                    },
                }}}, tick_status="committed")
            context = await bridge.before_model_call("ordinary host context")
            receipt = bridge.system_one_observation
            assert receipt is not None
            assert receipt["status"] == "shadow"
            assert receipt["backend_id"] == "digitalpsychology-laya-native"
            assert receipt["answers"][0]["selected"] == "materially_new"
            assert receipt["answers"][0]["raw_confidence"] == 0.8
            assert bridge.audit_projection["system_one"]["tick_id"] == "semantic-tick"
            assert "materially_new" not in context
            assert "digitalpsychology-laya-native" not in context
        finally:
            await bridge.close()

    asyncio.run(run())


def test_candidate_action_is_committed_before_execution_without_advancing_action_index(
        tmp_path):
    async def run():
        cfw = _CFWPort()
        goals = _goal_set()
        bridge = _bridge(tmp_path, identity=_identity("candidate-task"),
                         goal_set=goals, cfw_port=cfw, csw_port=_CSWPort(),
                         client=_in_process_client(tmp_path))
        try:
            opened = await bridge.open()
            assert opened["status"] == "ready"
            result = await bridge.before_action_candidate(
                ActionCandidate(
                    action_class="file_edit",
                    proposed_purpose="Implement the requested visual criterion",
                    affected_criterion_ids=("criterion-visual",),
                    action_id="action-visual-1", tool_name="apply_patch",
                    operation="edit_file", mutating=True, reversible=True,
                    external=False,
                    target_kind="repository",
                ), cfw_action_request={
                    "tool_binding_id": "apply_patch",
                    "arguments": {"patch": "bounded-host-action"},
                })
            assert result["action_authorized"] is True
            assert result["action_request_id"] == "action-request-host-1"
            assert cfw.action_gate_calls == ["preflight", "authorize"]
            assert bridge.behavior_state.revision == 1
            assert bridge._action_index == -1
            assert cfw.lifecycle_revision == 1

            result = await bridge.after_action(RuntimeObservation(
                action_index=1, goal_set=goals,
                criterion_status={"criterion-visual": "unmet"},
            ), goal_set=goals)
            assert result["status"] == "ready"
            assert bridge._action_index == 1
            assert bridge.behavior_state.revision == 2
        finally:
            await bridge.close()

    asyncio.run(run())


def test_cfw_preflight_rejection_is_reported_to_dp_without_advancing_action_index(tmp_path):
    async def run():
        cfw = _CFWPort(preflight_decision="require_confirmation")
        goals = _goal_set()
        client = _in_process_client(
            tmp_path, client_type=_CapturingControlClient, close_service=True)
        bridge = _bridge(tmp_path, identity=_identity("rejected-candidate-task"),
                         goal_set=goals, cfw_port=cfw, csw_port=_CSWPort(),
                         client=client)
        try:
            await bridge.open()
            cfw.signals = []
            for action_index in range(1, 10):
                completed = await bridge.after_action(RuntimeObservation(
                    action_index=action_index, goal_set=goals))
                assert completed["status"] == "ready", completed.get("fault")
            assert bridge._action_index == 9
            for attempt in range(3):
                result = await bridge.before_action_candidate(
                    ActionCandidate(
                        action_class="external_write",
                        proposed_purpose="Apply the requested change",
                        affected_criterion_ids=("criterion-visual",),
                        action_id=f"action-needs-confirmation-{attempt}",
                        tool_name="deploy", operation="publish",
                        mutating=True, reversible=False, external=True,
                    ),
                    cfw_action_request={
                        "tool_binding_id": "deploy", "arguments": {"target": "prod"}},
                )
                assert result["status"] == "rejected"
                assert result["stage"] == "cfw_preflight"
                assert result["action_authorized"] is False
            assert cfw.action_gate_calls == ["preflight"] * 3
            assert bridge._action_index == 9
            assert bridge._boundary_sequence == 11
            requests = client.prepared_requests[-3:]
            assert [request["observation"]["action_index"]
                    for request in requests] == [10, 10, 10]
            assert [request["boundary_sequence"] for request in requests] == [9, 10, 11]
            for attempt, request in enumerate(requests):
                assert request["boundary_kind"] == "pre_action_rejected"
                assert request["observation"]["candidate_preflight_result"] == \
                    "require_confirmation"
                assert request["observation"]["candidate_gate_receipts"][
                    "preflight"]["adapter_protocol_version"] == "1.2.0"
                assert request["observation"]["candidate_action"]["action_id"] == \
                    f"action-needs-confirmation-{attempt}"
                assert any(signal["code"] ==
                           "candidate_rejected.require_confirmation"
                           for signal in request["cfw_signals"])
                assert "prod" not in json.dumps(request)
        finally:
            await bridge.close()

    asyncio.run(run())


def test_cfw_final_gate_rejection_is_reported_to_dp_after_pre_action_tick(tmp_path):
    async def run():
        cfw = _CFWPort(final_decision="require_reinspection")
        goals = _goal_set()
        client = _in_process_client(
            tmp_path, client_type=_CapturingControlClient, close_service=True)
        bridge = _bridge(tmp_path, identity=_identity("final-rejection-task"),
                         goal_set=goals, cfw_port=cfw, csw_port=_CSWPort(),
                         client=client)
        try:
            await bridge.open()
            result = await bridge.before_action_candidate(
                ActionCandidate(
                    action_class="file_edit",
                    proposed_purpose="Apply the requested visual fix",
                    affected_criterion_ids=("criterion-visual",),
                    action_id="action-stale-assumption", tool_name="apply_patch",
                    operation="edit_file", mutating=True, reversible=True,
                    external=False,
                ),
                cfw_action_request={
                    "tool_binding_id": "apply_patch", "arguments": {"path": "view.py"}},
            )
            assert result["status"] == "rejected"
            assert result["stage"] == "cfw_final_gate"
            assert result["pre_action_control"]["status"] == "ready"
            assert result["control"]["status"] == "ready"
            assert result["action_authorized"] is False
            assert cfw.action_gate_calls == ["preflight", "authorize"]
            assert bridge._action_index == -1
            assert bridge._boundary_sequence == 1
            assert bridge._cfw_projection_action_index == 1
            assert cfw.applied_behavior_projection["host_action_index"] == 1
            assert [request["observation"]["action_index"]
                    for request in client.prepared_requests] == [1, 1]
            final_receipts = client.prepared_requests[-1]["observation"][
                "candidate_gate_receipts"]
            assert final_receipts["preflight"]["decision"] == "allow"
            assert final_receipts["final"]["decision"] == "require_reinspection"
            assert final_receipts["final"]["reason_codes"] == [
                "test.action_policy.require_reinspection"]
        finally:
            await bridge.close()

    asyncio.run(run())


def test_reconciliation_recovers_projection_candidate_index_after_pre_action_commit(tmp_path):
    async def run():
        identity = _identity("pre-action-reconnect-task")
        goals = _goal_set()
        cfw = _CFWPort()
        csw = _CSWPort()
        client = _in_process_client(tmp_path, close_service=False)
        first = _bridge(tmp_path, identity=identity, goal_set=goals,
                        cfw_port=cfw, csw_port=csw, client=client)
        await first.open()
        result = await first.before_action_candidate(
            ActionCandidate(
                action_class="file_edit",
                proposed_purpose="Implement the requested visual criterion",
                affected_criterion_ids=("criterion-visual",),
                action_id="action-before-reconnect", tool_name="apply_patch",
                operation="edit_file", mutating=True, reversible=True,
                external=False,
            ),
            cfw_action_request={"tool_binding_id": "apply_patch",
                                "arguments": {"path": "view.py"}},
        )
        assert result["status"] == "ready"
        assert first._action_index == -1
        assert first._cfw_projection_action_index == 1
        assert cfw.applied_behavior_projection["host_action_index"] == 1

        await client.close()  # Simulate disconnect before POST_ACTION.
        resumed = _bridge(tmp_path, identity=identity, goal_set=goals,
                          cfw_port=cfw, csw_port=csw, client=client)
        try:
            state = await resumed.open()
            assert state["status"] == "ready"
            assert resumed._state.revision == 1
            assert resumed._action_index == -1
            assert resumed._cfw_projection_action_index == 1
            expected = build_cfw_behavior_projection(
                resumed._state, host_action_index=1).to_dict()
            assert cfw.applied_behavior_projection == expected
        finally:
            await resumed.close()
            await client.close()

    asyncio.run(run())


def test_restart_reconciles_applied_prepare_before_mcp_commit(tmp_path):
    async def run():
        identity = _identity("restart-task")
        goals = _goal_set()
        cfw = _CFWPort()
        csw = _CSWPort()
        client = _in_process_client(tmp_path, close_service=False)
        first = _bridge(tmp_path, identity=identity, goal_set=goals,
                        cfw_port=cfw, csw_port=csw, client=client)
        await first.open()
        observation = RuntimeObservation(
            action_index=1, equivalent_retry_count=2,
            criterion_status={"criterion-visual": "unmet"},
            goal_set=goals)
        snapshot, _ = first._csw_snapshot()
        request = ControlTickRequest(
            control_session_id=identity.scope,
            tick_id="crash-after-apply",
            expected_behavior_revision=first.behavior_state.revision,
            cfw_lifecycle_revision=first._current_cfw_revision(),
            observation=observation, goal_set=goals,
            cfw_signals=first._cfw_signals(observation),
            csw_snapshot=snapshot,
        )
        prepared = await first.client.prepare_control_tick(request.to_dict())
        first.actuator.apply(prepared)
        await first.client.close()

        resumed = _bridge(tmp_path, identity=identity, goal_set=goals,
                          cfw_port=cfw, csw_port=csw, client=client)
        try:
            state = await resumed.open()
            assert state["status"] == "ready"
            assert state["behavior_revision"] == 1
            assert state["behavior_state"]["active_stateworks"] == ["repair"]
            assert csw.active == ("repair",)
        finally:
            await resumed.close()
            await client.close()

    asyncio.run(run())


def test_csw_changes_require_checkpoint_capability_before_any_actuation(tmp_path):
    async def run():
        identity = _identity("csw-checkpoint-required")
        goals = _goal_set()
        cfw = _CFWPort()
        csw = _CSWPort()
        client = _in_process_client(tmp_path, close_service=False)
        bridge = _bridge(tmp_path, identity=identity, goal_set=goals,
                         cfw_port=cfw, csw_port=csw, client=client)
        await bridge.open()
        initial_cfw_projection = cfw.applied_behavior_projection
        observation = RuntimeObservation(
            action_index=1, equivalent_retry_count=2,
            criterion_status={"criterion-visual": "unmet"}, goal_set=goals)
        snapshot, _ = bridge._csw_snapshot()
        request = ControlTickRequest(
            control_session_id=identity.scope,
            tick_id="csw-checkpoint-required",
            expected_behavior_revision=bridge.behavior_state.revision,
            cfw_lifecycle_revision=bridge._current_cfw_revision(),
            observation=observation, goal_set=goals,
            cfw_signals=bridge._cfw_signals(observation),
            csw_snapshot=snapshot,
        )
        prepared = await client.prepare_control_tick(request.to_dict())
        assert prepared["csw_directives"]

        # Simulate an older or incomplete CSW adapter. The actuator must reject
        # before either CFW projection or CSW selection changes.
        csw.checkpoint_selection = None
        with pytest.raises(RuntimeError, match="require durable selection checkpoints"):
            bridge.actuator.apply(prepared)

        assert cfw.applied_behavior_projection == initial_cfw_projection
        assert csw.active == ()
        assert csw.revision == 0
        assert not csw._selection_checkpoints
        current_snapshot, _ = bridge._csw_snapshot()
        await client.abort_control_tick(ControlAbortRequest(
            control_session_id=identity.scope,
            tick_id=str(prepared["tick_id"]),
            decision_id=str(prepared["decision_id"]),
            basis_digest=str(prepared["basis_digest"]),
            applied_directive_ids=(), rollback_completed=True,
            failure_class="missing_csw_checkpoint_capability",
            cfw_lifecycle_revision=bridge._current_cfw_revision(),
            csw_snapshot=current_snapshot,
        ).to_dict())
        await bridge.close()
        await client.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "crash_point",
    ("after_prepare", "after_cfw_apply", "after_csw_apply",
     "after_both_apply", "after_dp_commit_before_host_receipt"),
)
def test_control_transaction_crash_matrix_reconciles_deterministically(
        tmp_path, crash_point):
    async def run():
        identity = _identity(f"crash-matrix-{crash_point}")
        goals = _goal_set()
        cfw = _CFWPort()
        csw = _CSWPort()
        client = _in_process_client(tmp_path, close_service=False)
        first = _bridge(tmp_path, identity=identity, goal_set=goals,
                        cfw_port=cfw, csw_port=csw, client=client)
        await first.open()
        observation = RuntimeObservation(
            action_index=1, equivalent_retry_count=2,
            criterion_status={"criterion-visual": "unmet"}, goal_set=goals)
        snapshot, _ = first._csw_snapshot()
        request = ControlTickRequest(
            control_session_id=identity.scope,
            tick_id=f"crash-{crash_point}",
            expected_behavior_revision=first.behavior_state.revision,
            cfw_lifecycle_revision=first._current_cfw_revision(),
            observation=observation, goal_set=goals,
            cfw_signals=first._cfw_signals(observation),
            csw_snapshot=snapshot,
        )
        prepared = await client.prepare_control_tick(request.to_dict())

        if crash_point == "after_cfw_apply":
            cfw_directives = first.actuator._directives(prepared["cfw_directives"])
            cfw_prepared = cfw.prepare_behavior_directives(cfw_directives)
            basis = dict(prepared["basis"])
            projection = build_cfw_behavior_projection(
                BehaviorState.from_mapping(prepared["candidate_behavior_state"]),
                host_action_index=int(basis.get(
                    "cfw_projection_action_index", basis["host_action_index"])),
            ).to_dict()
            cfw.apply_behavior_projection(cfw_prepared, projection)
        elif crash_point == "after_csw_apply":
            checkpoint_snapshot, candidates = first._csw_snapshot()
            csw.checkpoint_selection(
                tick_id=str(prepared["tick_id"]),
                expected_selection_revision=checkpoint_snapshot.revision)
            for directive in first.actuator._directives(prepared["csw_directives"]):
                csw.activate_statework(
                    directive["target"],
                    expected_selection_revision=checkpoint_snapshot.revision,
                    candidate_statework_ids=candidates,
                    intervention_id=directive["intervention_id"],
                    expires_at_action=first.actuator._directive_expiry(directive),
                )
                checkpoint_snapshot = csw.behavioral_snapshot(
                    candidate_statework_ids=candidates)
        elif crash_point == "after_both_apply":
            cfw_directives = first.actuator._directives(prepared["cfw_directives"])
            cfw_prepared = cfw.prepare_behavior_directives(cfw_directives)
            basis = dict(prepared["basis"])
            projection = build_cfw_behavior_projection(
                BehaviorState.from_mapping(prepared["candidate_behavior_state"]),
                host_action_index=int(basis.get(
                    "cfw_projection_action_index", basis["host_action_index"])),
            ).to_dict()
            cfw.apply_behavior_projection(cfw_prepared, projection)

            checkpoint_snapshot, candidates = first._csw_snapshot()
            csw.checkpoint_selection(
                tick_id=str(prepared["tick_id"]),
                expected_selection_revision=checkpoint_snapshot.revision)
            for directive in first.actuator._directives(prepared["csw_directives"]):
                csw.activate_statework(
                    directive["target"],
                    expected_selection_revision=checkpoint_snapshot.revision,
                    candidate_statework_ids=candidates,
                    intervention_id=directive["intervention_id"],
                    expires_at_action=first.actuator._directive_expiry(directive),
                )
                checkpoint_snapshot = csw.behavioral_snapshot(
                    candidate_statework_ids=candidates)
        elif crash_point == "after_dp_commit_before_host_receipt":
            application = first.actuator.apply(prepared)
            commit = ControlCommitRequest(
                control_session_id=identity.scope,
                tick_id=str(prepared["tick_id"]),
                decision_id=str(prepared["decision_id"]),
                basis_digest=str(prepared["basis_digest"]),
                applied_directive_ids=application.applied_directive_ids,
                cfw_lifecycle_revision=application.cfw_lifecycle_revision,
                csw_snapshot=application.csw_snapshot,
            )
            await client.commit_control_tick(commit.to_dict())

        await client.close()
        resumed = _bridge(tmp_path, identity=identity, goal_set=goals,
                          cfw_port=cfw, csw_port=csw, client=client)
        try:
            state = await resumed.open()
            committed = crash_point in {
                "after_both_apply", "after_dp_commit_before_host_receipt"}
            assert state["status"] == "ready"
            assert state["behavior_revision"] == (1 if committed else 0)
            assert csw.active == (("repair",) if committed else ())
            assert not csw._selection_checkpoints
            actual_projection = cfw.applied_behavior_projection
            if actual_projection is not None:
                expected_projection = build_cfw_behavior_projection(
                    resumed.behavior_state,
                    host_action_index=resumed._cfw_projection_action_index,
                ).to_dict()
                assert actual_projection == expected_projection
        finally:
            await resumed.close()
            await client.close()

    asyncio.run(run())


@pytest.mark.skipif(not _MCP_STDIO_AVAILABLE,
                    reason="MCP stdio transport dependencies are unavailable")
@pytest.mark.parametrize(
    "crash_point",
    ("after_prepare", "after_cfw_apply", "after_csw_apply",
     "after_both_apply", "after_dp_commit_before_host_receipt"),
)
def test_private_stdio_crash_matrix_restarts_process_and_reconciles(
        tmp_path, monkeypatch, crash_point):
    async def run():
        processes = _capture_private_mcp_processes(monkeypatch)
        identity = _identity(f"private-crash-{crash_point}")
        goals = _goal_set()
        cfw = _CFWPort()
        csw = _CSWPort()
        client = _client(tmp_path)
        first = _bridge(tmp_path, identity=identity, goal_set=goals,
                        cfw_port=cfw, csw_port=csw, client=client)
        await first.open()
        observation = RuntimeObservation(
            action_index=1, action_completed=True, equivalent_retry_count=2,
            criterion_status={"criterion-visual": "unmet"}, goal_set=goals)
        snapshot, _ = first._csw_snapshot()
        request = ControlTickRequest(
            control_session_id=identity.scope,
            tick_id=f"private-crash-{crash_point}",
            expected_behavior_revision=first.behavior_state.revision,
            cfw_lifecycle_revision=first._current_cfw_revision(),
            observation=observation, goal_set=goals,
            cfw_signals=first._cfw_signals(observation),
            csw_snapshot=snapshot,
        )
        prepared = await client.prepare_control_tick(request.to_dict())
        assert prepared["status"] == "prepared"

        if crash_point in {"after_cfw_apply", "after_both_apply"}:
            cfw_directives = first.actuator._directives(prepared["cfw_directives"])
            cfw_prepared = cfw.prepare_behavior_directives(cfw_directives)
            basis = dict(prepared["basis"])
            projection = build_cfw_behavior_projection(
                BehaviorState.from_mapping(prepared["candidate_behavior_state"]),
                host_action_index=int(basis.get(
                    "cfw_projection_action_index", basis["host_action_index"])),
            ).to_dict()
            cfw.apply_behavior_projection(cfw_prepared, projection)

        if crash_point in {"after_csw_apply", "after_both_apply"}:
            checkpoint_snapshot, candidates = first._csw_snapshot()
            csw.checkpoint_selection(
                tick_id=str(prepared["tick_id"]),
                expected_selection_revision=checkpoint_snapshot.revision)
            for directive in first.actuator._directives(prepared["csw_directives"]):
                csw.activate_statework(
                    directive["target"],
                    expected_selection_revision=checkpoint_snapshot.revision,
                    candidate_statework_ids=candidates,
                    intervention_id=directive["intervention_id"],
                    expires_at_action=first.actuator._directive_expiry(directive),
                )
                checkpoint_snapshot = csw.behavioral_snapshot(
                    candidate_statework_ids=candidates)

        if crash_point == "after_both_apply":
            # Confirm the individually applied authority state equals the normal
            # actuator's prepared result before simulating loss of the host.
            assert cfw.lifecycle_revision == int(
                prepared["candidate_behavior_state"]["revision"])
            expected_local_active = set(snapshot.active_stateworks)
            for directive in first.actuator._directives(prepared["csw_directives"]):
                if directive["operation"] == "prefer_statework":
                    expected_local_active.add(directive["target"])
                elif directive["operation"] == "release_statework":
                    expected_local_active.discard(directive["target"])
            assert csw.active == tuple(sorted(expected_local_active))

        if crash_point == "after_dp_commit_before_host_receipt":
            application = first.actuator.apply(prepared)
            commit = ControlCommitRequest(
                control_session_id=identity.scope,
                tick_id=str(prepared["tick_id"]),
                decision_id=str(prepared["decision_id"]),
                basis_digest=str(prepared["basis_digest"]),
                applied_directive_ids=application.applied_directive_ids,
                cfw_lifecycle_revision=application.cfw_lifecycle_revision,
                csw_snapshot=application.csw_snapshot,
            )
            committed = await client.commit_control_tick(commit.to_dict())
            assert committed["status"] == "committed"

        await _kill_private_mcp_child(processes)
        # The crash matrix discards the old host transport before reopening the
        # same persistent DP store through a new MCP client process.
        await client.close()
        resumed_client = _client(tmp_path)
        resumed = _bridge(tmp_path, identity=identity, goal_set=goals,
                          cfw_port=cfw, csw_port=csw, client=resumed_client)
        try:
            state = await resumed.open()
            assert len(processes) == 2, "reconciliation did not start a new DP MCP child"
            assert processes[1].returncode is None
            should_commit = crash_point in {
                "after_both_apply", "after_dp_commit_before_host_receipt"}
            expected_revision = 1 if should_commit else 0
            expected_active = ("repair",) if should_commit else ()
            assert state["status"] == "ready"
            assert state["behavior_revision"] == expected_revision
            assert state["behavior_state"]["active_stateworks"] == list(expected_active)
            assert csw.active == expected_active
            assert not csw._selection_checkpoints
            reconciled = await resumed_client.reconcile_control_session({
                "protocol_version": PROTOCOL_VERSION,
                "control_session_id": identity.scope,
                "cfw_lifecycle_revision": resumed._current_cfw_revision(),
                "csw_snapshot": resumed._csw_snapshot()[0].to_dict(),
            })
            latest = reconciled["latest_control_tick"]
            assert latest["status"] == ("committed" if should_commit else "aborted")
            expected_projection = build_cfw_behavior_projection(
                resumed.behavior_state,
                host_action_index=resumed._cfw_projection_action_index,
            ).to_dict()
            assert cfw.applied_behavior_projection == expected_projection
        finally:
            await resumed.close()

    asyncio.run(run())


@pytest.mark.skipif(not _MCP_STDIO_AVAILABLE,
                    reason="MCP stdio transport dependencies are unavailable")
def test_private_stdio_dp_outage_expires_local_leases_and_reconciles(
        tmp_path, monkeypatch):
    async def run():
        processes = _capture_private_mcp_processes(monkeypatch)
        identity = _identity("private-dp-outage")
        goals = _goal_set()
        cfw = _CFWPort(preflight_decision="block", final_decision="block")
        csw = _CSWPort()
        metrics = ProductionMetrics(tmp_path / "private-outage-metrics.sqlite3")
        client = _client(tmp_path)
        bridge = HostControlBridge(
            identity=identity,
            objective="Render the requested page",
            objective_criteria=("criterion-visual",),
            initial_goal_set=goals,
            cfw=CFWRuntimeAdapter(cfw),
            csw=csw,
            client=client,
            production_metrics=metrics,
        )
        await bridge.open()
        committed = await bridge.after_action(RuntimeObservation(
            action_index=1,
            action_completed=True,
            equivalent_retry_count=2,
            criterion_status={"criterion-visual": "unmet"},
            goal_set=goals,
        ), goal_set=goals)
        assert committed["status"] == "ready"
        assert committed["behavior_revision"] == 1
        assert csw.active == ("repair",)
        committed_projection = cfw.applied_behavior_projection
        assert committed_projection is not None
        assert committed_projection["framework_modes"]["ward"] == "mandatory"

        repair_expiry = csw.lease_expiries["repair"]
        outage_action_index = max(2, repair_expiry)
        cfw.local_fuse_mode = "constrained"
        cfw.local_fuse_expiry = outage_action_index - 1

        await _kill_private_mcp_child(processes)
        initial_offline_context = await bridge.before_model_call(
            "Continue the visual repair safely.")
        assert len(processes) == 1, "offline host call unexpectedly restarted DP"
        assert "CURRENT BEHAVIOR GUIDANCE" in initial_offline_context
        assert "Continue the visual repair safely." in initial_offline_context
        assert bridge.behavior_state.revision == 1
        assert cfw.applied_behavior_projection == committed_projection

        degraded = await bridge.after_action(RuntimeObservation(
            action_index=outage_action_index,
            action_completed=True,
            equivalent_retry_count=2,
            criterion_status={"criterion-visual": "unmet"},
            goal_set=goals,
        ), goal_set=goals)
        assert degraded["status"] == "degraded"
        assert bridge.behavior_state.revision == 1
        assert cfw.local_fuse_mode == "normal"
        assert cfw.local_fuse_expiry is None
        assert csw.active == ()
        assert csw.lease_expiries == {}
        assert cfw.applied_behavior_projection == committed_projection
        assert cfw.effective_behavior_projection["framework_modes"]["ward"] == "mandatory"

        # The local authorities still decide legality with DP unavailable.
        action = {"tool_binding_id": "apply_patch", "arguments": {"path": "view.py"}}
        assert cfw.preflight_action(action)["decision"] == "block"
        assert cfw.authorize_action(action)["decision"] == "block"
        snapshot = csw.behavioral_snapshot(
            candidate_statework_ids=csw.available_statework_ids())
        assert tuple(item.statework_id for item in snapshot.eligible_stateworks) == ("repair",)
        with pytest.raises(ValueError, match="unbound StateWork"):
            csw.activate_statework(
                "repair", expected_selection_revision=snapshot.revision,
                candidate_statework_ids=("untrusted",),
                intervention_id="offline-illegal-request",
                expires_at_action=outage_action_index + 1)
        offline_context = await bridge.before_model_call("DP remains offline.")
        assert "CURRENT BEHAVIOR GUIDANCE" in offline_context
        assert "DP remains offline." in offline_context

        await bridge.close()
        resumed_client = _client(tmp_path)
        resumed = _bridge(tmp_path, identity=identity, goal_set=goals,
                          cfw_port=cfw, csw_port=csw, client=resumed_client)
        try:
            recovered = await resumed.open()
            assert len(processes) == 2, "reconnect did not start a new DP MCP child"
            assert processes[1].returncode is None
            assert recovered["status"] == "ready"
            assert recovered["behavior_revision"] == 1
            assert recovered["behavior_state"]["active_stateworks"] == []
            assert csw.active == ()
            assert resumed._needs_reconciliation is False
            assert cfw.applied_behavior_projection == build_cfw_behavior_projection(
                resumed.behavior_state,
                host_action_index=resumed._cfw_projection_action_index,
            ).to_dict()
        finally:
            await resumed.close()

    asyncio.run(run())
