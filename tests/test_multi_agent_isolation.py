from datetime import datetime, timezone
import pytest

from lib.feedback_loop import Event, EventError
from lib.routing_profiles import RoutingProfileError, derive_profiles
from lib.trajectories import build_trajectories


def _event(agent, *, event_id="same", app="app", namespace="ns", role="delegator", interaction=None, outcome="bad"):
    return Event(event_id=event_id, timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat(), task_id="task", agent_id=agent, agent_instance_id=agent, session_id="same-session", attempt_id="attempt-1", behavioral_subject="subject", namespace_id=namespace, application_id=app, application_instance_id=app, category="decision", event_type="route_outcome", role=role, interaction_id=interaction, payload={"route": "flow", "route_type": "stage", "route_decision_id": "d", "outcome": outcome, "outcome_source": "evaluator:e", "evaluator_id": "e", "candidate_profile_hash": "x", "candidate_adjustment_applied": False, "policy_identity": "c", "experiment_plan_hash": "p", "comparison_context_hash": "c"})


def test_overlapping_ids_are_distinct_trajectories():
    _episodes, tasks, _sessions, agents = build_trajectories([_event("agent-1"), _event("agent-2")])
    assert len(tasks) == 2
    assert len(agents) == 2
    assert len({task.trajectory_id for task in tasks}) == 2


def test_same_event_id_across_origins_is_not_merged():
    _episodes, tasks, _sessions, _agents = build_trajectories([_event("agent-1"), _event("agent-2")])
    assert all(len(task.events) == 1 for task in tasks)


def test_mismatched_sidecar_identity_fails_closed():
    event = _event("agent-1")
    plan = {"plan_hash": "p", "target_evaluator": "e", "target_evaluator_version": "1", "target_evaluator_hash": "h", "candidate_profile_hash": "x", "treatment_policy_identity": "t", "control_policy_identity": "c", "holdout_requirements": {"minimum_samples": 1, "evaluators": []}}
    context = {"namespace_id": "ns", "application_instance_id": "app", "agent_instance_id": "agent-2", "session_id": "same-session", "task_id": "task", "attempt_id": "attempt-1", "behavioral_subject": "subject", "routing_experiment_plan": plan, "routing_cohort": "control", "comparison_context_hash": "c"}
    with pytest.raises(RoutingProfileError):
        derive_profiles([event], {"same-session:task:attempt-1": context}, minimum_samples=1)


def test_shared_interaction_cannot_cross_cohorts():
    from lib.routing_profiles import build_profile, build_experiment_plan
    adjustment = {"route_type": "stage", "route": "flow", "disposition": "suppress",
                  "condition": {}, "evidence_refs": ["predeclared"]}
    identity = {
        "namespace_id": "ns", "application_id": "app", "application_version": "1",
        "application_instance_id": "app", "provider_id": "p", "model_id": "m",
        "model_revision": "1", "model_capability_hash": "c", "harness_id": "h",
        "harness_version": "1", "agent_instance_id": "agent-1",
    }
    context = {"task_family": "performance", "task_shape": "performance", "domain_tags": [],
        "phase": None, "environment": "test", "toolset": "fixture",
        "statework_versions": {}, "framework_version": "f", "guard_pack_hash": "g",
        "role": "delegator", "interaction_id": "shared", "delegation_id": "d"}
    subject = identity
    candidate = build_profile(
        profile_id="shared-interaction", subject=subject, context=context, observations=[],
        routing_adjustments=[adjustment], evidence={"event_ids": ["e"], "trajectory_ids": [],
        "source_hash": "s", "experiment_id": "shared", "candidate_adjustment": adjustment,
        "receipt_ref": None}, criterion={"evaluator_version": "routing-outcome-v2",
        "minimum_samples": 1, "minimum_effect": 0.1,
        "confidence_rule": "independent trajectory effect lower confidence bound is non-negative"})
    plan = build_experiment_plan(experiment_id="shared", candidate_profile_hash=candidate["profile_hash"],
        candidate_profile_id="shared", candidate_adjustment=adjustment,
        eligibility={"field": "task_family", "equals": "performance"}, target_evaluator="e",
        control_policy_identity="c", treatment_policy_identity="t", canary_fraction=0.5,
        holdout_requirements={"evaluators": ["e@1.0.0"], "minimum_samples": 1})
    def event(agent, cohort, applied):
        data = {**identity, **context, "task_id": "task", "session_id": "session", "attempt_id": "attempt-1",
                "agent_id": agent, "agent_instance_id": agent, "behavioral_subject": "subject",
                "routing_experiment_plan": plan, "routing_cohort": cohort,
                "candidate_profile_hash": plan["candidate_profile_hash"],
                "candidate_adjustment_applied": applied, "policy_hash": "p",
                "comparison_context_hash": "p"}
        return Event(event_id=f"e-{agent}-{cohort}", timestamp="2026-01-01T00:00:00Z",
            task_id="task", agent_id=agent, agent_instance_id=agent, session_id="session",
            attempt_id="attempt-1", behavioral_subject="subject", namespace_id="ns",
            application_id="app", application_instance_id="app", category="decision",
            event_type="route_outcome", role="delegator", interaction_id="shared", delegation_id="d",
            payload={"route": "flow", "route_type": "stage", "route_decision_id": "d",
            "outcome": "bad" if cohort == "control" else "good", "outcome_source": "evaluator:e",
            "evaluator_id": "e", "evaluator_version": "1", "evaluator_hash": plan["target_evaluator_hash"],
            "candidate_profile_hash": plan["candidate_profile_hash"],
            "candidate_adjustment_applied": applied, "policy_identity": "t" if applied else "c",
            "experiment_plan_hash": plan["plan_hash"], "comparison_context_hash": "p"}), data
    events=[]; contexts={}
    for index, (agent, cohort, applied) in enumerate((
        ("agent-1", "control", False), ("agent-2", "control", False),
        ("agent-3", "treatment", True), ("agent-4", "treatment", True),
    )):
        ev, data = event(agent, cohort, applied)
        ev.event_id = f"e-{index}"
        data["candidate_profile_hash"] = plan["candidate_profile_hash"]
        events.append(ev)
        contexts[f"ns|app|{ev.agent_instance_id}|session|task|attempt-1|subject|delegator|shared|d"] = data
    with pytest.raises(RoutingProfileError, match="shared interaction"):
        derive_profiles(events, contexts, minimum_samples=1)
