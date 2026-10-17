from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

try:
    from tests.test_control_mcp_host_bridge import _CFWPort, _CSWPort, _identity, _goal_set
except Exception:
    pytest.skip("host bridge fixture unavailable", allow_module_level=True)

from lib.control.service import ControlPlaneService
from lib.control_protocol import ControlSessionDescriptor
from dataclasses import replace
from lib.control_protocol import ControlTickRequest
from lib.host import HostControlBridge, InProcessControlClient
from lib.runtime import CFWRuntimeAdapter
from lib.protocol_artifact import (PROTOCOL_HASH, PROTOCOL_VERSION,
                                   QUESTION_REGISTRY,
                                   QUESTION_REGISTRY_HASH,
                                   STATE_PROJECTION_SCHEMA_HASH)
from lib.runtime.store import BehaviorStateStore
from lib.service import DigitalPsychologyService
from lib.system_one_backends import DisabledSystemOneBackend
from lib.system_one_contracts import SystemOneCapability, SystemOneFrame
from lib.system_one_service import SystemOneService
from lib.system_one_cli import load_authenticated_system_one_events


class _PortfolioBackend:
    def __init__(self, backend_id, model_id, revision, provider_hash):
        self.backend_id = backend_id
        self.model_id = model_id
        self.model_revision = revision
        self.model_content_hash = "c" * 64
        self.provider_protocol_hash = provider_hash
        self.max_latency_ms = 1000
        self.calls = []

    def capability(self):
        return SystemOneCapability(
            backend_id=self.backend_id, model_id=self.model_id,
            model_revision=self.model_revision,
            model_content_hash=self.model_content_hash,
            provider_protocol_hash=self.provider_protocol_hash,
            protocol_version=PROTOCOL_VERSION, protocol_hash=PROTOCOL_HASH,
            question_registry_hash=QUESTION_REGISTRY_HASH,
            state_projection_schema_hash=STATE_PROJECTION_SCHEMA_HASH,
            supported_question_families=tuple(QUESTION_REGISTRY),
            max_state_bytes=32768, stateful_sessions=False,
            max_concurrent_requests=2)

    def judge(self, frame):
        self.calls.append(frame)
        return self._judgment(frame)

    def _judgment(self, frame):
        from lib.system_one_contracts import QuestionAnswer, QuestionProbability, SystemOneJudgment
        answers = []
        for spec in frame.questions:
            labels = tuple(label for label, _ in spec.criteria)
            probabilities = tuple(QuestionProbability(
                label, 0.7 if index == 0 else 0.3 / (len(labels) - 1))
                for index, label in enumerate(labels))
            answers.append(QuestionAnswer(
                question_id=spec.question_id, selected=labels[0],
                raw_probabilities=probabilities))
        return SystemOneJudgment(
            identity=frame.identity, request_id=frame.request_id,
            sequence=frame.sequence, state_digest=frame.state_digest,
            question_bundle_digest=frame.question_bundle_digest,
            request_digest=frame.request_digest, policy_hash=frame.policy_hash,
            backend_id=frame.backend_id, model_id=frame.model_id,
            model_revision=frame.model_revision,
            model_content_hash=frame.model_content_hash,
            provider_protocol_hash=frame.provider_protocol_hash,
            calibration_bundle_hash=frame.calibration_bundle_hash,
            answers=tuple(answers), protocol_version=frame.protocol_version,
            protocol_hash=frame.protocol_hash)

    def reset_identity(self, _identity):
        return None

    def resync(self, _frame):
        return None

    def close(self):
        self.closed = True


def _portfolio_service(tmp_path):
    laya = _PortfolioBackend("digitalpsychology-laya-native", "fixture-laya",
                             "laya-rev", "a" * 64)
    jev = _PortfolioBackend("digitalpsychology-jev-openrouter",
                            "typesafe/jev-1.14", "jev-1.14", "b" * 64)
    service = SystemOneService(DisabledSystemOneBackend(), mode="shadow")
    from lib.system_one_runtime import SystemOneRuntime
    from lib.system_one_portfolio import SystemOnePortfolio
    enabled = tuple(QUESTION_REGISTRY)
    runtimes = [SystemOneRuntime(backend, enabled_question_families=enabled)
                for backend in (laya, jev)]
    service.portfolio = SystemOnePortfolio(runtimes)
    service.runtimes = tuple(runtimes)
    service.runtime = runtimes[0]
    service.mode = "shadow"
    service.enabled_question_families = enabled
    assert service.is_portfolio
    return service, laya, jev



def test_shadow_portfolio_records_both_but_selects_none(tmp_path):
    service, laya, jev = _portfolio_service(tmp_path)
    dp = DigitalPsychologyService(event_db=tmp_path / "events.sqlite3",
                                  system_one_service=service)
    control = ControlPlaneService(decision_service=dp,
                                  state_store=BehaviorStateStore(tmp_path / "behavior.sqlite3"))
    identity = _identity("shadow-portfolio")
    goals = _goal_set()
    cfw = _CFWPort()
    csw = _CSWPort()
    csw.active = ("repair",)
    # No semantic_remote_inference in policy context => shadow only.
    bridge = HostControlBridge(
        identity=identity, objective="Render the requested page",
        objective_criteria=("criterion-visual",), initial_goal_set=goals,
        cfw=CFWRuntimeAdapter(cfw), csw=csw,
        client=InProcessControlClient(control, close_service=False),
        host_policy_context={"deliverable_kind": "visual_ui"})
    async def run():
        try:
            await bridge.open()
            from dataclasses import replace
            from lib.control_protocol import ControlTickRequest
            prepared_request = ControlTickRequest(
                control_session_id=identity.scope, tick_id="shadow-tick",
                expected_behavior_revision=control.state_store.load(
                    identity.scope).state.revision,
                cfw_lifecycle_revision=bridge._current_cfw_revision(),
                observation=replace(
                    bridge._boundary_observation("post_action"),
                    equivalent_retry_count=1, action_completed=True, action_index=1),
                goal_set=goals, cfw_signals=bridge._cfw_signals(
                    bridge._boundary_observation("post_action")),
                csw_snapshot=csw.behavioral_snapshot(
                    candidate_statework_ids=csw.available_statework_ids()),
                boundary_kind="post_action",
                boundary_sequence=bridge._boundary_sequence + 1)
            prepared = control.prepare_control_tick(prepared_request.to_dict())
            assert prepared["status"] == "prepared"
            system_one = prepared["basis"].get("system_one") or {}
            result = system_one.get("result", {})
            assert result.get("portfolio") is True
            assert result.get("selected_provider_id") is None
            assert result.get("selection_reason") == "provider_not_selected"
            # Remote denied: Jev is filtered out; only Laya is dispatched.
            observations = result.get("observations", ())
            assert len(observations) == 1
            assert observations[0]["provider_id"] == "digitalpsychology-laya-native"
            assert observations[0]["result"]["status"] == "shadow"
            assert len(laya.calls) == 1
            assert len(jev.calls) == 0
            from tests.test_control_tick_noop import _commit_prepared
            committed = _commit_prepared(
                control, prepared, scope=identity.scope,
                csw_snapshot=csw.behavioral_snapshot(
                    candidate_statework_ids=csw.available_statework_ids()),
                cfw_lifecycle_revision=prepared["candidate_behavior_state"]["revision"])
            assert committed["status"] == "committed"
            return
        finally:
            await bridge.close()
    try:
        asyncio.run(run())
    finally:
        dp.close()


def test_remote_denied_jev_gets_zero_calls(tmp_path):
    service, laya, jev = _portfolio_service(tmp_path)
    # Track that Jev's transport never receives a call when remote is denied.
    jev_calls_before = len(jev.calls)
    dp = DigitalPsychologyService(event_db=tmp_path / "events.sqlite3",
                                  system_one_service=service)
    control = ControlPlaneService(decision_service=dp,
                                  state_store=BehaviorStateStore(tmp_path / "behavior.sqlite3"))
    identity = _identity("remote-denied")
    goals = _goal_set()
    cfw = _CFWPort()
    csw = _CSWPort()
    csw.active = ("repair",)
    # semantic_remote_inference absent => default deny.
    bridge = HostControlBridge(
        identity=identity, objective="Render the requested page",
        objective_criteria=("criterion-visual",), initial_goal_set=goals,
        cfw=CFWRuntimeAdapter(cfw), csw=csw,
        client=InProcessControlClient(control, close_service=False),
        host_policy_context={"deliverable_kind": "visual_ui"})
    async def run():
        try:
            await bridge.open()
            from dataclasses import replace
            from lib.control_protocol import ControlTickRequest
            prepared_request = ControlTickRequest(
                control_session_id=identity.scope, tick_id="deny-remote-tick",
                expected_behavior_revision=control.state_store.load(
                    identity.scope).state.revision,
                cfw_lifecycle_revision=bridge._current_cfw_revision(),
                observation=replace(
                    bridge._boundary_observation("post_action"),
                    equivalent_retry_count=1, action_completed=True, action_index=1),
                goal_set=goals, cfw_signals=bridge._cfw_signals(
                    bridge._boundary_observation("post_action")),
                csw_snapshot=csw.behavioral_snapshot(
                    candidate_statework_ids=csw.available_statework_ids()),
                boundary_kind="post_action",
                boundary_sequence=bridge._boundary_sequence + 1)
            prepared = control.prepare_control_tick(prepared_request.to_dict())
            assert prepared["status"] == "prepared"
            system_one = prepared["basis"].get("system_one") or {}
            result = system_one.get("result", {})
            assert result.get("portfolio") is True
            assert result.get("selected_provider_id") is None
            # Jev received zero calls because remote inference was denied.
            assert len(jev.calls) == jev_calls_before
            from tests.test_control_tick_noop import _commit_prepared
            committed = _commit_prepared(
                control, prepared, scope=identity.scope,
                csw_snapshot=csw.behavioral_snapshot(
                    candidate_statework_ids=csw.available_statework_ids()),
                cfw_lifecycle_revision=prepared["candidate_behavior_state"]["revision"])
            assert committed["status"] == "committed"
            return
        finally:
            await bridge.close()
    try:
        asyncio.run(run())
    finally:
        dp.close()

def test_host_committed_portfolio_tick_records_both_observations(tmp_path):
    import asyncio
    service, laya, jev = _portfolio_service(tmp_path)
    dp = DigitalPsychologyService(event_db=tmp_path / "events.sqlite3",
                                  system_one_service=service)
    control = ControlPlaneService(decision_service=dp,
                                  state_store=BehaviorStateStore(tmp_path / "behavior.sqlite3"))
    identity = _identity("host-portfolio")
    goals = _goal_set()
    cfw = _CFWPort()
    csw = _CSWPort()
    csw.active = ("repair",)
    bridge = HostControlBridge(
        identity=identity, objective="Render the requested page",
        objective_criteria=("criterion-visual",), initial_goal_set=goals,
        cfw=CFWRuntimeAdapter(cfw), csw=csw,
        client=InProcessControlClient(control, close_service=False),
        host_policy_context={"deliverable_kind": "visual_ui",
                             "semantic_remote_inference": True})
    descriptor = bridge._descriptor()
    async def run():
        try:
            await bridge.open()
            from dataclasses import replace
            from lib.control_protocol import ControlTickRequest
            prepared_request = ControlTickRequest(
                control_session_id=identity.scope, tick_id="portfolio-tick",
                expected_behavior_revision=control.state_store.load(
                    identity.scope).state.revision,
                cfw_lifecycle_revision=bridge._current_cfw_revision(),
                observation=replace(
                    bridge._boundary_observation("post_action"),
                    equivalent_retry_count=1, action_completed=True, action_index=1),
                goal_set=goals, cfw_signals=bridge._cfw_signals(
                    bridge._boundary_observation("post_action")),
                csw_snapshot=csw.behavioral_snapshot(
                    candidate_statework_ids=csw.available_statework_ids()),
                boundary_kind="post_action",
                boundary_sequence=bridge._boundary_sequence + 1)
            prepared = control.prepare_control_tick(prepared_request.to_dict())
            assert prepared["status"] == "prepared"
            system_one = prepared["basis"].get("system_one") or {}
            assert system_one.get("result", {}).get("portfolio") is True, (
                prepared["basis"].get("system_one"))
            from tests.test_control_tick_noop import _commit_prepared
            committed = _commit_prepared(
                control, prepared, scope=identity.scope,
                csw_snapshot=csw.behavioral_snapshot(
                    candidate_statework_ids=csw.available_statework_ids()),
                cfw_lifecycle_revision=prepared["candidate_behavior_state"]["revision"])
            assert committed["status"] == "committed"
            assert committed["system_one_decision_event_ids"]
            events = load_authenticated_system_one_events(tmp_path / "events.sqlite3")
            decisions = [event for event in events
                         if event.event_type == "system_one_decision"]
            assert len(decisions) == 2, decisions
            providers = {event.payload["system_one_backend_id"] for event in decisions}
            assert providers == {laya.backend_id, jev.backend_id}
            provider_calls = laya.calls + jev.calls
            assert len(provider_calls) == 2
            assert {frame.backend_id for frame in provider_calls} == {
                laya.backend_id, jev.backend_id}
            return
        finally:
            await bridge.close()
    try:
        asyncio.run(run())
    finally:
        dp.close()
