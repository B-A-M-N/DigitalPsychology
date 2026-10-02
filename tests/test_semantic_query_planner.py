from __future__ import annotations

from lib.behavior_state import (FrameworkSignal, GoalItemSnapshot,
                                GoalSetSnapshot)
from lib.control.semantic_query_planner import PolicyAmbiguity, SemanticQueryPlanner
from lib.runtime.contracts import ActionCandidate, RuntimeObservation


def _candidate(*, mutating=True, reversible=True, external=False):
    return ActionCandidate(
        action_id="candidate-1", action_class="file_edit",
        tool_name="apply_patch", operation="edit_file",
        proposed_purpose="Implement the required visual criterion",
        affected_criterion_ids=("criterion-1",), mutating=mutating,
        reversible=reversible, external=external,
    )


def _goals(*, retries=0, actions_since_progress=0):
    return GoalSetSnapshot(1, (GoalItemSnapshot(
        "criterion-1", status="ACTIVE", equivalent_retry_count=retries,
        actions_since_progress=actions_since_progress),))


def _ambiguity(dimension, *, gap=0.1):
    return PolicyAmbiguity("continue", "diagnose", gap, (dimension,))


def test_routine_reversible_candidate_does_not_invoke_laya():
    plan = SemanticQueryPlanner().plan(
        RuntimeObservation(action_index=1, action_completed=False,
                          goal_set=_goals(), candidate_action=_candidate()))

    assert not plan.should_query
    assert plan.value_of_information == 0


def test_single_possible_retry_requests_only_retry_semantics():
    plan = SemanticQueryPlanner().plan(
        RuntimeObservation(action_index=1, action_completed=False,
                          goal_set=_goals(retries=1), candidate_action=_candidate()),
        policy_ambiguity=_ambiguity("retry_relation"),
        cfw_signals=(FrameworkSignal("fuse", "approach_failed"),))

    assert plan.question_id == "retry_relation"
    assert plan.value_of_information >= 0.65


def test_repeated_retry_is_deterministic_and_skips_laya():
    plan = SemanticQueryPlanner().plan(
        RuntimeObservation(action_index=3, action_completed=False,
                          goal_set=_goals(retries=2), candidate_action=_candidate()))

    assert not plan.should_query
    assert "already established" in plan.reason


def test_irreversible_or_external_candidate_requests_action_relation():
    plan = SemanticQueryPlanner().plan(
        RuntimeObservation(action_index=1, action_completed=False,
                          goal_set=_goals(),
                          candidate_action=_candidate(reversible=False)),
        policy_ambiguity=_ambiguity("action_relation"))

    assert plan.question_id == "action_relation"


def test_host_confirmed_progress_does_not_spend_laya_latency():
    plan = SemanticQueryPlanner().plan(
        RuntimeObservation(action_index=2, action_completed=True,
                          goal_set=_goals(), progress_made=True,
                          artifact_changed=True))

    assert not plan.should_query
    assert "host outcome" in plan.reason


def test_evidence_without_confirmed_progress_requests_progress_relation():
    plan = SemanticQueryPlanner().plan(
        RuntimeObservation(action_index=2, action_completed=True,
                          goal_set=_goals(), evidence_refs=("host-evidence",)),
        policy_ambiguity=_ambiguity("progress_relation"))

    assert plan.question_id == "progress_relation"


def test_goal_local_support_action_counter_can_raise_goal_drift_query():
    plan = SemanticQueryPlanner().plan(
        RuntimeObservation(action_index=4, action_completed=True,
                          goal_set=_goals(actions_since_progress=4)),
        policy_ambiguity=_ambiguity("goal_drift"),
        cfw_signals=(FrameworkSignal("flow", "support_work_saturated"),))

    assert plan.question_id == "goal_drift"


def test_critical_ward_signal_suppresses_semantic_query():
    plan = SemanticQueryPlanner().plan(
        RuntimeObservation(action_index=1, action_completed=False,
                          goal_set=_goals(),
                          candidate_action=_candidate(external=True)),
        policy_ambiguity=_ambiguity("action_relation"),
        cfw_signals=(FrameworkSignal("ward", "ward.action_veto", severity="critical"),))

    assert not plan.should_query
    assert "hard CFW authority" in plan.reason


def test_laya_is_skipped_when_candidate_margin_is_decisive():
    plan = SemanticQueryPlanner().plan(
        RuntimeObservation(action_index=1, action_completed=False,
                          goal_set=_goals(),
                          candidate_action=_candidate(reversible=False)),
        policy_ambiguity=_ambiguity("action_relation", gap=0.4))

    assert not plan.should_query
    assert "margin is too large" in plan.reason


def test_laya_is_skipped_when_answer_cannot_resolve_candidate_distinction():
    plan = SemanticQueryPlanner().plan(
        RuntimeObservation(action_index=1, action_completed=False,
                          goal_set=_goals(),
                          candidate_action=_candidate(reversible=False)),
        policy_ambiguity=_ambiguity("retry_relation"))

    assert not plan.should_query


def test_csw_eligible_statework_tie_requests_route_preference():
    plan = SemanticQueryPlanner().plan(
        RuntimeObservation(action_index=1),
        policy_ambiguity=PolicyAmbiguity(
            "repair", "diagnose", 0.0, ("statework_fit",)))

    assert plan.question_id == "route_preference"


def test_statework_fit_is_skipped_when_deterministic_margin_is_clear():
    plan = SemanticQueryPlanner().plan(
        RuntimeObservation(action_index=1),
        policy_ambiguity=PolicyAmbiguity(
            "repair", "diagnose", 0.6, ("statework_fit",)))

    assert not plan.should_query
