from dataclasses import replace
from types import SimpleNamespace

from lib.system_one_backends import (
    _laya_diagnostics,
    _laya_questions,
    laya_state_projection,
)
from lib.system_one_contracts import (
    ActivityFact,
    CriterionState,
    QuestionSpec,
    SemanticState,
)
from lib.system_one_diagnostics import validate_laya_diagnostics
from lib.system_one_runtime import SystemOneRuntime


def _state(*, source="host_observed", identifier="criterion-private-a"):
    activity = ActivityFact(
        sequence=4, activity_type="action", source=source,
        action_class="write", host_declared_relation="ambiguous",
        target_kind="workspace", target_scope="repo", target_ref_digest="a" * 64,
        proposed_purpose="Update the target artifact",
        proposed_purpose_source="model_proposed",
        affected_criterion_ids=(identifier,), result_class="completed",
        artifact_changed=True, progress_class="VERIFICATION_PROGRESS",
        evidence_kinds=("artifact_digest",), relation_certainty="inferred")
    return SemanticState(
        task={
            "objective": "Complete the requested artifact and verify it.",
            "work_mode": "delivery", "deliverable_kind": "code_behavior",
            "task_complexity": "standard", "phase": "deliver",
            "active_segment": "segment-private-a", "active_statework": None,
            "active_flow": None, "non_goals": ["Do not alter unrelated files."],
            "domain_tags": ["repository"],
        },
        situation={
            "current_state": "DELIVER",
            "blockers": ["cfw_cause=private-cause-a;src=owl;codes=goal_drift;"
                         "actions=inspect;criteria=" + identifier + ";"
                         "refs=private-evidence-a;subject=private-subject-a"],
            "actions_since_goal_progress": 3,
            "actions_since_artifact_change": 1,
            "verification_actions_since_artifact_change": 0,
            "recovery_epochs": 0, "repeated_branch_count": 1,
        },
        criteria=(CriterionState(identifier, "Verify the target artifact.", "objective"),),
        activity=(activity,), workflows=(), convergence=(), candidate=activity,
        failed_approaches=(), legal_routes=())


def _frame(state, *, reverse_options=False, family="action_relation"):
    options = (("unrelated", "Does not support the objective."),
               ("advances_primary_goal", "Advances the goal."))
    if reverse_options:
        options = tuple(reversed(options))
    question = QuestionSpec(
        question_id=family, family=family, question_type="choice",
        instructions="Assess the supplied host facts.", criteria=options)
    return SimpleNamespace(state=state, questions=(question,))


def test_projection_removes_irrelevant_identifiers_and_sorts_options():
    first = _frame(_state())
    first_projection = laya_state_projection(first)
    second_state = _state(identifier="criterion-private-b")
    second_state = replace(
        second_state,
        task={**second_state.task, "active_segment": "different-private-segment"},
        situation={**second_state.situation, "blockers": [(
            "cfw_cause=other-private-cause;src=owl;codes=goal_drift;"
            "actions=inspect;criteria=criterion-private-b;"
            "refs=other-private-evidence;subject=other-private-subject")]})
    assert first_projection == laya_state_projection(_frame(second_state))
    assert "criterion-private-a" not in str(first_projection)
    assert "private-evidence" not in str(first_projection)
    assert _laya_questions(first) == _laya_questions(
        _frame(_state(), reverse_options=True))


def test_progress_projection_never_promotes_a_proposed_action_to_observed_fact():
    projected = laya_state_projection(
        _frame(_state(source="model_proposed"), family="progress_relation"))
    assert projected["host_observed_activity_present"] is False
    assert projected["host_observed_activities"] == []
    assert "Update the target artifact" not in str(projected)
    observed = laya_state_projection(
        _frame(_state(source="host_observed"), family="progress_relation"))
    assert "progress_class" not in str(observed)
    assert "host_declared_relation" not in str(observed)


def test_candidate_projection_does_not_expose_target_relation_label():
    projected = laya_state_projection(_frame(_state(), family="action_relation"))
    assert "host_declared_relation" not in str(projected)
    assert "ambiguous" not in str(projected)
    assert "goal_drift" not in str(projected)


def test_truncated_or_unknown_token_coverage_is_not_advisory_eligible():
    frame = _frame(_state())
    projection = laya_state_projection(frame)
    labels = {label for label, _description in frame.questions[0].criteria}
    result = SimpleNamespace(
        usage={"input_tokens": 50, "state_tokens": 40,
                "state_tokens_dropped": 4, "truncated": True,
                "truncated_questions": ["action_relation"]},
        probabilities={"action_relation": {label: 0.5 for label in labels}})
    diagnostics = _laya_diagnostics(
        frame, result, projection,
        {"status": "truncated", "state_tokens": 40,
         "tokens_dropped": 4, "truncated_questions": ["action_relation"]},
        12.5)
    validated = validate_laya_diagnostics(diagnostics)
    assert validated["truncation_status"] == "truncated"
    assert validated["coverage_status"] == "complete"
    assert validated["advisory_eligible"] is False

    unknown = _laya_diagnostics(
        frame, SimpleNamespace(probabilities={
            "action_relation": {label: 0.5 for label in labels}}),
        projection, {"status": "unknown", "state_tokens": None,
                     "tokens_dropped": None, "truncated_questions": []}, 2.0)
    assert unknown["truncation_status"] == "unknown"
    assert unknown["advisory_eligible"] is False


def test_missing_option_probabilities_are_recorded_as_incomplete_coverage():
    frame = _frame(_state())
    labels = tuple(label for label, _description in frame.questions[0].criteria)
    diagnostics = _laya_diagnostics(
        frame,
        SimpleNamespace(
            usage={"input_tokens": 50, "state_tokens": 40,
                    "state_tokens_dropped": 0, "truncated": False,
                    "truncated_questions": []},
            probabilities={"action_relation": {labels[0]: 1.0}}),
        laya_state_projection(frame),
        {"status": "complete", "state_tokens": 40,
         "tokens_dropped": 0, "truncated_questions": []}, 7.0)
    assert diagnostics["coverage_status"] == "incomplete"
    assert diagnostics["option_coverage"]["action_relation"]["returned_options"] == 1
    assert diagnostics["advisory_eligible"] is False


def test_native_laya_unknown_coverage_cannot_return_advisory_status():
    native_judgment = SimpleNamespace(backend_id="digitalpsychology-laya-native")
    result = SystemOneRuntime._mode_result(
        "advisory", native_judgment, ("action_relation",),
        question_ids=("action_relation",), inference_diagnostics=None)
    assert result.status == "shadow"
    assert result.reason == "laya_inference_coverage_unverified"
