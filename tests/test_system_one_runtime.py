from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import os
import sys
import threading
from types import SimpleNamespace

import pytest

from lib.protocol_artifact import (PROTOCOL_HASH, PROTOCOL_VERSION,
                                   QUESTION_REGISTRY, QUESTION_REGISTRY_HASH,
                                   STATE_PROJECTION_SCHEMA_HASH)
from lib.system_one_backends import (LayaSystemOneBackend, PROVIDER_PROTOCOL_HASH,
                                     ProviderHealthError, model_content_digest)
from lib.system_one_calibration import (CalibrationBundle, CalibrationRecord,
                                        calibration_metrics,
                                        calibration_record_from_events)
from lib.system_one_contracts import (ActivityFact, CriterionState, QuestionAnswer,
                                      QuestionProbability, QuestionSpec, SemanticState,
                                      SystemOneCapability, SystemOneFrame,
                                      SystemOneIdentity, SystemOneJudgment,
                                      canonical_hash)
from lib.system_one_learning import SystemOneCalibrationLifecycle
from lib.system_one_runtime import CircuitBreaker, SystemOneRuntime
from lib.system_one_service import SystemOneService


BACKEND_ID = "test-backend"
MODEL_ID = "test-model"
MODEL_REVISION = "test-revision"
MODEL_HASH = "a" * 64
PROVIDER_HASH = "b" * 64


def capability(backend=BACKEND_ID, model=MODEL_ID, revision=MODEL_REVISION,
               content_hash=MODEL_HASH, provider_hash=PROVIDER_HASH):
    return SystemOneCapability(
        backend_id=backend, model_id=model, model_revision=revision,
        model_content_hash=content_hash, provider_protocol_hash=provider_hash,
        protocol_version=PROTOCOL_VERSION, protocol_hash=PROTOCOL_HASH,
        question_registry_hash=QUESTION_REGISTRY_HASH,
        state_projection_schema_hash=STATE_PROJECTION_SCHEMA_HASH,
        supported_question_families=tuple(QUESTION_REGISTRY),
        max_state_bytes=32768, stateful_sessions=False)


def frame(*, agent="agent-1", sequence=1, question_ids=("progress_relation",),
          calibration_bundle=None, backend=BACKEND_ID, model=MODEL_ID,
          revision=MODEL_REVISION, content_hash=MODEL_HASH,
          provider_hash=PROVIDER_HASH, state_variant="same"):
    identity = SystemOneIdentity(
        namespace_id="tenant", application_id="app", application_instance_id="app-1",
        agent_id=agent, agent_instance_id=f"{agent}-instance", session_id="session-1",
        task_id="task-1", attempt_id="attempt-1", interaction_id="interaction-1")
    activity = ActivityFact(
        sequence=1, activity_type="action", source="host_observed",
        action_class="verification", host_declared_relation="ambiguous",
        target_kind="workspace", proposed_purpose="verify the requested result",
        affected_criterion_ids=("C1",))
    state = SemanticState(
        task={"objective": "Deliver the requested behavior.", "work_mode": "delivery",
              "deliverable_kind": "code_behavior", "task_complexity": "standard",
              "phase": "implementation", "active_segment": "segment-1",
              "active_statework": None, "active_flow": None,
              "non_goals": ["unrelated refactoring"]},
        situation={"current_state": "IMPLEMENTED", "blockers": [],
                   "actions_since_goal_progress": 2,
                   "actions_since_artifact_change": 1,
                   "verification_actions_since_artifact_change": 0,
                   "recovery_epochs": 0, "repeated_branch_count": 0},
        criteria=(CriterionState("C1", "Requested behavior works", "functional"),),
        activity=(activity,), workflows=(), convergence=(
            ("support_actions_since_goal_progress", 2),), candidate=activity,
        legal_routes=("implement", "diagnose", "verify"))
    if state_variant != "same":
        state = replace(state, situation={**state.situation,
                                          "current_state": state_variant})
    question_specs = []
    for question_id in question_ids:
        source = QUESTION_REGISTRY[question_id]
        criteria = source["criteria"]
        if question_id == "route_preference":
            criteria = {route: "Already-authorized legal route."
                        for route in state.legal_routes}
        question_specs.append(QuestionSpec(
            question_id=question_id, family=source["family"],
            question_type=source["type"], instructions=source["instructions"],
            criteria=tuple((key, description) for key, description in criteria.items())))
    questions = tuple(question_specs)
    state_digest = canonical_hash(state.to_dict())
    question_bundle_digest = canonical_hash([item.to_dict() for item in questions])
    policy_hash = "c" * 64
    bundle_hash = calibration_bundle.digest if calibration_bundle else ""
    request_digest = canonical_hash({"identity": identity.to_dict(), "sequence": sequence,
                                     "state_digest": state_digest,
                                     "question_bundle_digest": question_bundle_digest,
                                     "policy_hash": policy_hash})
    return SystemOneFrame(
        identity=identity, request_id=f"request-{sequence}", sequence=sequence,
        state_digest=state_digest, question_bundle_digest=question_bundle_digest,
        request_digest=request_digest, policy_hash=policy_hash,
        backend_id=backend, model_id=model, model_revision=revision,
        model_content_hash=content_hash, provider_protocol_hash=provider_hash,
        calibration_bundle_hash=bundle_hash, state=state, questions=questions)


def judgment(request, *, selected=None):
    answers = []
    for spec in request.questions:
        labels = tuple(label for label, _ in spec.criteria)
        chosen = selected or labels[0]
        if chosen not in labels:
            chosen = labels[0]
        probabilities = [0.0] * len(labels)
        index = labels.index(chosen)
        probabilities[index] = 0.85
        remainder = 0.15 / (len(labels) - 1)
        probabilities = [value if value else remainder for value in probabilities]
        answers.append(QuestionAnswer(
            question_id=spec.question_id, selected=chosen,
            raw_probabilities=tuple(QuestionProbability(label, value)
                                    for label, value in zip(labels, probabilities))))
    return SystemOneJudgment(
        identity=request.identity, request_id=request.request_id,
        sequence=request.sequence, state_digest=request.state_digest,
        question_bundle_digest=request.question_bundle_digest,
        request_digest=request.request_digest, policy_hash=request.policy_hash,
        backend_id=request.backend_id, model_id=request.model_id,
        model_revision=request.model_revision,
        model_content_hash=request.model_content_hash,
        provider_protocol_hash=request.provider_protocol_hash,
        calibration_bundle_hash=request.calibration_bundle_hash,
        answers=tuple(answers))


class FixedBackend:
    """Explicit injected provider for deterministic runtime tests."""

    max_latency_ms = 5000

    def __init__(self, *, responds=None, identity=None):
        self._capability = identity or capability()
        self.responds = responds
        self.calls = []

    @property
    def backend_id(self):
        return self._capability.backend_id

    @property
    def model_id(self):
        return self._capability.model_id

    @property
    def model_revision(self):
        return self._capability.model_revision

    @property
    def model_content_hash(self):
        return self._capability.model_content_hash

    @property
    def provider_protocol_hash(self):
        return self._capability.provider_protocol_hash

    def capability(self):
        return self._capability

    def judge(self, value):
        self.calls.append(value)
        if self.responds:
            return self.responds(value)
        return judgment(value)

    def reset_identity(self, identity):
        return None

    def resync(self, value):
        return None


def test_optional_laya_dependency_is_lazy_and_default_service_does_not_load_it(tmp_path):
    from lib.mcp_server import DigitalPsychologyService
    import tomllib

    project = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    assert all(not dependency.lower().startswith("laya")
               for dependency in project["project"]["dependencies"])
    assert project["project"]["optional-dependencies"]["laya"] == ["laya==0.3.22"]
    assert project["project"]["optional-dependencies"]["system-one"] == ["laya==0.3.22"]
    assert "laya" not in sys.modules
    service = DigitalPsychologyService(event_db=tmp_path / "events.sqlite3")
    try:
        assert service._system_one_service is None
        assert "laya" not in sys.modules
    finally:
        service.close()


def test_environment_factory_keeps_laya_optional_when_disabled():
    from lib.system_one_service import SystemOneService

    keys = [key for key in os.environ if key.startswith("DIGITALPSYCHOLOGY_SYSTEM_ONE_")]
    keys += ["DIGITALPSYCHOLOGY_SEMANTIC_PROVIDER"]
    previous = {key: os.environ.get(key) for key in keys}
    try:
        for key in keys:
            os.environ.pop(key, None)
        service = SystemOneService.from_environment()
        assert service.mode == "disabled"
        assert service.capability()["backend_id"] == "null"
        assert "laya" not in sys.modules
        service.close()
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_native_laya_adapter_renders_typed_questions_and_pins_local_content(tmp_path):
    for relative, content in {
        "model.safetensors": b"small fake model fixture",
        "rl_agent_config.json": b"{}",
        "encoder/config.json": b"{}",
        "tokenizer/tokenizer.json": b"{}",
        "tokenizer/tokenizer_config.json": b"{}",
    }.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    content_hash = model_content_digest(tmp_path)
    received = {}

    class FakeLayaAgent:
        def decide(self, state, *, questions, return_details, lang):
            received.update(state=state, questions=questions,
                            return_details=return_details, lang=lang)
            selected = {qid: next(iter(spec["criteria"]))
                        for qid, spec in questions.items()}
            probabilities = {qid: {label: (0.8 if label == selected[qid]
                                             else 0.2 / (len(spec["criteria"]) - 1))
                                   for label in spec["criteria"]}
                             for qid, spec in questions.items()}
            answers = {qid: {"type": "choice", "choice": selected[qid],
                             "probabilities": probabilities[qid]}
                       for qid in questions}
            return SimpleNamespace(values=answers, answers=answers,
                                   probabilities=probabilities)

    backend = LayaSystemOneBackend(
        tmp_path, model_id="fixture-laya", model_revision="local-test-revision",
        expected_model_content_hash=content_hash,
        max_latency_ms=30000,
        loader=lambda path, device: FakeLayaAgent())
    assert backend.max_latency_ms == 30000
    request = frame(backend=backend.backend_id, model=backend.model_id,
                    revision=backend.model_revision,
                    content_hash=backend.model_content_hash,
                    provider_hash=PROVIDER_PROTOCOL_HASH)
    response = backend.judge(request)
    assert response.answers[0].selected == request.questions[0].criteria[0][0]
    assert received["state"]["task"]["deliverable_kind"] == "code_behavior"
    assert received["questions"]["progress_relation"]["type"] == "choice"
    assert received["return_details"] is True
    with pytest.raises(ValueError, match="content hash"):
        LayaSystemOneBackend(tmp_path, model_id="fixture-laya",
                             model_revision="local-test-revision",
                             expected_model_content_hash="0" * 64,
                             loader=lambda path, device: FakeLayaAgent())
    with pytest.raises(ValueError, match="timeout"):
        LayaSystemOneBackend(tmp_path, model_id="fixture-laya",
                             model_revision="local-test-revision",
                             expected_model_content_hash=content_hash,
                             max_latency_ms=30001,
                             loader=lambda path, device: FakeLayaAgent())


def test_route_preference_uses_only_current_legal_route_choices():
    request = frame(question_ids=("route_preference",),
                    backend="digitalpsychology-laya-native", model="local",
                    revision="rev", content_hash="1" * 64,
                    provider_hash=PROVIDER_PROTOCOL_HASH)
    choices = dict(request.questions[0].criteria)
    assert tuple(choices) == request.state.legal_routes
    assert "send_email" not in choices


def test_runtime_rebinds_cache_and_scopes_it_to_full_identity():
    backend = FixedBackend()
    runtime = SystemOneRuntime(backend, cache_size=4)
    first = runtime.judge(frame(sequence=1), mode="shadow")
    cached = runtime.judge(frame(sequence=2), mode="shadow")
    other_agent = runtime.judge(frame(agent="agent-2", sequence=1), mode="shadow")
    assert first.judgment is not None
    assert cached.cache_hit is True
    assert cached.judgment.request_id == "request-2"
    assert cached.judgment.sequence == 2
    assert other_agent.cache_hit is False
    assert len(backend.calls) == 2


def test_runtime_accepts_a_foreign_repository_frame_through_the_vendored_wire_contract():
    request = frame()

    class ForeignRepositoryFrame:
        def canonical_payload(self):
            return request.canonical_payload()

    result = SystemOneRuntime(FixedBackend()).judge(
        ForeignRepositoryFrame(), mode="shadow")
    assert result.status == "shadow"
    assert result.judgment.request_digest == request.request_digest


def test_concurrent_identical_state_discards_old_response_after_newer_sequence():
    first_started = threading.Event()
    release_first = threading.Event()

    def respond(value):
        if value.sequence == 1:
            first_started.set()
            assert release_first.wait(2)
        return judgment(value)

    runtime = SystemOneRuntime(FixedBackend(responds=respond), max_concurrent_requests=2)
    old_result = []
    old_thread = threading.Thread(
        target=lambda: old_result.append(runtime.judge(frame(sequence=1), mode="shadow")))
    old_thread.start()
    assert first_started.wait(1)
    latest = runtime.judge(frame(sequence=2), mode="shadow")
    release_first.set()
    old_thread.join(2)
    assert latest.status == "shadow"
    assert old_result[0].status == "stale"


def test_bad_provider_contract_does_not_open_shared_health_breaker():
    calls = []

    def respond(value):
        calls.append(value.sequence)
        answer = judgment(value)
        if len(calls) == 1:
            wrong_identity = replace(answer.identity, agent_id="other-agent")
            return replace(answer, identity=wrong_identity)
        return answer

    breaker = CircuitBreaker(threshold=1, cooldown_seconds=30)
    runtime = SystemOneRuntime(FixedBackend(responds=respond), breaker=breaker)
    bad = runtime.judge(frame(sequence=1), mode="shadow")
    good = runtime.judge(frame(sequence=2), mode="shadow")
    assert bad.status == "rejected"
    assert good.status == "shadow"
    assert breaker.failures == 0


def test_provider_crash_uses_one_attempt_and_opens_health_breaker():
    calls = []

    def broken(value):
        calls.append(value.request_id)
        raise ProviderHealthError("offline")

    breaker = CircuitBreaker(threshold=1, cooldown_seconds=30)
    runtime = SystemOneRuntime(FixedBackend(responds=broken), breaker=breaker)
    assert runtime.judge(frame(sequence=1), mode="shadow").status == "unavailable"
    assert runtime.judge(frame(sequence=2), mode="shadow").status == "unavailable"
    assert calls == ["request-1"]


def calibration_record(question_id="progress_relation", *, status="active"):
    return CalibrationRecord(
        question_id=question_id, backend_id=BACKEND_ID, model_id=MODEL_ID,
        model_revision=MODEL_REVISION, model_content_hash=MODEL_HASH,
        provider_protocol_hash=PROVIDER_HASH, temperature=0.5,
        minimum_advisory_confidence=0.8, sample_count=100, training_count=80,
        validation_count=20, training_outcome_digest="d" * 64,
        validation_outcome_digest="e" * 64, validation_accuracy=0.95,
        validation_brier=0.05, validation_ece=0.04,
        work_modes=("delivery",), deliverable_kinds=("code_behavior",),
        independent_trajectory_count=30,
        cohort_counts=(("delivery", "code_behavior", 20),),
        minimum_cohort_trajectories=20,
        minimum_outcomes=20, status=status)


def test_calibration_is_per_question_family_and_changes_probabilities():
    bundle = CalibrationBundle({"progress_relation": calibration_record()})
    request = frame(question_ids=("progress_relation", "retry_relation"),
                    calibration_bundle=bundle)
    runtime = SystemOneRuntime(FixedBackend(), calibrations=bundle)
    result = runtime.judge(request, mode="advisory")
    assert result.status == "ok"
    assert result.calibrated_question_ids == ("progress_relation",)
    progress = result.judgment.answer("progress_relation")
    retry = result.judgment.answer("retry_relation")
    assert progress.calibrated_probabilities is not None
    assert progress.calibration_hash == bundle.records["progress_relation"].digest
    assert retry.calibrated_probabilities is None


def _calibration_events(count=100):
    events = []
    labels = tuple(QUESTION_REGISTRY["progress_relation"]["criteria"])
    for index in range(count):
        decision_id = f"decision-{index}"
        timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=2 * index)
        payload = {
            "system_one_decision_id": decision_id,
            "system_one_backend_id": BACKEND_ID,
            "system_one_model_id": MODEL_ID,
            "system_one_model_revision": MODEL_REVISION,
            "system_one_model_content_hash": MODEL_HASH,
            "system_one_provider_protocol_hash": PROVIDER_HASH,
            "system_one_work_mode": "delivery",
            "system_one_deliverable_kind": "code_behavior",
            "system_one_answers": {"progress_relation": {
                "question_id": "progress_relation", "selected": "goal_progress",
                "raw_probabilities": [{"label": label,
                                       "probability": (0.5 if label == "goal_progress"
                                                       else 0.1)} for label in labels],
                "calibrated_probabilities": None, "calibration_hash": None}},
        }
        identity = dict(namespace_id="tenant", application_id="app",
                        application_instance_id="app-1", agent_id="agent",
                        agent_instance_id="agent-instance", session_id="session",
                        task_id=f"task-{index}", attempt_id="attempt",
                        interaction_id="interaction")
        events.append(SimpleNamespace(
            event_type="system_one_decision", category="decision",
            payload=payload, timestamp=timestamp.isoformat().replace("+00:00", "Z"), **identity))
        events.append(SimpleNamespace(
            event_type="system_one_outcome", category="validation",
            payload={"system_one_outcome_for": decision_id,
                     "system_one_outcome_progress_relation": "goal_progress",
                     "system_one_outcome_authority": "validator",
                     "system_one_outcome_evidence_ref": f"evidence-{index}",
                     "system_one_independent_outcome": True},
            timestamp=(timestamp + timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
            **identity))
    return events


def test_calibration_uses_later_independent_outcomes_and_fits_temperature():
    events = _calibration_events()
    report = calibration_metrics(events, label="progress_relation")
    assert report["independent_outcomes"] == 100
    record = calibration_record_from_events(
        events, question_id="progress_relation", minimum_outcomes=100,
        minimum_accuracy=0.99, maximum_brier=0.02, maximum_ece=0.1)
    assert record.status == "candidate"
    assert record.temperature < 1.0
    assert record.validation_count >= 20


def test_calibration_rejects_self_labeled_or_nonlater_outcomes():
    events = _calibration_events(count=4)
    for event in events:
        if event.event_type == "system_one_outcome":
            event.payload["system_one_independent_outcome"] = False
    assert calibration_metrics(events)["independent_outcomes"] == 0
    with pytest.raises(ValueError, match="insufficient later independent"):
        calibration_record_from_events(events, question_id="progress_relation",
                                       minimum_outcomes=1)


def test_calibration_groups_attempts_and_interactions_into_one_trajectory():
    events = _calibration_events(count=100)
    for pair, offset in enumerate(range(0, len(events), 2)):
        task_id = "repeated-task"
        attempt_id = f"attempt-{pair}"
        interaction_id = f"interaction-{pair}"
        for event in events[offset:offset + 2]:
            event.task_id = task_id
            event.attempt_id = attempt_id
            event.interaction_id = interaction_id
    with pytest.raises(ValueError, match="independent trajectories"):
        calibration_record_from_events(
            events, question_id="progress_relation", minimum_outcomes=100)


def test_calibration_lifecycle_stages_activates_and_exports_active_bundle(tmp_path):
    lifecycle = SystemOneCalibrationLifecycle(tmp_path / "system-one-calibration.json")
    candidate = calibration_record().with_status("candidate")
    staged = lifecycle.stage(candidate, expected_revision=0)
    assert staged["revision"] == 1
    activated = lifecycle.activate(
        staged["candidate_id"], question_id="progress_relation",
        authority_ref="validator-review-1", expected_revision=1)
    assert activated["revision"] == 2
    bundle = lifecycle.active_bundle()
    assert bundle.records["progress_relation"].status == "active"
    path_data = json.loads((tmp_path / "system-one-calibration.json").read_text())
    restored = CalibrationBundle.from_mapping(bundle.to_dict())
    assert restored.digest == bundle.digest
    assert path_data["revision"] == 2


def test_digital_psychology_service_composes_system_one_without_an_mcp_tool(tmp_path):
    from lib.mcp_server import DigitalPsychologyService

    internal = SystemOneService(FixedBackend(), mode="shadow")
    service = DigitalPsychologyService(event_db=tmp_path / "events.sqlite3",
                                       system_one_service=internal)
    try:
        assert service.capability()["backend_id"] == BACKEND_ID
        result = service.judge(frame(), mode="shadow")
        assert result.judgment is not None
        assert not hasattr(service, "system_one_decide")
    finally:
        service.close()


def test_packaged_protocol_schema_is_canonical_copy():
    schema_path = Path(__file__).parents[1] / "lib" / "schemas" / "system-one-protocol.schema.json"
    schema = json.loads(schema_path.read_text())
    assert schema["protocol_version"] == "2.1.0"
    assert schema["$defs"]["SystemOneFrame"]["additionalProperties"] is False
    assert schema["x-question-registry"] == QUESTION_REGISTRY
