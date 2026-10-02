from __future__ import annotations

from lib.behavior_state import (BehaviorState, CSWBehaviorSnapshot, GoalItemSnapshot,
                                GoalSetSnapshot)
from lib.control.system_one import create_system_one_frame_factory
from lib.runtime.contracts import RuntimeIdentity, RuntimeObservation
from lib.system_one_backends import DisabledSystemOneBackend
from lib.system_one_contracts import SystemOneFrame
from lib.system_one_service import SystemOneService


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

    assert restored.sequence == 10
    assert restored.question_ids == (
        "action_relation", "progress_relation", "retry_relation", "goal_drift")
    assert restored.state.criteria[0].criterion_id == "criterion-visual"
    assert restored.state.criteria[0].status == "active"
