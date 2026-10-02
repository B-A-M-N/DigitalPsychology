from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys

from lib.behavior_state import (CSWBehaviorSnapshot, GoalItemSnapshot,
                                GoalSetSnapshot, StateWorkOption)
from lib.control_protocol import ControlTickRequest
from lib.host import HostControlBridge, MCPControlClient, MCPControlUnavailable
from lib.runtime import CFWRuntimeAdapter, RuntimeIdentity, RuntimeObservation


ROOT = Path(__file__).resolve().parents[1]


class _CFWPort:
    def __init__(self) -> None:
        self.signals = [{
            "source": "owl", "code": "owl.approach_failed", "active": True,
            "severity": "warning", "criterion_id": "criterion-visual",
            "evidence_refs": [],
        }]
        self.controls: dict[str, dict] = {}
        self._state = None

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
                           candidate_statework_ids):
        if expected_selection_revision != self.revision:
            raise RuntimeError("stale CSW selection revision")
        if statework_id not in candidate_statework_ids:
            raise ValueError("unbound StateWork")
        self.active = tuple(sorted(set(self.active) | {statework_id}))
        self.revision += 1
        return self.behavioral_snapshot(candidate_statework_ids=candidate_statework_ids)

    def deactivate_statework(self, statework_id, *, expected_selection_revision,
                             candidate_statework_ids):
        if expected_selection_revision != self.revision:
            raise RuntimeError("stale CSW selection revision")
        self.active = tuple(item for item in self.active if item != statework_id)
        self.revision += 1
        return self.behavioral_snapshot(candidate_statework_ids=candidate_statework_ids)


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


def _client(tmp_path):
    env = dict(os.environ)
    env.update({
        "DIGITALPSYCHOLOGY_STATE_ROOT": str(tmp_path / "dp-state"),
        "DIGITALPSYCHOLOGY_EVENT_DB": str(tmp_path / "events.sqlite3"),
        "DIGITALPSYCHOLOGY_BEHAVIOR_STATE_DB": str(tmp_path / "behavior.sqlite3"),
        "DIGITALPSYCHOLOGY_SYSTEM_ONE_MODE": "disabled",
        "DIGITALPSYCHOLOGY_SEMANTIC_PROVIDER": "disabled",
    })
    return MCPControlClient(command=sys.executable, env=env, cwd=ROOT,
                            timeout_seconds=20)


def _bridge(tmp_path, *, identity, goal_set, cfw_port, csw_port):
    return HostControlBridge(
        identity=identity,
        objective="Render the requested page",
        objective_criteria=("criterion-visual",),
        initial_goal_set=goal_set,
        cfw=CFWRuntimeAdapter(cfw_port),
        csw=csw_port,
        client=_client(tmp_path),
    )


class _UnavailableControlClient:
    async def start(self):
        raise MCPControlUnavailable("server unavailable")

    async def close(self):
        return None


def test_dp_mcp_outage_keeps_host_runtime_usable():
    async def run():
        cfw = _CFWPort()
        bridge = HostControlBridge(
            identity=_identity("offline-task"),
            objective="Render the requested page",
            objective_criteria=("criterion-visual",),
            initial_goal_set=_goal_set(), cfw=CFWRuntimeAdapter(cfw),
            client=_UnavailableControlClient(),
        )
        opened = await bridge.open()
        assert opened["status"] == "degraded"
        context = await bridge.before_model_call("host context")
        assert "host context" in context
        result = await bridge.after_action(RuntimeObservation(
            action_index=1, equivalent_retry_count=2,
            criterion_status={"criterion-visual": "unmet"},
        ))
        assert result["status"] == "degraded"
        assert cfw.applied_behavior_state is None
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
            assert "DIGITAL PSYCHOLOGY BEHAVIOR STATE" in context
            assert "base host context" in context
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
