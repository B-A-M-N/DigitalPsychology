from __future__ import annotations

from pathlib import Path
import sys
from dataclasses import replace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.feedback_loop import Event
from lib.slow_loop import collect_intervention_credit_assignments


def _event(event_id, event_type, *, actor="digitalpsychology_runtime",
           authority="digitalpsychology_runtime", status=None, attribution=None,
           evidence_refs=(), goal_progress=None, preventive_utility=()):
    payload = {"behavior_intervention_id": "intv-a"}
    if event_type == "behavior_intervention_applied":
        payload.update({
            "behavior_intervention_status": "applied",
            "behavior_intervention_cause": "goal_drift",
            "behavior_intervention_target_criteria": ["criterion-ui"],
        })
    else:
        payload.update({
            "behavior_intervention_status": status,
            "behavior_intervention_attribution": attribution,
            "behavior_intervention_goal_progress": goal_progress,
            "behavior_intervention_criterion_progress": ["criterion-ui"]
            if goal_progress else [],
            "behavior_intervention_artifact_changed": True,
            "behavior_intervention_repeated_approach": False,
            "behavior_intervention_verification_quality": "improved",
            "behavior_intervention_preventive_utility": list(preventive_utility),
        })
    return Event(
        event_id=event_id, timestamp="2026-10-01T12:00:00Z",
        namespace_id="tenant", application_id="app",
        application_instance_id="app-instance", agent_instance_id="agent-instance",
        session_id="session", task_id="task", attempt_id="attempt",
        segment_id="segment", agent_id="agent", actor_id=actor,
        behavioral_subject="task", category=("decision" if status is None else "validation"),
        event_type=event_type, authority_source=authority,
        scope="tenant/app/agent/session/task/attempt",
        goal_contract_hash="goal-hash", evidence_refs=list(evidence_refs), payload=payload,
    )


def test_slow_loop_joins_only_authenticated_isolated_supported_pairs():
    applied = _event("apply-1", "behavior_intervention_applied")
    effective = _event(
        "outcome-1", "behavior_intervention_assessed",
        authority="host_outcome_evaluator", status="effective",
        attribution="supported", evidence_refs=("csw-evidence-1",), goal_progress=True)
    forged = _event(
        "outcome-forged", "behavior_intervention_assessed", actor="agent",
        authority="host_outcome_evaluator", status="effective",
        attribution="supported", evidence_refs=("fake-evidence",), goal_progress=True)

    pairs, observations = collect_intervention_credit_assignments(
        [applied, effective, forged])

    assert len(pairs) == 1
    assert pairs[0]["outcome"] == "effective"
    assert pairs[0]["independently_supported"] is True
    assert observations == [{
        "cause": "goal_drift", "target_criteria": ["criterion-ui"],
        "supported_trial_count": 1, "effective_count": 1,
        "ineffective_count": 0, "regressed_count": 0,
        "effective_rate": 1.0, "disposition": "observational_only",
        "promotion_requires": "independent baseline, intervention, and holdout cohorts",
    }]


def test_same_intervention_id_cannot_join_across_runtime_sessions():
    applied = _event("apply-1", "behavior_intervention_applied")
    outcome = _event(
        "outcome-other-session", "behavior_intervention_assessed",
        authority="host_outcome_evaluator", status="effective",
        attribution="supported", evidence_refs=("csw-evidence-1",), goal_progress=True)
    outcome = replace(outcome, session_id="different-session")

    pairs, observations = collect_intervention_credit_assignments([applied, outcome])

    assert pairs == []
    assert observations == []


def test_slow_loop_preserves_independently_supported_preventive_utility():
    applied = _event("apply-preventive", "behavior_intervention_applied")
    preventive = _event(
        "outcome-preventive", "behavior_intervention_assessed",
        authority="host_outcome_evaluator", status="effective",
        attribution="supported", evidence_refs=("csw-evidence-2",),
        goal_progress=False,
        preventive_utility=("prevented_invalid_completion",))

    pairs, _ = collect_intervention_credit_assignments([applied, preventive])

    assert len(pairs) == 1
    assert pairs[0]["outcome"] == "effective"
    assert pairs[0]["goal_progress"] is False
    assert pairs[0]["preventive_utility"] == ["prevented_invalid_completion"]
