from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys

from lib.behavior_state import (CSWBehaviorSnapshot, GoalItemSnapshot,
                                GoalSetSnapshot, StateWorkOption)
from lib.control_protocol import (PROTOCOL_VERSION, ControlAbortRequest,
                                  ControlTickRequest)
from lib.host import HostControlBridge, MCPControlClient, MCPControlUnavailable
from lib.runtime import (ActionCandidate, CFWRuntimeAdapter, RuntimeIdentity,
                         RuntimeObservation)


ROOT = Path(__file__).resolve().parents[1]


class _CFWPort:
    def __init__(self, *, preflight_decision="allow", final_decision="allow") -> None:
        self.signals = [{
            "source": "owl", "code": "owl.approach_failed", "active": True,
            "severity": "warning", "criterion_id": "criterion-visual",
            "evidence_refs": [],
        }]
        self.controls: dict[str, dict] = {}
        self._state = None
        self.preflight_decision = preflight_decision
        self.final_decision = final_decision
        self.action_gate_calls = []

    @staticmethod
    def _action_digest(action):
        body = json.dumps(dict(action), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(body.encode()).hexdigest()

    def preflight_action(self, action):
        self.action_gate_calls.append("preflight")
        return {"action_digest": self._action_digest(action),
                "decision": self.preflight_decision}

    def authorize_action(self, action):
        self.action_gate_calls.append("authorize")
        return {"action_digest": self._action_digest(action),
                "action_request_id": "action-request-host-1",
                "decision": self.final_decision}

    def available_component_ids(self):
        return ("owl", "anchor", "fuse", "ward", "sispis")

    def collect_behavior_signals(self, _observation):
        return tuple(self.signals)

    @property
    def lifecycle_revision(self):
        return int(self._state["revision"]) if self._state is not None else -1

    @property
    def applied_behavior_state(self):
        return dict(self._state) if self._state is not None else None

    def prepare_behavior_directives(self, directives):
        return (dict(self.controls), dict(self._state) if self._state else None,
                tuple(dict(item) for item in directives))

    def apply_behavior_directives(self, prepared, behavior_state):
        _controls, _prior, directives = prepared
        self._state = dict(behavior_state)
        self.controls = {**self.controls,
                         **{item["directive_id"]: item for item in directives}}
        return tuple(item["directive_id"] for item in directives)

    def synchronize_behavior_state(self, behavior_state):
        self._state = dict(behavior_state)

    def rollback_behavior_directives(self, prepared):
        controls, prior, _directives = prepared
        self.controls = dict(controls)
        self._state = dict(prior) if prior is not None else None

    def restore_behavior_state(self, behavior_state, *, expected_current_revision):
        if self.lifecycle_revision != expected_current_revision:
            raise RuntimeError("stale CFW lifecycle revision")
        self._state = dict(behavior_state)

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
        self.options = (StateWorkOption(
            "repair", behavior_tags=("recovery",),
            purpose="Rebuild a failed approach from evidence."),)

    def available_statework_ids(self):
        return ("repair",)

    def authority_identity(self):
        return {"adapter_kind": "test-csw", "scope_hash": "b" * 64,
                "capability_hash": "c" * 64}

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
        criterion_id="criterion-visual", status="READY"),))


class _CapturingControlClient(MCPControlClient):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.prepared_requests = []

    async def prepare_control_tick(self, request):
        self.prepared_requests.append(dict(request))
        return await super().prepare_control_tick(request)


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


def _bridge(tmp_path, *, identity, goal_set, cfw_port, csw_port, client=None):
    return HostControlBridge(
        identity=identity,
        objective="Render the requested page",
        objective_criteria=("criterion-visual",),
        initial_goal_set=goal_set,
        cfw=CFWRuntimeAdapter(cfw_port),
        csw=csw_port,
        client=client or _client(tmp_path),
    )


class _UnavailableControlClient:
    async def start(self):
        raise MCPControlUnavailable("server unavailable")

    async def close(self):
        return None


def test_dp_mcp_outage_keeps_host_runtime_usable():
    async def run():
        cfw = _CFWPort()
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
        assert cfw.applied_behavior_state is None
        assert csw.expiration_calls == [1]
        assert csw.active == ()
        await bridge.close()

    asyncio.run(run())


def test_private_stdio_mcp_applies_cfw_and_csw_then_commits(tmp_path):
    async def run():
        cfw = _CFWPort()
        csw = _CSWPort()
        goals = _goal_set()
        bridge = _bridge(tmp_path, identity=_identity(), goal_set=goals,
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
            assert cfw.applied_behavior_state["revision"] == 1
            assert cfw.controls
            context = await bridge.before_model_call("base host context")
            assert "CURRENT BEHAVIOR GUIDANCE" in context
            assert "base host context" in context
        finally:
            await bridge.close()

    asyncio.run(run())


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


def test_system_one_receipt_stays_in_host_audit_not_model_context():
    async def run():
        bridge = HostControlBridge(
            identity=_identity("system-one-audit"),
            objective="Render the requested page",
            objective_criteria=("criterion-visual",),
            initial_goal_set=_goal_set(), cfw=CFWRuntimeAdapter(_CFWPort()),
            csw=_CSWPort(), client=_UnavailableControlClient(),
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
                         goal_set=goals, cfw_port=cfw, csw_port=_CSWPort())
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
        client = _client(tmp_path, client_type=_CapturingControlClient)
        bridge = _bridge(tmp_path, identity=_identity("rejected-candidate-task"),
                         goal_set=goals, cfw_port=cfw, csw_port=_CSWPort(),
                         client=client)
        try:
            await bridge.open()
            result = await bridge.before_action_candidate(
                ActionCandidate(
                    action_class="external_write",
                    proposed_purpose="Apply the requested change",
                    affected_criterion_ids=("criterion-visual",),
                    action_id="action-needs-confirmation", tool_name="deploy",
                    operation="publish", mutating=True, reversible=False,
                    external=True,
                ),
                cfw_action_request={
                    "tool_binding_id": "deploy", "arguments": {"target": "prod"}},
            )
            assert result["status"] == "rejected"
            assert result["stage"] == "cfw_preflight"
            assert result["action_authorized"] is False
            assert cfw.action_gate_calls == ["preflight"]
            assert bridge._action_index == -1
            assert bridge._boundary_sequence == 0
            request = client.prepared_requests[-1]
            assert request["boundary_kind"] == "pre_action_rejected"
            assert request["observation"]["candidate_preflight_result"] == "require_confirmation"
            assert request["observation"]["candidate_action"]["action_id"] == \
                "action-needs-confirmation"
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
        bridge = _bridge(tmp_path, identity=_identity("final-rejection-task"),
                         goal_set=goals, cfw_port=cfw, csw_port=_CSWPort())
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
        finally:
            await bridge.close()

    asyncio.run(run())


def test_restart_reconciles_applied_prepare_before_mcp_commit(tmp_path):
    async def run():
        identity = _identity("restart-task")
        goals = _goal_set()
        cfw = _CFWPort()
        csw = _CSWPort()
        first = _bridge(tmp_path, identity=identity, goal_set=goals,
                        cfw_port=cfw, csw_port=csw)
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
                          cfw_port=cfw, csw_port=csw)
        try:
            state = await resumed.open()
            assert state["status"] == "ready"
            assert state["behavior_revision"] == 1
            assert state["behavior_state"]["active_stateworks"] == ["repair"]
            assert csw.active == ("repair",)
        finally:
            await resumed.close()

    asyncio.run(run())
