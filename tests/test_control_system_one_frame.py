from __future__ import annotations

import hashlib

from lib.control.service import ControlPlaneService
from lib.behavior_state import (BehaviorState, CSWBehaviorSnapshot, FrameworkSignal,
                                GoalItemSnapshot, GoalSetSnapshot, StateWorkOption)
from lib.control_protocol import ControlSessionDescriptor, ControlTickRequest
from lib.control.system_one import create_system_one_frame_factory
from lib.runtime.contracts import ActionCandidate, RuntimeIdentity, RuntimeObservation
from lib.runtime.store import BehaviorStateStore
from lib.service import DigitalPsychologyService
from lib.system_one_backends import DisabledSystemOneBackend
from lib.system_one_contracts import (QuestionAnswer, QuestionProbability,
                                      SystemOneCapability, SystemOneFrame,
                                      SystemOneJudgment)
from lib.protocol_artifact import (PROTOCOL_HASH, PROTOCOL_VERSION,
                                   QUESTION_REGISTRY, QUESTION_REGISTRY_HASH,
                                   STATE_PROJECTION_SCHEMA_HASH)
from lib.system_one_service import SystemOneService


class _ShadowBackend:
    backend_id = "fixture-laya"
    model_id = "fixture-model"
    model_revision = "fixture-revision"
    model_content_hash = "a" * 64
    provider_protocol_hash = "b" * 64
    max_latency_ms = 1000

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
            max_state_bytes=32768, stateful_sessions=False)

    def judge(self, frame):
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


def test_control_frame_factory_builds_a_valid_host_grounded_frame():
    identity = RuntimeIdentity(
        namespace_id="tenant-a", application_id="codex",
        application_instance_id="codex-local", agent_instance_id="agent-1",
        session_id="session-1", task_id="task-1", attempt_id="attempt-1",
        workflow_id="implementation", subject_ref="repo:example",
        segment_id="segment-0", agent_id="codex", behavioral_subject="task-1",
        goal_contract_hash="a" * 64,
    )
    goals = GoalSetSnapshot(4, (GoalItemSnapshot(
        "criterion-visual", status="ACTIVE", actions_since_progress=2,
        equivalent_retry_count=1),))
    observation = RuntimeObservation(
        action_index=9, goal_set=goals, progress_made=False,
        evidence_refs=("host-evidence-1",), approach_epoch="hypothesis-2")
    service = SystemOneService(DisabledSystemOneBackend(), mode="shadow")
    try:
        factory = create_system_one_frame_factory(service)
        frame = factory(
            identity, "Render the requested page", ("criterion-visual",),
            BehaviorState(),
            observation, CSWBehaviorSnapshot(revision=2), (),
        )
        restored = SystemOneFrame.from_mapping(frame.canonical_payload())
    finally:
        service.close()

    assert restored.sequence == 20
    assert restored.question_ids == ("retry_relation",)
    assert restored.state.criteria[0].criterion_id == "criterion-visual"
    assert restored.state.criteria[0].status == "active"


def test_system_one_candidate_frame_contains_only_sanitized_upcoming_action():
    identity = RuntimeIdentity(
        namespace_id="tenant-a", application_id="codex",
        application_instance_id="codex-local", agent_instance_id="agent-1",
        session_id="session-1", task_id="task-candidate", attempt_id="attempt-1",
        workflow_id="implementation", subject_ref="repo:example",
        segment_id="segment-0", agent_id="codex", behavioral_subject="task-candidate",
    )
    goals = GoalSetSnapshot(4, (GoalItemSnapshot(
        "criterion-visual", status="READY"),))
    candidate = ActionCandidate(
        action_class="file_edit",
        proposed_purpose="Implement the requested visual criterion",
        affected_criterion_ids=("criterion-visual",),
        action_id="action-visual-1", tool_name="apply_patch",
        operation="edit_file", target_kind="repository",
        mutating=True, reversible=True, external=False,
        approach_epoch="approach-1")
    observation = RuntimeObservation(
        action_index=10, action_completed=False, goal_set=goals,
        candidate_action=candidate, equivalent_retry_count=1)
    service = SystemOneService(DisabledSystemOneBackend(), mode="shadow")
    try:
        factory = create_system_one_frame_factory(service)
        frame = factory(identity, "Render the requested page", ("criterion-visual",),
                        BehaviorState(revision=2), observation,
                        CSWBehaviorSnapshot(revision=0), ())
    finally:
        service.close()

    assert frame.sequence == 25
    assert frame.question_ids == ("retry_relation",)
    assert frame.state.candidate.source == "model_proposed"
    assert frame.state.candidate.action_class == "file_edit"
    assert frame.state.candidate.proposed_purpose == candidate.proposed_purpose
    assert frame.state.candidate.affected_criterion_ids == ("criterion-visual",)

    request = ControlTickRequest(
        control_session_id=identity.scope, tick_id="pre-action-roundtrip",
        expected_behavior_revision=2, cfw_lifecycle_revision=0,
        observation=observation, goal_set=goals, cfw_signals=(),
        csw_snapshot=CSWBehaviorSnapshot(revision=0),
        boundary_kind="pre_action", boundary_sequence=37)
    restored_request = ControlTickRequest.from_mapping(request.to_dict())
    assert restored_request.boundary_kind == "pre_action"
    assert restored_request.boundary_sequence == 37
    assert restored_request.observation.candidate_action == candidate


def test_system_one_frame_uses_host_deliverable_and_truthful_optional_counters():
    identity = RuntimeIdentity(
        namespace_id="tenant-a", application_id="codex",
        application_instance_id="codex-local", agent_instance_id="agent-1",
        session_id="session-task-kind", task_id="task-task-kind",
        attempt_id="attempt-1", workflow_id="implementation",
        subject_ref="repo:example", segment_id="segment-0",
        agent_id="codex", behavioral_subject="task-task-kind")
    service = SystemOneService(DisabledSystemOneBackend(), mode="shadow")
    try:
        frame = create_system_one_frame_factory(service)(
            identity, "Deploy the service", ("criterion-1",), BehaviorState(),
            RuntimeObservation(
                action_index=2, evidence_refs=("host-evidence-2",),
                actions_since_artifact_change=5,
                verification_actions_since_artifact_change=2),
            CSWBehaviorSnapshot(revision=0), (),
            {"task_kind": "deployment"},
        )
    finally:
        service.close()

    assert frame.state.task["deliverable_kind"] == "infrastructure"
    assert frame.state.situation["actions_since_artifact_change"] == 5
    assert frame.state.situation["verification_actions_since_artifact_change"] == 2


def test_unbound_diagnostic_candidate_remains_unknown_and_semantic_criteria_prioritize_starvation():
    identity = RuntimeIdentity(
        namespace_id="tenant-a", application_id="codex",
        application_instance_id="codex-local", agent_instance_id="agent-1",
        session_id="session-unbound-action", task_id="task-unbound-action",
        attempt_id="attempt-1", workflow_id="implementation",
        subject_ref="repo:example", segment_id="segment-0",
        agent_id="codex", behavioral_subject="task-unbound-action")
    goals = GoalSetSnapshot(4, tuple(
        GoalItemSnapshot(
            f"criterion-{index:02d}",
            status="BLOCKED" if index == 20 else "READY",
        ) for index in range(1, 21)))
    candidate = ActionCandidate(
        action_id="diagnostic-read", action_class="read",
        tool_name="read_file", operation="inspect",
        proposed_purpose="Inspect the current failure evidence",
        affected_criterion_ids=(), relation_certainty="unknown",
        mutating=False, reversible=True)
    seen = {f"criterion-{index:02d}": 4 for index in range(1, 19)}
    seen.update({"criterion-19": 0, "criterion-20": 0})
    service = SystemOneService(DisabledSystemOneBackend(), mode="shadow")
    try:
        frame = create_system_one_frame_factory(service)(
            identity, "Repair the service", ("criterion-01", "criterion-02", "criterion-03"),
            BehaviorState(attention_last_seen=seen),
            RuntimeObservation(action_index=10, action_completed=False,
                              goal_set=goals, candidate_action=candidate),
            CSWBehaviorSnapshot(revision=0), (),
            {"deliverable_kind": "code_change"},
        )
    finally:
        service.close()

    assert frame.state.task["deliverable_kind"] == "code_behavior"
    assert frame.state.candidate.affected_criterion_ids == ()
    assert frame.state.candidate.relation_certainty == "unknown"
    semantic_ids = {item.criterion_id for item in frame.state.criteria}
    assert "criterion-19" in semantic_ids
    assert "criterion-20" in semantic_ids
    assert "criterion-08" not in semantic_ids


def test_frame_factory_does_not_substitute_an_unplanned_enabled_question():
    identity = RuntimeIdentity(
        namespace_id="tenant-a", application_id="codex",
        application_instance_id="codex-local", agent_instance_id="agent-1",
        session_id="session-query-plan", task_id="task-query-plan",
        attempt_id="attempt-1", workflow_id="implementation",
        subject_ref="repo:example", segment_id="segment-0",
        agent_id="codex", behavioral_subject="task-query-plan")
    goals = GoalSetSnapshot(1, (GoalItemSnapshot(
        "criterion-visual", status="ACTIVE", equivalent_retry_count=1),))
    candidate = ActionCandidate(
        action_id="candidate-query-plan", action_class="file_edit",
        tool_name="apply_patch", operation="edit_file",
        proposed_purpose="Implement the requested criterion",
        affected_criterion_ids=("criterion-visual",))
    observation = RuntimeObservation(
        action_index=1, action_completed=False, goal_set=goals,
        candidate_action=candidate, equivalent_retry_count=1)
    service = SystemOneService(
        DisabledSystemOneBackend(), mode="shadow",
        enabled_question_families=("action_relation",))
    try:
        frame = create_system_one_frame_factory(service)(
            identity, "Render the requested page", ("criterion-visual",),
            BehaviorState(), observation, CSWBehaviorSnapshot(revision=0), ())
    finally:
        service.close()

    assert frame is None


def test_laya_statework_fit_contains_only_bounded_csw_eligible_choices():
    identity = RuntimeIdentity(
        namespace_id="tenant-a", application_id="codex",
        application_instance_id="codex-local", agent_instance_id="agent-1",
        session_id="session-statework-fit", task_id="task-statework-fit",
        attempt_id="attempt-1", workflow_id="implementation",
        subject_ref="repo:example", segment_id="segment-0",
        agent_id="codex", behavioral_subject="task-statework-fit")
    csw = CSWBehaviorSnapshot(
        revision=3,
        eligible_stateworks=(
            StateWorkOption("diagnose-flow", domain="coding",
                            behavior_tags=("diagnose",),
                            purpose="Inspect evidence and isolate the failure."),
            StateWorkOption("repair-flow", domain="coding",
                            behavior_tags=("diagnose",),
                            purpose="Apply a bounded repair after diagnosis."),
            StateWorkOption("active-flow", domain="coding",
                            behavior_tags=("diagnose",),
                            purpose="Already active and cannot be selected again."),
        ),
        active_stateworks=("active-flow",),
    )
    service = SystemOneService(DisabledSystemOneBackend(), mode="shadow")
    try:
        frame = create_system_one_frame_factory(service)(
            identity, "Repair the requested behavior", ("criterion-1",),
            BehaviorState(cognitive_mode="DIAGNOSE"),
            RuntimeObservation(action_index=1), csw, ())
    finally:
        service.close()

    assert frame.question_ids == ("route_preference",)
    choices = dict(frame.questions[0].criteria)
    assert set(choices) == {"diagnose-flow", "repair-flow"}
    assert "Inspect evidence" in choices["diagnose-flow"]
    assert "Already active" not in " ".join(choices.values())
    assert set(frame.state.legal_routes) == set(choices)


def test_private_control_tick_audits_shadow_laya_with_cfw_and_csw_context(tmp_path):
    identity = RuntimeIdentity(
        namespace_id="tenant-a", application_id="codex",
        application_instance_id="codex-local", agent_instance_id="agent-1",
        session_id="session-shadow", task_id="task-shadow", attempt_id="attempt-1",
        workflow_id="implementation", subject_ref="repo:example",
        segment_id="segment-0", agent_id="codex", behavioral_subject="task-shadow",
        goal_contract_hash="c" * 64,
    )
    goals = GoalSetSnapshot(4, (GoalItemSnapshot(
        "criterion-visual", status="ACTIVE", actions_since_progress=2),))
    csw = CSWBehaviorSnapshot(
        revision=3, active_stateworks=("repair",), workflow_states={"repair": "INSPECTING"},
        snapshot_id=hashlib.sha256(b"csw-repair").hexdigest(),
        workflow_revisions={"repair": 2}, eligible_steps={"repair": ("inspect",)})
    state_store = BehaviorStateStore(tmp_path / "behavior.sqlite3")
    initial_state = BehaviorState(revision=1, active_stateworks=("repair",))
    state_store.save(identity.scope, expected_revision=0, expected_action_index=-1,
                     action_index=0, state=initial_state)
    system_one = SystemOneService(_ShadowBackend(), mode="shadow")
    dp = DigitalPsychologyService(event_db=tmp_path / "events.sqlite3",
                                  system_one_service=system_one)
    control = ControlPlaneService(decision_service=dp, state_store=state_store)
    descriptor = ControlSessionDescriptor(
        identity=identity, objective="Render the requested page",
        objective_criteria=("criterion-visual",), initial_goal_set=goals,
        available_frameworks=("owl", "anchor", "fuse", "ward", "flow", "sispis", "dox"),
        csw_authority_identity={"authority": "test-csw"}, host_capabilities={},
    )
    control.open_control_session(descriptor.to_dict())
    observation = RuntimeObservation(
        action_index=1, goal_set=goals,
        criterion_status={"criterion-visual": "in_progress"},
        equivalent_retry_count=1, approach_epoch="hypothesis-2")
    request = ControlTickRequest(
        control_session_id=identity.scope, tick_id="shadow-tick",
        expected_behavior_revision=1, cfw_lifecycle_revision=2,
        observation=observation, goal_set=goals,
        cfw_signals=(FrameworkSignal(
            source="owl", code="owl.approach_failed", severity="critical",
            criterion_id="criterion-visual"),),
        csw_snapshot=csw)
    try:
        prepared = control.prepare_control_tick(request.to_dict())
    finally:
        control.close()

    audited = prepared["basis"]["system_one"]
    frame = audited["frame"]
    assert frame["state"]["situation"]["blockers"] == ["owl.approach_failed"]
    assert frame["state"]["task"]["active_statework"] == "repair"
    assert frame["state"]["workflows"][0]["current_state"] == "INSPECTING"
    assert audited["result"]["status"] == "shadow"
    assert {item["question_id"] for item in audited["result"]["judgment"]["answers"]} == {
        "retry_relation"}
    assert prepared["status"] == "prepared"
