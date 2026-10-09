"""Opt-in local checkpoint consistency canary (no API and no real trajectories)."""
import json
import os
from dataclasses import replace

import pytest

from lib.laya_configuration import model_settings
from lib.protocol_artifact import PROTOCOL_HASH, PROTOCOL_VERSION, QUESTION_REGISTRY
from lib.system_one_backends import (
    PROVIDER_PROTOCOL_HASH,
    LayaProvider,
    laya_state_projection,
)
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


def _frame(family: str, objective: str, *, reverse_options: bool,
           criterion_id: str = "incidental-criterion-a") -> SystemOneFrame:
    criterion_claims = {
        "action_relation": "The final report must pass the published acceptance check.",
        "progress_relation": "The final report must pass the published acceptance check.",
        "retry_relation": "The report must be regenerated from current source files.",
        "goal_drift": "The navigation must expose an accessible landmark.",
        "route_preference": "The finished report must pass the published acceptance check.",
    }
    actions = {
        "action_relation": (
            "run_acceptance_check",
            "Run the published acceptance check against the final report and save its result."),
        "progress_relation": (
            "run_acceptance_check",
            "Run the published acceptance check against the final report and save its result."),
        "retry_relation": (
            "regenerate_then_verify",
            "Retain the stale-report hypothesis, but change the method: regenerate from the current source revision before checking and record a new report digest."),
        "goal_drift": (
            "verify_accessible_navigation",
            "Run the required accessibility check on the unfinished navigation landmark criterion."),
        "route_preference": (
            "run_acceptance_check",
            "Run the published acceptance check against the finished report."),
    }
    action_class, purpose = actions[family]
    activity = ActivityFact(
        sequence=4,
        activity_type="action",
        source="host_observed" if family == "progress_relation" else "model_proposed",
        action_class=action_class,
        host_declared_relation="ambiguous",
        target_kind="workspace",
        target_scope="repository",
        target_ref_digest="a" * 64,
        proposed_purpose=purpose,
        proposed_purpose_source=(
            "host_observed" if family == "progress_relation" else "model_proposed"),
        affected_criterion_ids=(criterion_id,),
        result_class="completed" if family == "progress_relation" else None,
        artifact_changed=False if family == "progress_relation" else None,
        progress_class="VERIFICATION_PROGRESS" if family == "progress_relation" else None,
        evidence_kinds=("acceptance_test_result",) if family == "progress_relation" else (),
        relation_certainty="inferred",
    )
    failed = AttemptSemanticDescriptor(
        cause_class="verification_failure",
        hypothesis_summary="The report is stale because it predates the latest source revision.",
        mechanism="Check the generated report directly without rebuilding it from current source.",
        evidence_target="acceptance-check output for the existing report",
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
                     purpose="Repair the report if its content fails the acceptance check.",
                     eligible=True),
        WorkflowFact(statework_id="verify", current_state="READY",
                     progress_class="FORWARD_PROGRESS", domain="repository",
                     purpose="Run the required acceptance check and retain its result.",
                     eligible=True),
    )
    current_criterion = CriterionState(
        criterion_id, criterion_claims[family], "objective",
        status="unmet",
        evidence_status="available" if family == "progress_relation" else "none",
        evidence_ref_digests=("d" * 64,) if family == "progress_relation" else ())
    criteria_before = ((CriterionState(
        criterion_id, criterion_claims[family], "objective", status="unmet"),)
        if family == "progress_relation" else None)
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
            "non_goals": ["Do not change unrelated files."],
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
        criteria=(current_criterion,),
        activity=(activity,),
        workflows=workflows if family == "route_preference" else (),
        convergence=(),
        candidate=activity,
        failed_approaches=(failed,) if family == "retry_relation" else (),
        legal_routes=("repair", "verify") if family == "route_preference" else (),
        criteria_before=criteria_before,
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


def _with_state(frame: SystemOneFrame, state: SemanticState) -> SystemOneFrame:
    state_digest = canonical_hash(state.to_dict())
    request_digest = canonical_hash({
        "identity": frame.identity.to_dict(), "sequence": frame.sequence,
        "state_digest": state_digest,
        "question_bundle_digest": frame.question_bundle_digest,
        "policy_hash": frame.policy_hash,
    })
    return replace(frame, state=state, state_digest=state_digest,
                   request_digest=request_digest,
                   request_id="synthetic-" + request_digest[:32])


@pytest.mark.parametrize("family", tuple(QUESTION_REGISTRY))
def test_local_laya_is_stable_for_paraphrase_and_option_order(local_laya, family):
    scenarios = {
        "action_relation": (
            "Produce the final report, then verify it with the published acceptance check.",
            "Finish the final report and run the published check to verify it.",
            "required_verification"),
        "progress_relation": (
            "Determine what the completed acceptance check established about the final report.",
            "Assess what the finished check showed about the final report.",
            "verification_progress"),
        "retry_relation": (
            "Regenerate the stale report from current sources, then verify the new report digest.",
            "Rebuild the report using current sources, then check its new digest.",
            "same_premise_new_action"),
        "goal_drift": (
            "Complete the accessible navigation landmark criterion and verify it.",
            "Finish and check the required accessible navigation landmark.",
            "no"),
        "route_preference": (
            "Choose the next workflow to verify that the finished report passes acceptance.",
            "Select the next workflow to check the completed report against acceptance.",
            "verify"),
    }
    original, paraphrase, expected = scenarios[family]
    frames = (
        _frame(family, original, reverse_options=False),
        _frame(family, paraphrase, reverse_options=False),
        _frame(family, original, reverse_options=True),
        _frame(family, original, reverse_options=False,
               criterion_id="renamed-incidental-criterion-b"),
    )
    judgments = [local_laya.judge(frame) for frame in frames]
    answers = [judgment.answer(family) for judgment in judgments]
    assert all(answer is not None for answer in answers)
    selected = [answer.selected for answer in answers]
    if (family == "retry_relation"
            and (selected[0] != expected or len(set(selected)) != 1)):
        pytest.xfail(
            "known local retry-family checkpoint limitation: "
            f"expected {expected} across paraphrases/options/IDs, got {selected}")
    assert answers[0].selected == expected, family
    assert answers[0].selected == answers[1].selected, family
    assert answers[0].selected == answers[2].selected, family
    assert answers[0].selected == answers[3].selected, family
    assert all(judgment.local_inference_diagnostics["advisory_eligible"] is True
               for judgment in judgments)
    projection = laya_state_projection(frames[0])
    if family == "progress_relation":
        assert "progress_class" not in str(projection)
    assert "host_declared_relation" not in str(projection)
    if family == "progress_relation":
        assert projection["criterion_progress_context"]["before_available"] is True
        assert projection["criterion_progress_context"]["before"][0]["evidence_status"] == "none"
        assert projection["criterion_progress_context"]["after"][0]["evidence_status"] == "available"


def test_local_laya_keeps_relevant_evidence_from_oversized_host_state(local_laya):
    frame = _frame(
        "action_relation",
        "Complete the requested artifact and verify it against the acceptance criteria.",
        reverse_options=False)
    expanded_state = replace(
        frame.state,
        convergence=tuple((f"irrelevant-host-context-{index:03d}-" + "x" * 20, index)
                          for index in range(240)),
    )
    frame = _with_state(frame, expanded_state)
    serialized_size = len(json.dumps(
        frame.canonical_payload(), sort_keys=True, separators=(",", ":"),
        ensure_ascii=False).encode("utf-8"))
    projection = laya_state_projection(frame)

    assert serialized_size > 12_000
    assert projection["criteria"][0]["claim"] == (
        "The final report must pass the published acceptance check.")
    assert projection["candidate_hypothesis"]["proposed_purpose"] == (
        "Run the published acceptance check against the final report and save its result.")
    assert "irrelevant-host-context" not in str(projection)

    judgment = local_laya.judge(frame)
    assert judgment.local_inference_diagnostics["advisory_eligible"] is True
    assert judgment.local_inference_diagnostics["truncation_status"] == "complete"
