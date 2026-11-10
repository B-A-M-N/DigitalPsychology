"""Proposal pipeline: trusted host callback drains completed judgments once.

Covers the production call path (prepare -> capture -> drain -> receipts on
the service result) and the direct drain entry: exactly-once per request_id,
idempotent replay, visible failures with retry recovery, default-off no-op,
and malformed judgments never reaching the sink.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from lib.behavior_state import GoalItemSnapshot, GoalSetSnapshot
from lib.control.service import PROPOSAL_SEEN_REQUEST_BOUND, ControlPlaneService
from lib.control_protocol import ControlAbortRequest, ControlTickRequest
from lib.host import HostControlBridge, InProcessControlClient
from lib.runtime.contracts import RuntimeIdentity, RuntimeObservation
from lib.runtime.store import BehaviorStateStore
from lib.system_one_contracts import (QuestionAnswer, QuestionProbability,
                                       QuestionSpec, SystemOneFrame,
                                       SystemOneIdentity, SystemOneJudgment,
                                       canonical_hash)

from tests.test_system_one_runtime import frame as helper_frame


# ---------------------------------------------------------------------------
# Light judgment doubles for the direct drain entry.
# ---------------------------------------------------------------------------

class _Prob:
    def __init__(self, label, probability):
        self.label = label
        self.probability = probability


class _Answer:
    def __init__(self, selected, raw):
        self.selected = selected
        self.raw_probabilities = tuple(_Prob(label, value) for label, value in raw)
        self.calibrated_probabilities = None


class _Judgment:
    def __init__(self, answers, request_id="req-1"):
        self._answers = answers
        self.model_id = "typesafe/jev-1.14"
        self.model_revision = "jev-1.14"
        self.request_id = request_id

    def answer(self, question_id):
        return self._answers.get(question_id)


def _judgment(request_id="req-1"):
    return _Judgment({
        "test_strategy_preference": _Answer(
            "testworks", [("testworks", 0.8), ("godkiller", 0.2)]),
        "decomposition_preference": _Answer(
            "godkiller", [("godkiller", 0.6), ("testworks", 0.4)]),
    }, request_id=request_id)


def _light_service(tmp_path):
    store = BehaviorStateStore(tmp_path / "behavior.sqlite3")
    return ControlPlaneService(decision_service=SimpleNamespace(),
                               state_store=store)


def _register(service, submitted, *, fail=False):
    def submit(payload):
        if fail:
            raise RuntimeError("inbox unreachable")
        submitted.append(payload)
        return f"prop-{len(submitted)}"

    service.register_proposal_sink(
        submit=submit,
        subject_context=lambda judgment: {"subject_ref": "repo:x"})
    return submit


# ---------------------------------------------------------------------------
# Direct drain entry.
# ---------------------------------------------------------------------------

def test_sink_fires_once_per_completed_judgment(tmp_path):
    service = _light_service(tmp_path)
    submitted = []
    _register(service, submitted)

    receipts = service.drain_completed_judgment(_judgment())

    assert [item["ok"] for item in receipts] == [True, True]
    assert [item["proposal_id"] for item in receipts] == ["prop-1", "prop-2"]
    assert len(submitted) == 2
    assert all(item["subject_ref"] == "repo:x" for item in submitted)


def test_replay_of_same_request_id_is_idempotent(tmp_path):
    service = _light_service(tmp_path)
    submitted = []
    _register(service, submitted)

    first = service.drain_completed_judgment(_judgment("req-7"))
    # Same object, then a distinct object carrying the same request_id.
    second = service.drain_completed_judgment(_judgment("req-7"))
    assert second == first
    assert len(submitted) == 2


def test_failed_sink_visible_and_retry_recovers(tmp_path):
    service = _light_service(tmp_path)
    submitted = []
    calls = {"fail": True}

    def submit(payload):
        if calls["fail"]:
            raise RuntimeError("inbox unreachable")
        submitted.append(payload)
        return f"prop-{len(submitted)}"

    service.register_proposal_sink(
        submit=submit,
        subject_context=lambda judgment: {"subject_ref": "repo:x"})

    failed = service.drain_completed_judgment(_judgment("req-9"))
    assert len(failed) == 2
    assert all(item["ok"] is False for item in failed)
    assert all("RuntimeError" in item["error"] for item in failed)
    assert submitted == []

    calls["fail"] = False
    recovered = service.retry_proposal_drain("req-9")
    assert [item["ok"] for item in recovered] == [True, True]
    # The failed round submitted nothing, so the retry accepts each payload
    # exactly once: no duplicates.
    assert len(submitted) == 2
    assert [item["proposal_id"] for item in recovered] == ["prop-1", "prop-2"]

    # A now-healthy replay is served from the ledger without re-submitting.
    again = service.drain_completed_judgment(_judgment("req-9"))
    assert again == recovered
    assert len(submitted) == 2


def test_retry_unknown_request_id_fails_closed(tmp_path):
    service = _light_service(tmp_path)
    service.register_proposal_sink(
        submit=lambda payload: "prop-x",
        subject_context=lambda judgment: {"subject_ref": "repo:x"})
    with pytest.raises(ValueError):
        service.retry_proposal_drain("req-missing")


def test_no_sink_means_noop(tmp_path):
    service = _light_service(tmp_path)
    assert service.proposal_sink_registered is False
    assert service.drain_completed_judgment(_judgment()) == []
    # Even a malformed judgment is a no-op when the path is off.
    assert service.drain_completed_judgment(object()) == []
    assert service.retry_proposal_drain("req-1") == []


def test_malformed_judgment_never_reaches_sink(tmp_path):
    service = _light_service(tmp_path)
    submitted = []
    _register(service, submitted)

    with pytest.raises(ValueError):
        service.drain_completed_judgment(object())
    broken = _Judgment({}, request_id=None)
    with pytest.raises(ValueError):
        service.drain_completed_judgment(broken)

    class _Raising:
        model_id = "m"
        model_revision = "r"
        request_id = "req-bad"

        def answer(self, question_id):
            raise RuntimeError("answer store exploded")

    with pytest.raises(ValueError):
        service.drain_completed_judgment(_Raising())
    assert submitted == []

    # The ledger was not poisoned: a valid drain still works afterwards.
    receipts = service.drain_completed_judgment(_judgment("req-1"))
    assert [item["ok"] for item in receipts] == [True, True]
    assert len(submitted) == 2


def test_sink_registration_validates_callables(tmp_path):
    service = _light_service(tmp_path)
    with pytest.raises(ValueError):
        service.register_proposal_sink(
            submit="inbox",
            subject_context=lambda judgment: {"subject_ref": "repo:x"})
    with pytest.raises(ValueError):
        service.register_proposal_sink(
            submit=lambda payload: "prop-x", subject_context=None)
    assert service.proposal_sink_registered is False

    submitted = []
    _register(service, submitted)
    assert service.proposal_sink_registered is True
    service.clear_proposal_sink()
    assert service.proposal_sink_registered is False
    assert service.drain_completed_judgment(_judgment()) == []
    assert submitted == []


def test_seen_request_ledger_is_bounded(tmp_path):
    service = _light_service(tmp_path)
    submitted = []
    _register(service, submitted)

    total = PROPOSAL_SEEN_REQUEST_BOUND + 40
    for index in range(total):
        service.drain_completed_judgment(_judgment(f"req-{index}"))
    assert len(service._proposal_seen) == PROPOSAL_SEEN_REQUEST_BOUND
    # The oldest entries were evicted first.
    with pytest.raises(ValueError):
        service.retry_proposal_drain("req-0")
    assert service.retry_proposal_drain(f"req-{total - 1}") is not None


# ---------------------------------------------------------------------------
# Production path: prepare_control_tick drains automatically.
# ---------------------------------------------------------------------------

def _spec(question_id, labels):
    return QuestionSpec(
        question_id=question_id, family=question_id, question_type="choice",
        instructions=f"Which already-authorized option best fits ({question_id})?",
        criteria=tuple((label, f"Already-authorized {label}.")
                       for label in labels))


def _frame(identity, question_ids):
    specs = {
        "test_strategy_preference": ("testworks", "godkiller", "terraformer"),
        "decomposition_preference": ("godkiller", "testworks", "terraformer"),
    }
    template = helper_frame(question_ids=("route_preference",))
    questions = tuple(_spec(qid, specs[qid]) for qid in question_ids)
    state_digest = canonical_hash(template.state.to_dict())
    question_bundle_digest = canonical_hash(
        [item.to_dict() for item in questions])
    policy_hash = "c" * 64
    request_digest = canonical_hash(
        {"identity": identity.to_dict(), "sequence": 1,
         "state_digest": state_digest,
         "question_bundle_digest": question_bundle_digest,
         "policy_hash": policy_hash})
    return SystemOneFrame(
        identity=identity, request_id="pipe-frame-1", sequence=1,
        state_digest=state_digest,
        question_bundle_digest=question_bundle_digest,
        request_digest=request_digest, policy_hash=policy_hash,
        backend_id="test-backend", model_id="typesafe/jev-1.14",
        model_revision="jev-1.14", model_content_hash="a" * 64,
        provider_protocol_hash="b" * 64, calibration_bundle_hash="",
        state=template.state, questions=questions)


def _completed_judgment(frame, request_id):
    dists = {
        "test_strategy_preference": ("testworks", {"testworks": 0.7,
                                                   "godkiller": 0.2,
                                                   "terraformer": 0.1}),
        "decomposition_preference": ("godkiller", {"godkiller": 0.6,
                                                   "testworks": 0.3,
                                                   "terraformer": 0.1}),
    }
    wanted = {item.question_id for item in frame.questions}
    answers = []
    for qid, (selected, dist) in dists.items():
        if qid not in wanted:
            continue
        total = sum(dist.values())
        answers.append(QuestionAnswer(
            question_id=qid, selected=selected,
            raw_probabilities=tuple(QuestionProbability(label, value / total)
                                    for label, value in dist.items())))
    return SystemOneJudgment(
        identity=frame.identity, request_id=request_id,
        sequence=frame.sequence, state_digest=frame.state_digest,
        question_bundle_digest=frame.question_bundle_digest,
        request_digest=frame.request_digest, policy_hash=frame.policy_hash,
        backend_id=frame.backend_id, model_id=frame.model_id,
        model_revision=frame.model_revision,
        model_content_hash=frame.model_content_hash,
        provider_protocol_hash=frame.provider_protocol_hash,
        calibration_bundle_hash=frame.calibration_bundle_hash,
        answers=tuple(answers))


class _FakeDecisions:
    def judge(self, frame):
        return SimpleNamespace(
            status="ok",
            judgment=_completed_judgment(frame, "pipe-req-1"),
            to_dict=lambda: {"status": "ok"})

    def decide_behavior(self, context, *, judgment=None):
        state = context.state.to_dict()
        state["revision"] = context.state.revision + 1
        lease = context.attention_lease
        state["attention_lease"] = lease.to_dict() if lease is not None else None
        if lease is not None:
            state["focus_criteria"] = list(lease.criterion_ids)
        return {"decision_id": "pipe-decision", "scope": context.scope,
                "expected_revision": context.state.revision, "state": state,
                "directives": (), "preferred_stateworks": (),
                "intervention_outcomes": (), "reason": "pipe-noop"}


def _pipeline_frame_factory(identity, *args, **kwargs):
    mapped = SystemOneIdentity(
        namespace_id=identity.namespace_id,
        application_id=identity.application_id,
        application_instance_id=identity.application_instance_id,
        agent_id=identity.agent_id,
        agent_instance_id=identity.agent_instance_id,
        session_id=identity.session_id, task_id=identity.task_id,
        attempt_id=identity.attempt_id)
    return _frame(mapped, ("test_strategy_preference",
                           "decomposition_preference"))


def _pipeline_runtime(tmp_path):
    identity = RuntimeIdentity(
        namespace_id="tenant-a", application_id="codex",
        application_instance_id="codex-local", agent_instance_id="agent-1",
        session_id="session-pipe", task_id="task-pipe", attempt_id="attempt-1",
        workflow_id="workflow-1", subject_ref="repo:example",
        segment_id="segment-0", agent_id="codex",
        behavioral_subject="task-pipe", goal_contract_hash="a" * 64)
    goals = GoalSetSnapshot(1, (GoalItemSnapshot(
        criterion_id="criterion-visual", status="READY",
        claim="Render the requested page at the target viewport."),))
    store = BehaviorStateStore(tmp_path / "behavior.sqlite3")
    service = ControlPlaneService(
        decision_service=_FakeDecisions(), state_store=store,
        system_one_frame_factory=_pipeline_frame_factory)
    client = InProcessControlClient(service)
    bridge = HostControlBridge(
        identity=identity, objective="Render the requested page",
        objective_criteria=("criterion-visual",), initial_goal_set=goals,
        client=client,
        host_policy_context={"deliverable_kind": "code_behavior"})
    return bridge, client, service, goals


def _prepare_tick(bridge, service, goals, tick_id, boundary_sequence=0):
    observation = RuntimeObservation(
        action_index=0, action_completed=False,
        criterion_status={"criterion-visual": "unmet"}, goal_set=goals)
    snapshot, _ = bridge._csw_snapshot()
    request = ControlTickRequest(
        control_session_id=bridge.identity.scope, tick_id=tick_id,
        expected_behavior_revision=bridge.behavior_state.revision,
        cfw_lifecycle_revision=bridge._current_cfw_revision(),
        observation=observation, goal_set=goals,
        cfw_signals=bridge._cfw_signals(observation),
        csw_snapshot=snapshot, boundary_kind="operator",
        boundary_sequence=boundary_sequence)
    prepared = service.prepare_control_tick(request.to_dict())
    return prepared, snapshot


def _abort_tick(bridge, service, prepared, snapshot):
    service.abort_control_tick(ControlAbortRequest(
        control_session_id=bridge.identity.scope, tick_id=prepared["tick_id"],
        decision_id=prepared["decision_id"],
        basis_digest=prepared["basis_digest"], applied_directive_ids=(),
        rollback_completed=True, failure_class="test_cleanup",
        cfw_lifecycle_revision=bridge._current_cfw_revision(),
        csw_snapshot=snapshot).to_dict())


def _run(coro):
    return asyncio.run(coro)


def test_prepare_drains_completed_judgment_onto_result_path(tmp_path):
    bridge, _client, service, goals = _pipeline_runtime(tmp_path)
    submitted = []
    _register(service, submitted)

    async def scenario():
        tick_snapshots = []
        try:
            assert (await bridge.open())["status"] == "ready"
            prepared, snapshot = _prepare_tick(bridge, service, goals,
                                              "pipe-tick-1")
            assert prepared["status"] == "prepared"
            receipts = prepared["proposal_receipts"]
            assert [item["ok"] for item in receipts] == [True, True]
            assert [item["kind"] for item in receipts] == [
                "test_selection_draft", "partition_draft"]
            assert len(submitted) == 2
            assert all(item["subject_ref"] == "repo:x" for item in submitted)
            assert {item["body"]["provider_decision_id"]
                    for item in submitted} == {"pipe-req-1"}
            _abort_tick(bridge, service, prepared, snapshot)

            # A second tick completes the same judgment again (same
            # request_id): the replay is idempotent, no duplicate proposals.
            replayed, replay_snapshot = _prepare_tick(
                bridge, service, goals, "pipe-tick-2", boundary_sequence=1)
            tick_snapshots.append((replayed, replay_snapshot))
            assert replayed["proposal_receipts"] == receipts
            assert len(submitted) == 2
        finally:
            for prepared, snapshot in tick_snapshots:
                _abort_tick(bridge, service, prepared, snapshot)
            await bridge.close()

    _run(scenario())


def test_prepare_without_sink_leaves_response_untouched(tmp_path):
    bridge, _client, service, goals = _pipeline_runtime(tmp_path)

    async def scenario():
        prepared = None
        snapshot = None
        try:
            assert (await bridge.open())["status"] == "ready"
            prepared, snapshot = _prepare_tick(bridge, service, goals,
                                              "pipe-tick-1")
            assert prepared["status"] == "prepared"
            assert "proposal_receipts" not in prepared
        finally:
            if prepared is not None:
                _abort_tick(bridge, service, prepared, snapshot)
            await bridge.close()

    _run(scenario())


def test_prepare_surfaces_failed_sink_on_result_path(tmp_path):
    bridge, _client, service, goals = _pipeline_runtime(tmp_path)
    submitted = []
    _register(service, submitted, fail=True)

    async def scenario():
        prepared = None
        snapshot = None
        try:
            assert (await bridge.open())["status"] == "ready"
            prepared, snapshot = _prepare_tick(bridge, service, goals,
                                              "pipe-tick-1")
            # The control path still prepares; the sink failure is visible
            # as failed receipts, not swallowed and not raised.
            assert prepared["status"] == "prepared"
            receipts = prepared["proposal_receipts"]
            assert len(receipts) == 2
            assert all(item["ok"] is False for item in receipts)
            assert all("RuntimeError" in item["error"] for item in receipts)
            assert submitted == []
        finally:
            if prepared is not None:
                _abort_tick(bridge, service, prepared, snapshot)
            await bridge.close()

    _run(scenario())
