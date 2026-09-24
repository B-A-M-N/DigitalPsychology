#!/usr/bin/env python3
"""Regression tests for independent routing learning and lifecycle authority."""
from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lib.feedback_loop import Event, Guard, GuardCompiler  # noqa: E402
from lib.routing_lifecycle import RoutingLifecycleStore  # noqa: E402
from lib.routing_profiles import (  # noqa: E402
    build_experiment_plan, build_profile, build_routing_pack, derive_profiles,
    promote_profile, RoutingProfileError,
)
from lib.trajectories import build_trajectories  # noqa: E402


def candidate_and_plan():
    adjustment = {
        "route_type": "flow", "route": "flow", "statework_id": None,
        "disposition": "suppress", "condition": {},
        "evidence_refs": ["predeclared-hypothesis"], "confidence": 1.0,
        "expires_after_samples": 2,
    }
    context = {
        "task_family": "performance", "task_shape": "performance",
        "domain_tags": [], "phase": None, "environment": "test",
        "toolset": "fixture", "statework_versions": {},
        "framework_version": "1.4.0", "guard_pack_hash": "guard-pack",
    }
    candidate = build_profile(
        profile_id="routing-learning-regression",
        subject={"agent_instance_id": "agent-a", "model": "model-a", "harness": "test"},
        context=context, observations=[], routing_adjustments=[adjustment],
        evidence={"event_ids": ["hypothesis"], "trajectory_ids": [],
                  "source_hash": "hypothesis", "experiment_id": "experiment-1",
                  "candidate_adjustment": adjustment, "receipt_ref": None},
        criterion={"evaluator_version": "routing-outcome-v2", "minimum_samples": 2,
                   "minimum_effect": 0.1,
                   "confidence_rule": "independent trajectory effect lower confidence bound is non-negative"})
    plan = build_experiment_plan(
        experiment_id="experiment-1", candidate_profile_hash=candidate["profile_hash"],
        candidate_profile_id=candidate["profile_id"], candidate_adjustment=adjustment,
        eligibility={"field": "task_family", "equals": "performance"},
        target_evaluator="route-success-v1", control_policy_identity="control-policy-v1",
        treatment_policy_identity="treatment-policy-v1", canary_fraction=0.5)
    return candidate, plan


def route_event(index: int, session: str, task: str, outcome: str, plan: dict) -> Event:
    timestamp = (datetime(2026, 9, 1, tzinfo=timezone.utc) + timedelta(minutes=index)).isoformat().replace("+00:00", "Z")
    cohort = "treatment" if session.startswith("treatment") else "control"
    return Event(
        event_id=f"route-{session}-{index}", timestamp=timestamp, task_id=task,
        agent_id="agent-a", agent_instance_id="agent-a", session_id=session,
        attempt_id="attempt-1", category="decision", event_type="route_outcome",
        subject=f"subject:{task}", payload={
            "route": "flow", "route_type": "flow",
            "route_decision_id": f"decision-{session}",
            "evaluator_id": "route-success-v1",
            "outcome_source": "evaluator:route-success-v1",
            "outcome": outcome,
            "candidate_profile_hash": plan["candidate_profile_hash"],
            "candidate_adjustment_applied": cohort == "treatment",
            "comparison_context_hash": "comparison-1",
            "policy_hash": cohort,
        })


def main() -> int:
    candidate, plan = candidate_and_plan()
    events = []
    contexts = {}
    for session, cohort in (("control-session", "control"), ("treatment-session", "treatment")):
        task = f"task-{cohort}"
        for index in range(100):
            events.append(route_event(index, session, task,
                                      "bad" if cohort == "control" else "good", plan))
        contexts[f"{session}:{task}:attempt-1"] = {
            "agent_instance_id": "agent-a", "model": "model-a", "harness": "test",
            "task_family": "performance", "task_shape": "performance", "domain_tags": [],
            "phase": None, "environment": "test", "toolset": "fixture",
            "statework_versions": {}, "framework_version": "1.4.0",
            "guard_pack_hash": "guard-pack", "policy_hash": cohort,
            "routing_experiment_plan": plan, "routing_cohort": cohort,
            "candidate_profile_hash": plan["candidate_profile_hash"],
            "candidate_adjustment_applied": cohort == "treatment",
            "comparison_context_hash": "comparison-1",
        }
    assert derive_profiles(events, contexts, minimum_samples=2) == [], \
        "repeated route events must not become independent samples"
    print("ok: repeated events in one trajectory cannot satisfy sample minimum")

    try:
        promote_profile(candidate, "caller-picked-receipt")
        raise AssertionError("promotion accepted a caller-picked receipt")
    except RoutingProfileError:
        print("ok: promotion requires a pre-existing verifiable receipt")

    pack = build_routing_pack([candidate], source_revision="source-1")
    assert pack["semantic_hash"]
    print("ok: routing pack is content-addressed")

    with tempfile.TemporaryDirectory(prefix="routing-lifecycle-") as raw:
        updated = RoutingLifecycleStore(Path(raw) / "state.json").observe(
            candidate, [{"profile_hash": candidate["profile_hash"],
                         "trajectory_id": "post-1", "eligible": True}])
        assert updated["status"] == "candidate"
        updated = RoutingLifecycleStore(Path(raw) / "state.json").observe(
            candidate, [{"profile_hash": candidate["profile_hash"],
                         "trajectory_id": "post-2", "eligible": True}])
        assert updated["status"] == "stale"
        rolled = RoutingLifecycleStore(Path(raw) / "state.json").rollback(
            updated, reason="regression observed", receipt_ref="rollback-1")
        assert rolled["status"] == "rolled_back" and rolled["lifecycle_reason"] == "regression observed"
        rejected = RoutingLifecycleStore(Path(raw) / "state.json").reject(
            rolled, reason="operator rejected intervention", evidence_refs=["evt-reject"])
        assert rejected["status"] == "stale" and rejected["lifecycle_reason"] == "operator rejected intervention"
    print("ok: post-deployment trajectory budget expires a profile")

    compiler = GuardCompiler(token_budget=200)
    duplicate = compiler.compile([
        Guard(id="G-family-v1", family="G-family", version="1", status="active",
              target_behavior="one", rule="one", enforcement_key="one"),
        Guard(id="G-family-v2", family="G-family", version="2", status="active",
              target_behavior="two", rule="two", enforcement_key="two"),
    ], task_domains=[], task_shapes=[])
    assert duplicate.get("error") == "multiple effective guard versions without explicit supersession"
    print("ok: reference compiler rejects duplicate effective guard families")

    event_a = Event(event_id="interaction-a", timestamp="2026-09-01T00:00:00Z",
                    task_id="task-a", agent_id="agent-a", agent_instance_id="agent-a",
                    session_id="session-a", interaction_id="interaction-1", role="delegator",
                    category="decision", event_type="delegated")
    event_b = Event(event_id="interaction-b", timestamp="2026-09-01T00:01:00Z",
                    task_id="task-b", agent_id="agent-b", agent_instance_id="agent-b",
                    session_id="session-b", interaction_id="interaction-1", role="delegate",
                    category="decision", event_type="delegated")
    _episodes, _tasks, _sessions, agents = build_trajectories([event_a, event_b])
    assert all(profile.interaction_trajectories for profile in agents)
    print("ok: interaction trajectories and behavioral tendencies are materialized")
    print("ROUTING_LEARNING_TEST=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
