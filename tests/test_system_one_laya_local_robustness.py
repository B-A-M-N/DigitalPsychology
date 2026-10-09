"""Opt-in local checkpoint consistency canary (no API and no real trajectories)."""
import os

import pytest

from lib.laya_configuration import model_settings
from lib.protocol_artifact import PROTOCOL_HASH, PROTOCOL_VERSION, QUESTION_REGISTRY
from lib.system_one_backends import PROVIDER_PROTOCOL_HASH, LayaProvider
from lib.system_one_contracts import (
    ActivityFact,
    AttemptSemanticDescriptor,
    CriterionState,
    QuestionSpec,
    SemanticState,
    SystemOneFrame,
    SystemOneIdentity,
    WorkflowFact,
    canonical_hash,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("DIGITALPSYCHOLOGY_RUN_LOCAL_LAYA_ROBUSTNESS") != "1",
    reason="opt-in local checkpoint test; never starts model-backed work by default",
)


@pytest.fixture(scope="module")
def local_laya():
    provider = LayaProvider.from_environment()
    provider.warmup_and_wait()
    yield provider
    provider.close()


def _frame(family: str, objective: str, *, reverse_options: bool) -> SystemOneFrame:
    criterion_id = "incidental-criterion-a"
    activity = ActivityFact(
        sequence=4,
        activity_type="action",
        source="host_observed" if family == "progress_relation" else "model_proposed",
        action_class="inspect_output" if family == "progress_relation" else "write_report",
        host_declared_relation="ambiguous",
        target_kind="workspace",
        target_scope="repository",
        target_ref_digest="a" * 64,
        proposed_purpose="Inspect the artifact and verify the requested result.",
        proposed_purpose_source="derived",
        affected_criterion_ids=(criterion_id,),
        result_class="completed" if family == "progress_relation" else None,
        artifact_changed=family == "progress_relation",
        progress_class="VERIFICATION_PROGRESS" if family == "progress_relation" else None,
        evidence_kinds=("artifact_digest",),
        relation_certainty="inferred",
    )
    failed = AttemptSemanticDescriptor(
        cause_class="verification_failure",
        hypothesis_summary="The existing report did not verify the target output.",
        mechanism="Run the same verification command again without changing inputs.",
        evidence_target="the same report and command result",
        planned_action_class="run_verification",
        planned_target_kind="workspace",
        failed_result_class="no_change",
        premise_digest="b" * 64,
        action_plan_digest="c" * 64,
        affected_criterion_ids=(criterion_id,),
        source="host_observed",
    )
    workflows = (
        WorkflowFact(statework_id="repair", current_state="ACTIVE",
                     progress_class="RECOVERY", domain="repository",
                     purpose="Change the implementation to meet the criterion.",
                     eligible=True),
        WorkflowFact(statework_id="verify", current_state="READY",
                     progress_class="FORWARD_PROGRESS", domain="repository",
                     purpose="Check the artifact against the acceptance criteria.",
                     eligible=True),
    )
    state = SemanticState(
        task={
            "objective": objective,
            "work_mode": "delivery",
            "deliverable_kind": "code_behavior",
            "task_complexity": "standard",
            "phase": "deliver",
            "active_segment": "incidental-segment-a",
            "active_statework": None,
            "active_flow": None,
            "non_goals": ["Do not modify unrelated files."],
            "domain_tags": ["repository"],
        },
        situation={
            "current_state": "DELIVER",
            "blockers": [],
            "actions_since_goal_progress": 2,
            "actions_since_artifact_change": 1,
            "verification_actions_since_artifact_change": 0,
            "recovery_epochs": 0,
            "repeated_branch_count": 1,
        },
        criteria=(CriterionState(
            criterion_id, "Produce and verify the requested repository artifact.",
            "objective", status="unmet"),),
        activity=(activity,),
        workflows=workflows if family == "route_preference" else (),
        convergence=(),
        candidate=activity,
        failed_approaches=(failed,) if family == "retry_relation" else (),
        legal_routes=("repair", "verify") if family == "route_preference" else (),
    )
    spec = QUESTION_REGISTRY[family]
    options = (tuple((key, value) for key, value in spec["criteria"].items())
               if family != "route_preference" else (
                   ("repair", "Change the implementation to meet the criterion."),
                   ("verify", "Check the artifact against the acceptance criteria."),
               ))
    if reverse_options:
        options = tuple(reversed(options))
    question = QuestionSpec(
        question_id=family, family=family, question_type="choice",
        instructions=spec["instructions"], criteria=options)
    identity = SystemOneIdentity(
        namespace_id="offline-fixture", application_id="dp-robustness",
        application_instance_id="dp-robustness-local", agent_id="fixture-agent",
        agent_instance_id="fixture-agent-1", session_id="fixture-session",
        task_id="fixture-task", attempt_id="fixture-attempt")
    state_digest = canonical_hash(state.to_dict())
    question_digest = canonical_hash([question.to_dict()])
    policy_hash = canonical_hash({"fixture": "synthetic robustness only"})
    request_digest = canonical_hash({
        "identity": identity.to_dict(), "sequence": 1,
        "state_digest": state_digest,
        "question_bundle_digest": question_digest,
        "policy_hash": policy_hash,
    })
    settings = model_settings()
    return SystemOneFrame(
        identity=identity,
        request_id="synthetic-" + request_digest[:32],
        sequence=1,
        state_digest=state_digest,
        question_bundle_digest=question_digest,
        request_digest=request_digest,
        policy_hash=policy_hash,
        backend_id="digitalpsychology-laya-native",
        model_id=settings["model_id"],
        model_revision=settings["model_revision"],
        model_content_hash=settings["model_content_sha256"],
        provider_protocol_hash=PROVIDER_PROTOCOL_HASH,
        calibration_bundle_hash="",
        state=state,
        questions=(question,),
        protocol_version=PROTOCOL_VERSION,
        protocol_hash=PROTOCOL_HASH,
    )


@pytest.mark.parametrize("family", tuple(QUESTION_REGISTRY))
def test_local_laya_is_stable_for_paraphrase_and_option_order(local_laya, family):
    original = "Complete the requested artifact and verify it against the acceptance criteria."
    paraphrase = "Finish the requested deliverable, then check it against the acceptance criteria."
    frames = (
        _frame(family, original, reverse_options=False),
        _frame(family, paraphrase, reverse_options=False),
        _frame(family, original, reverse_options=True),
    )
    judgments = [local_laya.judge(frame) for frame in frames]
    answers = [judgment.answer(family) for judgment in judgments]
    assert all(answer is not None for answer in answers)
    assert answers[0].selected == answers[1].selected, family
    assert answers[0].selected == answers[2].selected, family
    assert judgments[0].local_inference_diagnostics["advisory_eligible"] is True
    assert judgments[1].local_inference_diagnostics["advisory_eligible"] is True
    assert judgments[2].local_inference_diagnostics["advisory_eligible"] is True
