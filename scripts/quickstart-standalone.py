#!/usr/bin/env python3
"""Five-minute DP-only experiment: independent trajectories to promotion."""
from __future__ import annotations
import json, sys, tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from lib.feedback_loop import Event
from lib.routing_lifecycle import RoutingLifecycleStore
from lib.slow_loop import BoundedLearningController
from lib.routing_profiles import build_experiment_plan, build_profile, promote_profile

SUBJECT = {
    "namespace_id": "readme", "application_id": "dp-quickstart",
    "application_version": "1.0.0", "application_instance_id": "experiment-1",
    "provider_id": "fixture", "model_id": "fixture-model", "model_revision": "1",
    "model_capability_hash": "fixture-capability", "harness_id": "fixture-harness",
    "harness_version": "1", "agent_instance_id": "agent-1",
    "model": "fixture-model", "harness": "fixture-harness",
}
CONTEXT = {"task_family": "performance", "task_shape": "performance",
           "domain_tags": [], "phase": None, "environment": "quickstart",
           "toolset": "fixture", "statework_versions": {},
           "framework_version": "fixture", "guard_pack_hash": "fixture-guard"}
ADJUSTMENT = {"route_type": "stage", "route": "flow", "statework_id": None,
              "disposition": "suppress", "condition": {},
              "evidence_refs": ["predeclared-experiment"], "confidence": 1.0,
              "expires_after_samples": 10}
candidate = build_profile(
    profile_id="readme-routing-experiment",
    subject=SUBJECT, context=CONTEXT, observations=[],
    routing_adjustments=[ADJUSTMENT],
    evidence={"event_ids": ["predeclared"], "trajectory_ids": [],
              "source_hash": "predeclared-source", "experiment_id": "readme-experiment",
              "candidate_adjustment": ADJUSTMENT, "receipt_ref": None},
    criterion={"evaluator_version": "routing-outcome-v2",
               "minimum_samples": 2, "minimum_effect": 0.1,
               "confidence_rule": "independent trajectory effect lower confidence bound is non-negative"})
plan = build_experiment_plan(
    experiment_id="readme-experiment", candidate_profile_hash=candidate["profile_hash"],
    candidate_profile_id=candidate["profile_id"], candidate_adjustment=ADJUSTMENT,
    eligibility={"field": "task_family", "equals": "performance"},
    target_evaluator="routing-outcome-v1",
    control_policy_identity="fixture-control", treatment_policy_identity="fixture-treatment",
    holdout_requirements={"evaluators": ["routing-outcome-v1@1.0.0"],
                          "minimum_samples": 2,
                          "holdout_task_ids": [f"holdout-{i}" for i in range(10)]},
    canary_fraction=0.5)

events, contexts = [], {}
base = datetime(2026, 9, 23, tzinfo=timezone.utc)
cohorts = (["control"] * 10) + (["treatment"] * 10) + (["holdout"] * 10)
for index, cohort in enumerate(cohorts):
    task = f"holdout-{cohorts[:index].count('holdout')}" if cohort == "holdout" else f"{cohort}-{cohorts[:index].count(cohort)}"
    session = f"session-{index}"
    applied = cohort == "treatment"
    identity = "fixture-treatment" if applied else "fixture-control"
    events.append(Event(
        event_id=f"event-{index}", timestamp=(base + timedelta(minutes=index)).isoformat().replace("+00:00", "Z"),
        task_id=task, agent_id="agent", agent_instance_id="agent-1", session_id=session,
        attempt_id="attempt-1", category="decision", event_type="route_outcome",
        namespace_id=SUBJECT["namespace_id"], application_id=SUBJECT["application_id"],
        application_instance_id=SUBJECT["application_instance_id"],
        provider_id="fixture", model_id="fixture-model", model_revision="1",
        model_capability_hash="fixture-capability", harness_id="fixture-harness",
        harness_version="1", behavioral_subject="subject-1", subject="subject-1",
        payload={"route": "flow", "route_type": "stage", "route_decision_id": f"decision-{index}",
                 "evaluator_id": plan["target_evaluator"],
                 "evaluator_version": plan["target_evaluator_version"],
                 "evaluator_hash": plan["target_evaluator_hash"],
                 "outcome_source": f"evaluator:{plan['target_evaluator']}",
                 "outcome": "bad" if cohort == "control" else "good",
                 "candidate_profile_hash": plan["candidate_profile_hash"],
                 "candidate_adjustment_applied": applied,
                 "policy_identity": identity, "experiment_plan_hash": plan["plan_hash"],
                 "policy_hash": identity, "comparison_context_hash": "comparison-fixture"}))
    contexts[f"{session}:{task}:attempt-1"] = {
        **SUBJECT, **CONTEXT, "task_id": task, "session_id": session,
        "policy_hash": identity, "routing_experiment_plan": plan,
        "routing_cohort": cohort, "candidate_profile_hash": plan["candidate_profile_hash"],
        "candidate_adjustment_applied": applied,
        "comparison_context_hash": "comparison-fixture",
    }

with tempfile.TemporaryDirectory(prefix="dp-quickstart-") as raw:
    lifecycle = RoutingLifecycleStore(Path(raw) / "lifecycle.json")
    report = BoundedLearningController(lifecycle=lifecycle, minimum_samples=2).run(events, contexts)
    print("independent trajectories:", len(events))
    print("candidates:", len(report.candidates))
    if report.candidates:
        receipt = report.candidates[0]["evidence"]["experiment_receipt"]
        print("receipt decision:", receipt["decision"])
    print("promoted profiles:", len(report.promoted))
    print("rejections:", report.rejected)
