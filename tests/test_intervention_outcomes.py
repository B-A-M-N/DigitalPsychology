from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.behavior_state import (BehaviorIntervention, GoalItemSnapshot,
                                GoalSetSnapshot, InterventionTarget)
from lib.behavior_state import BehaviorDirective
from lib.outcomes import (HostOutcomeFact, InterventionEventContext,
                          InterventionEvaluationFrame, InterventionEvaluator)
from lib.mcp_server import DigitalPsychologyService


SCOPE = "tenant/app/agent/session/task/attempt"


class _EvidenceAuthority:
    def __init__(self, facts):
        self.facts = facts

    def resolve_outcome_fact(self, evidence_ref: str, *, scope: str):
        return self.facts[evidence_ref]


def _intervention(*, baseline="unmet", concurrent=(), baseline_goal_set=None,
                  baseline_attention_last_seen=None):
    return BehaviorIntervention(
        intervention_id="intv-1", cause="goal_drift",
        expected_effect="produce criterion-directed progress",
        start_revision=4, scope=SCOPE, expires_after_actions=3,
        actions_elapsed=0,
        targets=(InterventionTarget("mode", "DIAGNOSE", "DELIVER"),),
        target_criteria=("criterion-visual",),
        baseline_criterion_status={"criterion-visual": baseline},
        baseline_action_index=10, evaluation_horizon_actions=3,
        concurrent_intervention_ids=tuple(concurrent),
        baseline_goal_set=baseline_goal_set,
        baseline_attention_last_seen=baseline_attention_last_seen or {},
    )


def _fact(reference, kind, source, *, criterion_id=None, revision=6):
    return HostOutcomeFact(reference, SCOPE, source, kind, revision, criterion_id,
                           action_index=12)


def _frame(*refs, competitors=(), complete=True, action_index=13,
           goal_set=None, attention_last_seen=None):
    return InterventionEvaluationFrame(
        scope=SCOPE, observed_revision=6, action_index=action_index,
        evaluation_complete=complete, applied_intervention_ids=("intv-1",),
        competing_intervention_ids=tuple(competitors), evidence_refs=tuple(refs),
        goal_set=goal_set, attention_last_seen=attention_last_seen or {},
    )


def test_validated_target_progress_with_applied_receipt_gets_supported_credit():
    facts = {
        "changed": _fact("changed", "artifact_changed", "host_artifact_observer"),
        "criterion": _fact("criterion", "criterion_verified", "host_validator",
                            criterion_id="criterion-visual"),
        "attempt": _fact("attempt", "materially_different_attempt", "cfw_observation"),
    }

    outcome = InterventionEvaluator().assess(
        _intervention(), _frame("changed", "criterion", "attempt"),
        authority=_EvidenceAuthority(facts),
    )

    assert outcome.outcome == "effective"
    assert outcome.attribution == "supported"
    assert outcome.goal_progress is True
    assert outcome.criterion_ids_progressed == ("criterion-visual",)
    assert outcome.artifact_changed is True
    assert outcome.repeated_approach is False


def test_progress_during_overlapping_interventions_is_not_credited_to_one():
    facts = {"criterion": _fact(
        "criterion", "criterion_verified", "host_validator",
        criterion_id="criterion-visual")}

    outcome = InterventionEvaluator().assess(
        _intervention(), _frame("criterion", competitors=("intv-other",)),
        authority=_EvidenceAuthority(facts),
    )

    assert outcome.outcome == "unattributed"
    assert outcome.attribution == "confounded"
    assert outcome.goal_progress is True


def test_host_comparison_can_identify_progress_unrelated_to_intervention():
    facts = {
        "criterion": _fact(
            "criterion", "criterion_verified", "host_validator",
            criterion_id="criterion-visual"),
        "comparison": HostOutcomeFact(
            "comparison", SCOPE, "host_experiment_evaluator",
            "progress_not_attributed", observed_revision=7,
            action_index=12, intervention_id="intv-1",
            comparison_id="comparison-42",
            comparison_design="matched_baseline_holdout"),
    }

    outcome = InterventionEvaluator().assess(
        _intervention(), _frame("criterion", "comparison"),
        authority=_EvidenceAuthority(facts),
    )

    assert outcome.outcome == "unattributed"
    assert outcome.attribution == "not_attributed"
    assert outcome.goal_progress is True
    assert outcome.comparison_id == "comparison-42"


def test_unknown_baseline_or_missing_applied_receipt_cannot_claim_effectiveness():
    facts = {"criterion": _fact(
        "criterion", "criterion_verified", "host_validator",
        criterion_id="criterion-visual")}
    evaluator = InterventionEvaluator()

    unknown_baseline = evaluator.assess(
        _intervention(baseline="unknown"), _frame("criterion"),
        authority=_EvidenceAuthority(facts),
    )
    no_receipt = evaluator.assess(
        _intervention(), InterventionEvaluationFrame(
            scope=SCOPE, observed_revision=6, action_index=13,
            evaluation_complete=True, applied_intervention_ids=(),
            evidence_refs=("criterion",)),
        authority=_EvidenceAuthority(facts),
    )

    assert unknown_baseline.outcome == "inconclusive"
    assert unknown_baseline.attribution == "insufficient"
    assert no_receipt.outcome == "unattributed"
    assert no_receipt.attribution == "insufficient"


def test_complete_window_with_host_rejection_of_target_is_ineffective():
    facts = {
        "rejected": _fact("rejected", "criterion_rejected", "host_validator",
                           criterion_id="criterion-visual"),
        "artifact": _fact("artifact", "artifact_changed", "host_artifact_observer"),
    }

    outcome = InterventionEvaluator().assess(
        _intervention(), _frame("rejected", "artifact"),
        authority=_EvidenceAuthority(facts),
    )

    assert outcome.outcome == "ineffective"
    assert outcome.goal_progress is False
    assert outcome.attribution == "supported"


def test_agent_assertion_is_not_an_authorized_outcome_source():
    with pytest.raises(ValueError, match="not authorized"):
        _fact("agent-says-fixed", "criterion_verified", "agent")


def test_evidence_authority_cannot_return_another_scope_or_pre_intervention_fact():
    intervention = _intervention()
    wrong_scope = HostOutcomeFact(
        "criterion", "other-scope", "host_validator", "criterion_verified", 6,
        "criterion-visual", action_index=12)
    stale = HostOutcomeFact(
        "criterion", SCOPE, "host_validator", "criterion_verified", 4,
        "criterion-visual", action_index=10)
    for fact in (wrong_scope, stale):
        with pytest.raises(ValueError):
            InterventionEvaluator().assess(
                intervention, _frame("criterion"),
                authority=_EvidenceAuthority({"criterion": fact}),
            )


def test_evaluation_horizon_cannot_be_closed_early():
    with pytest.raises(ValueError, match="before its action horizon"):
        InterventionEvaluator().assess(
            _intervention(), _frame(complete=True, action_index=11),
            authority=_EvidenceAuthority({}),
        )


def test_task_set_outcome_requires_target_progress_without_regression():
    before = GoalSetSnapshot(1, (
        GoalItemSnapshot("criterion-visual", status="READY"),
        GoalItemSnapshot("criterion-docs", status="READY"),
    ))
    after = GoalSetSnapshot(2, (
        GoalItemSnapshot("criterion-visual", status="VERIFIED"),
        GoalItemSnapshot("criterion-docs", status="READY"),
    ))
    attention = {"criterion-visual": 10, "criterion-docs": 10}
    intervention = _intervention(
        baseline_goal_set=before, baseline_attention_last_seen=attention)

    outcome = InterventionEvaluator().assess(
        intervention,
        _frame(goal_set=after, attention_last_seen=attention),
        authority=_EvidenceAuthority({}),
    )

    assert outcome.outcome == "effective"
    assert outcome.target_criteria == ("criterion-visual",)
    assert outcome.task_set_outcome is not None
    assert outcome.task_set_outcome.targeted_progress_delta == 1
    assert outcome.task_set_outcome.total_required_remaining_before == 2
    assert outcome.task_set_outcome.total_required_remaining_after == 1
    assert outcome.task_set_outcome.newly_verified == ("criterion-visual",)
    assert not outcome.task_set_outcome.regressions


def test_task_set_regression_blocks_credit_even_when_target_progresses():
    before = GoalSetSnapshot(1, (
        GoalItemSnapshot("criterion-visual", status="READY"),
        GoalItemSnapshot("criterion-auth", status="VERIFIED"),
    ))
    after = GoalSetSnapshot(2, (
        GoalItemSnapshot("criterion-visual", status="VERIFIED"),
        GoalItemSnapshot("criterion-auth", status="ACTIVE"),
    ))
    attention = {"criterion-visual": 10, "criterion-auth": 10}

    outcome = InterventionEvaluator().assess(
        _intervention(baseline_goal_set=before,
                      baseline_attention_last_seen=attention),
        _frame(goal_set=after, attention_last_seen=attention),
        authority=_EvidenceAuthority({}),
    )

    assert outcome.outcome == "regressed"
    assert outcome.task_set_outcome is not None
    assert outcome.task_set_outcome.regressions == ("criterion-auth",)


def test_progress_on_unrelated_criterion_does_not_credit_targeted_intervention():
    before = GoalSetSnapshot(1, (
        GoalItemSnapshot("criterion-visual", status="READY"),
        GoalItemSnapshot("criterion-auth", status="READY"),
    ))
    after = GoalSetSnapshot(2, (
        GoalItemSnapshot("criterion-visual", status="READY"),
        GoalItemSnapshot("criterion-auth", status="VERIFIED"),
    ))
    attention = {"criterion-visual": 10, "criterion-auth": 10}

    outcome = InterventionEvaluator().assess(
        _intervention(baseline_goal_set=before,
                      baseline_attention_last_seen=attention),
        _frame(goal_set=after, attention_last_seen=attention),
        authority=_EvidenceAuthority({}),
    )

    assert outcome.outcome == "inconclusive"
    assert outcome.goal_progress is None
    assert outcome.target_criteria == ("criterion-visual",)
    assert outcome.task_set_outcome is not None
    assert outcome.task_set_outcome.targeted_progress_delta == 0
    assert outcome.task_set_outcome.newly_verified == ("criterion-auth",)


def test_host_application_and_assessment_are_persisted_as_authenticated_events(tmp_path):
    service = DigitalPsychologyService(event_db=tmp_path / "events.sqlite3")
    intervention = _intervention()
    directive = BehaviorDirective(
        directive_id="dir-1", operation="set_mode", target="mode", value="DELIVER",
        cause=intervention.cause, expected_effect=intervention.expected_effect,
        scope=SCOPE, expires_after_actions=3,
        intervention_id=intervention.intervention_id,
        expires_at_action=3,
    )
    context = InterventionEventContext(
        namespace_id="tenant-a", application_id="assistant",
        application_instance_id="assistant-1", agent_instance_id="agent-1",
        session_id="session-1", task_id="task-1", attempt_id="attempt-1",
        segment_id="segment-0", agent_id="agent-1",
        behavioral_subject="task-1", goal_contract_hash="a" * 64,
    )
    facts = {"criterion": _fact(
        "criterion", "criterion_verified", "host_validator",
        criterion_id="criterion-visual")}
    try:
        applied_event_id = service.record_intervention_application(
            intervention, (directive,), (directive.directive_id,), event_context=context)
        outcome = InterventionEvaluator().assess(
            intervention, _frame("criterion"), authority=_EvidenceAuthority(facts))
        outcome_event_id = service.record_intervention_outcome(outcome, event_context=context)
        rows = service._sink._conn.execute(
            "SELECT event_id, payload FROM events ORDER BY row_id").fetchall()
        authenticated = service._sink._conn.execute(
            "SELECT event_id FROM authenticated_events ORDER BY event_id").fetchall()
    finally:
        service.close()

    events = {event_id: json.loads(payload) for event_id, payload in rows}
    assert applied_event_id in events and outcome_event_id in events
    assert events[applied_event_id]["event_type"] == "behavior_intervention_applied"
    assert events[applied_event_id]["payload"]["behavior_intervention_status"] == "applied"
    assessed = events[outcome_event_id]
    assert assessed["actor_id"] == "digitalpsychology_runtime"
    assert assessed["authority_source"] == "host_outcome_evaluator"
    assert assessed["payload"]["behavior_intervention_status"] == "effective"
    assert assessed["payload"]["behavior_intervention_goal_progress"] is True
    assert assessed["evidence_refs"] == ["criterion"]
    assert {row[0] for row in authenticated} >= {applied_event_id, outcome_event_id}


def test_new_directive_requires_absolute_expiry_and_legacy_decoder_is_explicit():
    values = {
        "directive_id": "dir-expiry", "operation": "set_mode",
        "target": "mode", "value": "RECOVER", "cause": "equivalent_retry",
        "expected_effect": "Change behavior for a bounded action window.",
        "scope": SCOPE, "expires_after_actions": 3,
        "intervention_id": "intervention-expiry",
    }
    with pytest.raises(TypeError, match="expires_at_action"):
        BehaviorDirective(**values)

    pre_action = BehaviorDirective.from_legacy_mapping(
        values, host_action_index=4, boundary_kind="pre_action")
    post_action = BehaviorDirective.from_legacy_mapping(
        values, host_action_index=4, boundary_kind="post_action")
    assert pre_action.expires_at_action == 6
    assert post_action.expires_at_action == 7
