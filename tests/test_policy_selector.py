from dataclasses import replace

import pytest

from lib.behavior_state import (BehaviorState, CSWBehaviorSnapshot,
                                StateWorkOption)
from lib.control.policy_selector import (
    BehaviorCandidate, BehaviorConstraintResolver, BehaviorPolicyConstraints,
    BehaviorPolicyPrior, BehaviorPolicySelector, candidate_id_for,
)
from lib.routing_profiles import (
    _canonical, build_profile, profile_semantic_hash, promote_profile,
)
import hashlib


ALL_FRAMEWORKS = ("owl", "anchor", "fuse", "ward", "flow", "sispis", "dox")


def candidate(state, *, reason_codes=(), cause="test", expected_effect="test effect",
              stateworks=()):
    return BehaviorCandidate.from_state(
        state, focus_criteria=(), statework_preferences=stateworks,
        reason_codes=reason_codes, cause=cause, expected_effect=expected_effect)


def test_selector_keeps_deterministic_recommendation_as_no_prior_fallback():
    initial = BehaviorState()
    continue_candidate = candidate(initial, reason_codes=("continue_current",))
    recovery = replace(
        initial, cognitive_mode="RECOVER",
        framework_modes={**dict(initial.framework_modes), "owl": "reset"},
        exploration_budget="low", retry_policy="materially_different_only")
    recommended = candidate(recovery, reason_codes=("deterministic_rule",))

    selection = BehaviorPolicySelector().select(
        (continue_candidate, recommended),
        constraints=BehaviorPolicyConstraints(
            current_state=initial, available_frameworks=ALL_FRAMEWORKS,
            goal_set=None),
        csw_snapshot=CSWBehaviorSnapshot(revision=0), goal_set=None,
        policy_context={}, priors=())

    assert selection.candidate.candidate_id == recommended.candidate_id
    assert selection.score.evidence_source == "deterministic_controller_fallback"


def test_host_required_mode_filters_otherwise_valid_candidates():
    initial = BehaviorState()
    deliver = candidate(initial)
    reinspect = candidate(replace(initial, cognitive_mode="REINSPECT"))
    selection = BehaviorPolicySelector().select(
        (deliver, reinspect),
        constraints=BehaviorPolicyConstraints(
            current_state=initial, available_frameworks=ALL_FRAMEWORKS,
            goal_set=None, required_mode="REINSPECT"),
        csw_snapshot=CSWBehaviorSnapshot(revision=0), goal_set=None,
        policy_context={}, priors=())

    assert selection.candidate.candidate_id == reinspect.candidate_id
    assert selection.rejected[deliver.candidate_id] == "host_or_goal_required_mode"


def test_constraint_resolver_keeps_ward_floor_and_csw_eligibility_hard():
    state = BehaviorState()
    frameworks = {**dict(state.framework_modes), "ward": "off"}
    controls = {name: getattr(state, name) for name in (
        "verification_intensity", "exploration_budget", "retry_policy",
        "recovery_level", "context_strategy", "response_strategy")}
    ward_off = BehaviorCandidate(
        candidate_id=candidate_id_for(
            cognitive_mode=state.cognitive_mode, framework_modes=frameworks,
            controls=controls, focus_scope="current_attention",
            statework_preferences=()),
        cognitive_mode=state.cognitive_mode, framework_modes=frameworks,
        controls=controls, focus_criteria=(), statework_preferences=(),
        cause="test", expected_effect="test")
    unavailable_work = candidate(state, stateworks=("not-eligible",))
    resolver = BehaviorConstraintResolver()
    constraints = BehaviorPolicyConstraints(
        current_state=state, available_frameworks=ALL_FRAMEWORKS,
        goal_set=None)

    assert resolver.reject_reason(
        ward_off, constraints, CSWBehaviorSnapshot(revision=0)) == "ward_hard_floor"
    assert resolver.reject_reason(
        unavailable_work, constraints,
        CSWBehaviorSnapshot(revision=0,
                            eligible_stateworks=(StateWorkOption("other-work"),))) == "statework_not_eligible"


def test_policy_prior_cannot_be_forged_or_loaded_from_unpromoted_profile():
    state = BehaviorState()
    candidate_value = candidate(state)
    with pytest.raises(ValueError, match="validated promoted profile"):
        BehaviorPolicyPrior(
            candidate=candidate_value, profile_id="p", profile_hash="h",
            receipt_id="r", experiment_id="e", context={}, subject={},
            condition={}, sharing_scope="agent_private",
            correct_progress_probability=0.9, regression_probability=0.0,
            repeated_failure_probability=None, sample_count=10, holdout_count=5)

    assert BehaviorPolicyPrior.from_promoted_profile({}) == ()


def promoted_policy_profile(policy):
    subject = {
        "namespace_id": "ns", "application_id": "app",
        "application_version": "1", "application_instance_id": "instance",
        "provider_id": "provider", "model_id": "model",
        "model_revision": "revision", "model_capability_hash": "capabilities",
        "harness_id": "harness", "harness_version": "1",
        "agent_instance_id": "agent",
    }
    context = {"task_family": "coding"}
    adjustment = {
        "route_type": "stage", "route": "flow", "disposition": "prefer",
        "condition": {"field": "task_family", "equals": "coding"},
        "evidence_refs": ["evidence-policy"], "behavior_policy": policy,
    }
    profile = build_profile(
        profile_id="promoted-behavior-policy", subject=subject, context=context,
        observations=[], routing_adjustments=[adjustment],
        evidence={"event_ids": ["event"], "trajectory_ids": ["treatment-1"],
                  "source_hash": "source-evidence", "experiment_id": "experiment",
                  "candidate_adjustment": adjustment, "receipt_ref": None},
        criterion={"evaluator_version": "host-objective-v1", "minimum_samples": 1,
                   "minimum_effect": 0.1, "confidence_rule": "holdout regression-free"})
    receipt = {
        "receipt_id": "receipt-policy", "kind": "routing_experiment",
        "receipt_version": "1.0.0", "experiment_id": "experiment",
        "experiment_plan_hash": "a" * 64,
        "profile_candidate_hash": profile_semantic_hash(profile),
        "source_evidence_hash": "source-evidence",
        "candidate_adjustment": adjustment,
        "control_trajectory_ids": ["control-1"],
        "treatment_trajectory_ids": [f"treatment-{index}" for index in range(20)],
        "policy_hashes": ["policy-hash"],
        "policy_identities": {"control": "control", "treatment": "treatment"},
        "comparison_context_hash": "comparison",
        "evaluator_id": "host-objective", "evaluator_version": "1",
        "evaluator_hash": "b" * 64,
        "control_bad_rate": 0.5, "treatment_bad_rate": 0.1, "effect": 0.4,
        "confidence_interval": [0.01, 0.7], "minimum_samples": 1,
        "minimum_holdout_samples": 1, "minimum_effect": 0.1,
        "regressions": [], "holdout_trajectory_ids": ["holdout-1"],
        "holdout_outcomes": [["holdout-1", "PASS"]],
        "holdout_evaluations": [["holdout-1", "host-objective", "1", "PASS"]],
        "holdout_evaluators": ["host-objective@1"],
        "holdout_max_not_applicable_fraction": 0.25,
        "control_max_not_applicable_fraction": 0.25,
        "treatment_max_not_applicable_fraction": 0.25,
        "decision": "pass",
    }
    receipt["receipt_hash"] = hashlib.sha256(_canonical(receipt).encode()).hexdigest()
    return promote_profile(profile, receipt, status="active")


def test_validated_progress_evidence_beats_lower_estimated_cost():
    state = BehaviorState()
    evidenced = candidate(state)
    cheaper = candidate(replace(
        state, verification_intensity="passive", exploration_budget="low",
        context_strategy="compact"))
    profile = promoted_policy_profile(evidenced.policy_mapping())
    priors = BehaviorPolicyPrior.from_promoted_profile(profile)
    assert len(priors) == 1

    policy_context = {
        **dict(profile["subject"]), **dict(profile["context"]),
    }
    selection = BehaviorPolicySelector().select(
        (evidenced, cheaper),
        constraints=BehaviorPolicyConstraints(
            current_state=state, available_frameworks=ALL_FRAMEWORKS,
            goal_set=None),
        csw_snapshot=CSWBehaviorSnapshot(revision=0), goal_set=None,
        policy_context=policy_context, priors=priors)

    assert selection.candidate.candidate_id == evidenced.candidate_id
    assert selection.score.correct_progress_probability > 0.5
    assert selection.score.estimated_cost > 0.25
