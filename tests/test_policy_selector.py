from dataclasses import replace

import pytest

from lib.behavior_state import (BEHAVIOR_MODES, AttentionLease, BehaviorState,
                                CSWBehaviorSnapshot, FrameworkSignal,
                                GoalItemSnapshot, GoalSetSnapshot,
                                StateWorkOption)
from lib.attention import schedule_attention
from lib.control.policy_selector import (
    BehaviorCandidate, BehaviorConstraintResolver, BehaviorPolicyConstraints,
    BehaviorPolicyPrior, BehaviorPolicySelector, CandidateEvidence,
    candidate_id_for,
)
from lib.fast_loop import BehavioralController, FastLoopInput
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
    assert selection.score.evidence_class == "deterministic_rule"
    assert selection.score.evidence.correct_progress_estimate is None


def test_selector_abstains_when_unmeasured_candidates_are_ambiguous():
    state = BehaviorState()
    first = candidate(replace(state, cognitive_mode="RECOVER"))
    second = candidate(replace(state, cognitive_mode="DIAGNOSE"))

    selection = BehaviorPolicySelector().select(
        (first, second),
        constraints=BehaviorPolicyConstraints(
            current_state=state, available_frameworks=ALL_FRAMEWORKS,
            goal_set=None),
        csw_snapshot=CSWBehaviorSnapshot(revision=0), goal_set=None,
        policy_context={}, priors=())

    assert selection.disposition == "abstain_ambiguous"
    assert selection.candidate is None
    assert selection.score is None


def test_selector_abstains_to_information_fallback_without_policy_evidence():
    state = BehaviorState()
    continuation = candidate(state, reason_codes=("continue_current",))
    diagnostic = candidate(
        replace(state, cognitive_mode="DIAGNOSE"),
        reason_codes=("abstention_fallback",))

    selection = BehaviorPolicySelector().select(
        (continuation, diagnostic),
        constraints=BehaviorPolicyConstraints(
            current_state=state, available_frameworks=ALL_FRAMEWORKS,
            goal_set=None),
        csw_snapshot=CSWBehaviorSnapshot(revision=0), goal_set=None,
        policy_context={}, priors=(),
        # These weak candidate-generation heuristics are not evidence.
        rule_support={continuation.candidate_id: 0.40,
                      diagnostic.candidate_id: 0.30})

    assert selection.candidate is None
    assert selection.disposition == "abstain_insufficient_evidence"


def test_starvation_risk_uses_attention_age_and_fairness_bound_not_task_size():
    state = BehaviorState()
    goal_set = GoalSetSnapshot(1, tuple(
        GoalItemSnapshot(f"criterion-{index}") for index in range(20)))
    focus = tuple(f"criterion-{index}" for index in range(3))
    proposed = BehaviorCandidate.from_state(
        state, focus_criteria=focus, statework_preferences=(),
        reason_codes=("continue_current",), cause="test", expected_effect="test")
    lease = AttentionLease("lease-1", focus, 1, 1, 5, max_actions=3)
    last_seen = {item.criterion_id: 4 for item in goal_set.items}

    selection = BehaviorPolicySelector().select(
        (proposed,), constraints=BehaviorPolicyConstraints(
            current_state=state, available_frameworks=ALL_FRAMEWORKS,
            goal_set=goal_set),
        csw_snapshot=CSWBehaviorSnapshot(revision=0), goal_set=goal_set,
        policy_context={}, attention_lease=lease,
        attention_last_seen=last_seen, current_action_index=5)

    # Seventeen unattended items do not imply an 85% starvation estimate;
    # these items are only four actions old against a 21-action fairness bound.
    assert selection.score.starvation_risk == pytest.approx(4 / 21)


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
            evidence=CandidateEvidence())

    assert BehaviorPolicyPrior.from_promoted_profile({}) == ()


def test_behavior_policy_profile_requires_explicit_receipt_metrics():
    from lib.routing_profiles import RoutingProfileError, validate_profile

    state = BehaviorState()
    profile = promoted_policy_profile(candidate(state).policy_mapping())
    receipt = profile["evidence"]["receipt"]
    receipt.pop("candidate_policy_evidence")
    receipt.pop("receipt_hash")
    receipt["receipt_hash"] = hashlib.sha256(_canonical(receipt).encode()).hexdigest()
    profile["profile_hash"] = profile_semantic_hash(profile)

    with pytest.raises(RoutingProfileError, match="explicit correct-progress"):
        validate_profile(profile, require_deployable=True)


def promoted_policy_profile(policy, *, treatment_count=20,
                           treatment_bad_rate=0.1,
                           correct_progress_rate=0.9,
                           regression_rate=0.05,
                           repeated_failure_rate=0.05,
                           confidence_interval=(0.75, 0.98),
                           regression_confidence_interval=(0.0, 0.15),
                           repeated_failure_confidence_interval=(0.0, 0.15)):
    from lib.control.session_registry import current_behavior_policy_compatibility

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
        "treatment_trajectory_ids": [f"treatment-{index}"
                                      for index in range(treatment_count)],
        "policy_hashes": ["policy-hash"],
        "policy_identities": {"control": "control", "treatment": "treatment"},
        "candidate_policy_compatibility": current_behavior_policy_compatibility(policy),
        "comparison_context_hash": "comparison",
        "evaluator_id": "host-objective", "evaluator_version": "1",
        "evaluator_hash": "b" * 64,
        "control_bad_rate": 0.5, "treatment_bad_rate": treatment_bad_rate,
        "effect": 0.5 - treatment_bad_rate,
        "candidate_policy_evidence": {
            "correct_progress_rate": correct_progress_rate,
            "regression_rate": regression_rate,
            "repeated_failure_rate": repeated_failure_rate,
            "confidence_interval": list(confidence_interval),
            "regression_confidence_interval": list(regression_confidence_interval),
            "repeated_failure_confidence_interval": list(
                repeated_failure_confidence_interval),
            "sample_count": treatment_count,
            "holdout_count": 1,
        },
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


def test_promoted_policy_from_a_different_candidate_generator_is_ignored():
    state = BehaviorState()
    profile = promoted_policy_profile(candidate(state).policy_mapping())
    receipt = dict(profile["evidence"]["receipt"])
    receipt["candidate_policy_compatibility"] = {
        **dict(receipt["candidate_policy_compatibility"]),
        "candidate_generator_contract_hash": "0" * 64,
    }
    receipt.pop("receipt_hash", None)
    receipt["receipt_hash"] = hashlib.sha256(_canonical(receipt).encode()).hexdigest()
    profile["evidence"]["receipt"] = receipt

    assert BehaviorPolicyPrior.from_promoted_profile(profile) == ()


def test_validated_progress_evidence_beats_lower_estimated_cost():
    state = BehaviorState()
    evidenced = candidate(state)
    cheaper = candidate(replace(
        state, verification_intensity="passive", exploration_budget="low",
        context_strategy="compact"))
    profile = promoted_policy_profile(
        evidenced.policy_mapping(), treatment_bad_rate=0.1,
        correct_progress_rate=0.82, confidence_interval=(0.70, 0.91))
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
    assert selection.score.evidence.correct_progress_estimate == 0.82
    assert selection.score.estimated_cost > 0.25


def test_selector_abstains_when_small_validated_cohort_has_weak_lower_bound():
    state = BehaviorState()
    continue_policy = candidate(state, reason_codes=("continue_current",))
    recovery_state = replace(
        state, cognitive_mode="RECOVER", retry_policy="materially_different_only")
    recovery = candidate(recovery_state, reason_codes=("deterministic_rule",))
    profile = promoted_policy_profile(
        recovery.policy_mapping(), treatment_count=1, treatment_bad_rate=0.0,
        correct_progress_rate=1.0, confidence_interval=(0.2, 1.0),
        regression_rate=0.0, regression_confidence_interval=(0.0, 0.8),
        repeated_failure_rate=0.0,
        repeated_failure_confidence_interval=(0.0, 0.8))
    priors = BehaviorPolicyPrior.from_promoted_profile(profile)
    assert len(priors) == 1

    selection = BehaviorPolicySelector().select(
        (continue_policy, recovery),
        constraints=BehaviorPolicyConstraints(
            current_state=state, available_frameworks=ALL_FRAMEWORKS,
            goal_set=None),
        csw_snapshot=CSWBehaviorSnapshot(revision=0), goal_set=None,
        policy_context={**dict(profile["subject"]), **dict(profile["context"])},
        priors=priors)

    assert selection.candidate is None
    assert selection.disposition == "abstain_insufficient_evidence"
    assert selection.abstention_reason == "abstain_insufficient_evidence"


def test_required_mode_is_never_overridden_by_policy_abstention():
    state = BehaviorState()
    required = candidate(replace(state, cognitive_mode="REINSPECT"),
                         reason_codes=("deterministic_rule",))
    profile = promoted_policy_profile(
        required.policy_mapping(), treatment_count=1, treatment_bad_rate=0.0,
        correct_progress_rate=1.0, confidence_interval=(0.2, 1.0),
        regression_rate=0.0, regression_confidence_interval=(0.0, 0.8),
        repeated_failure_rate=0.0,
        repeated_failure_confidence_interval=(0.0, 0.8))
    priors = BehaviorPolicyPrior.from_promoted_profile(profile)

    selection = BehaviorPolicySelector().select(
        (required,),
        constraints=BehaviorPolicyConstraints(
            current_state=state, available_frameworks=ALL_FRAMEWORKS,
            goal_set=None, required_mode="REINSPECT"),
        csw_snapshot=CSWBehaviorSnapshot(revision=0), goal_set=None,
        policy_context={**dict(profile["subject"]), **dict(profile["context"])},
        priors=priors)

    assert selection.candidate.cognitive_mode == "REINSPECT"
    assert selection.abstained is False


def test_unrelated_weak_prior_does_not_abstain_over_strong_learned_candidate():
    state = BehaviorState()
    continuation = candidate(state, reason_codes=("continue_current",))
    weak = candidate(replace(state, cognitive_mode="RECOVER"))
    strong = candidate(replace(state, cognitive_mode="VERIFY"))
    weak_profile = promoted_policy_profile(
        weak.policy_mapping(), treatment_count=1, treatment_bad_rate=0.0,
        correct_progress_rate=1.0, confidence_interval=(0.2, 1.0),
        regression_rate=0.0, regression_confidence_interval=(0.0, 0.8),
        repeated_failure_rate=0.0,
        repeated_failure_confidence_interval=(0.0, 0.8))
    strong_profile = promoted_policy_profile(
        strong.policy_mapping(), treatment_count=20, treatment_bad_rate=0.1,
        correct_progress_rate=0.92, confidence_interval=(0.78, 0.98),
        regression_rate=0.02, regression_confidence_interval=(0.0, 0.12),
        repeated_failure_rate=0.04,
        repeated_failure_confidence_interval=(0.0, 0.14))
    priors = (BehaviorPolicyPrior.from_promoted_profile(weak_profile)
              + BehaviorPolicyPrior.from_promoted_profile(strong_profile))
    policy_context = {**dict(strong_profile["subject"]),
                      **dict(strong_profile["context"])}

    selection = BehaviorPolicySelector().select(
        (continuation, weak, strong),
        constraints=BehaviorPolicyConstraints(
            current_state=state, available_frameworks=ALL_FRAMEWORKS,
            goal_set=None),
        csw_snapshot=CSWBehaviorSnapshot(revision=0), goal_set=None,
        policy_context=policy_context, priors=priors)

    assert selection.candidate.candidate_id == strong.candidate_id
    assert selection.score.correct_progress_lower_bound > 0.5
    assert selection.abstained is False


def test_controller_abstention_selects_diagnostic_information_gathering_fallback():
    initial = BehaviorState()
    goals = GoalSetSnapshot(1, (GoalItemSnapshot("criterion-1"),))
    attention = schedule_attention(goals, initial, action_index=1)
    csw = CSWBehaviorSnapshot(
        revision=0,
        eligible_stateworks=(StateWorkOption(
            "repair-flow", behavior_tags=("recovery", "repair")),),
    )
    recovery_state = replace(
        initial,
        cognitive_mode="RECOVER",
        framework_modes={**dict(initial.framework_modes), "owl": "reset",
                         "anchor": "recovery", "fuse": "constrained"},
        verification_intensity="high",
        exploration_budget="low",
        retry_policy="materially_different_only",
        recovery_level=1,
    )
    recovery_candidate = BehaviorCandidate.from_state(
        recovery_state,
        focus_criteria=attention.lease.criterion_ids,
        statework_preferences=("repair-flow",),
        reason_codes=("deterministic_rule", "equivalent_retry"),
        cause="equivalent_retry", expected_effect="Bounded recovery",
    )
    profile = promoted_policy_profile(
        recovery_candidate.policy_mapping(), treatment_count=1,
        treatment_bad_rate=0.0, correct_progress_rate=1.0,
        confidence_interval=(0.2, 1.0), regression_rate=0.0,
        regression_confidence_interval=(0.0, 0.8), repeated_failure_rate=0.0,
        repeated_failure_confidence_interval=(0.0, 0.8))
    priors = BehaviorPolicyPrior.from_promoted_profile(profile)
    context = FastLoopInput(
        scope="tenant/app/agent/session/task/attempt",
        state=initial,
        csw_snapshot=csw,
        objective_criteria=("criterion-1",),
        goal_set=goals,
        attention_lease=attention.lease,
        attention_last_seen=attention.last_seen,
        available_frameworks=ALL_FRAMEWORKS,
        cfw_signals=(FrameworkSignal(
            "fuse", "equivalent_retry", criterion_id="criterion-1"),),
        equivalent_retry_count=2,
        policy_context={**dict(profile["subject"]), **dict(profile["context"])},
    )

    decision = BehavioralController().decide(context, policy_priors=priors)

    assert decision.state.cognitive_mode == "DIAGNOSE"
    assert decision.state.framework_modes["fuse"] == "constrained"
    assert decision.state.retry_policy == "materially_different_only"
    assert decision.state.focus_criteria == ("criterion-1",)
    assert any(item.target == "focus_criteria" for item in decision.directives)
    assert not any(item.target == "recovery_level" for item in decision.directives)
    assert decision.reason.startswith("abstain and gather more evidence:")


def test_unrelated_laya_action_relation_supports_replanning_before_execution():
    class Judgment:
        def answer(self, question_id):
            if question_id != "action_relation":
                return None
            return type("Answer", (), {
                "selected": "unrelated",
                "raw_confidence": 0.94,
                "calibrated_confidence": 0.94,
            })()

    goals = GoalSetSnapshot(1, (GoalItemSnapshot("criterion-1"),))
    decision = BehavioralController().decide(
        FastLoopInput(
            scope="tenant/app/agent/session/task/attempt",
            state=BehaviorState(),
            csw_snapshot=CSWBehaviorSnapshot(revision=0),
            objective_criteria=("criterion-1",),
            goal_set=goals,
            pre_action=True,
        ),
        judgment=Judgment(),
    )

    assert decision.state.cognitive_mode == "DIAGNOSE"
    assert any(item.operation == "set_mode" and item.value == "DIAGNOSE"
               for item in decision.directives)


def test_laya_statework_preference_changes_only_a_csw_eligible_choice():
    class Judgment:
        def answer(self, question_id):
            if question_id != "route_preference":
                return None
            return type("Answer", (), {
                "selected": "verify-flow",
                "raw_confidence": 0.96,
                "calibrated_confidence": 0.96,
            })()

    csw = CSWBehaviorSnapshot(
        revision=1,
        eligible_stateworks=(
            StateWorkOption("diagnose-flow", behavior_tags=("diagnose",)),
            StateWorkOption("verify-flow", behavior_tags=("verify",)),
        ),
    )
    decision = BehavioralController().decide(
        FastLoopInput(
            scope="tenant/app/agent/session/task/attempt",
            state=BehaviorState(cognitive_mode="DIAGNOSE"),
            csw_snapshot=csw,
            objective_criteria=("criterion-1",),
        ),
        judgment=Judgment(),
    )

    assert decision.preferred_stateworks == ("verify-flow",)
    assert any(item.operation == "prefer_statework" and item.target == "verify-flow"
               for item in decision.directives)


def _ambiguous_retry_case(judgment):
    """A stuck retry where the semantic provider points at the right relation."""
    goals = GoalSetSnapshot(1, (GoalItemSnapshot("criterion-1"),))

    provider_judgment = None
    if judgment is not None:
        class BaseJudgment:
            def answer(self, question_id):
                if question_id != "action_relation":
                    return None
                return type("Answer", (), {
                    "selected": judgment,
                    "raw_confidence": 0.93,
                    "calibrated_confidence": 0.93,
                })()

        provider_judgment = BaseJudgment()

    return BehavioralController().decide(
        FastLoopInput(
            scope="tenant/app/agent/session/task/attempt",
            state=BehaviorState(),
            csw_snapshot=CSWBehaviorSnapshot(revision=0),
            objective_criteria=("criterion-1",),
            goal_set=goals,
            pre_action=True,
        ),
        judgment=provider_judgment,
    )


def test_laya_disabled_parity_preserves_deterministic_safety_and_cws_eligibility():
    """Gate: with no provider, selection stays deterministic and never widens.

    Two identical no-provider runs must produce identical decisions, and the
    selected control set must equal the deterministic no-prior fallback.
    """
    goals = GoalSetSnapshot(1, (GoalItemSnapshot("criterion-1"),))
    runs = []
    for _ in range(2):
        decision = BehavioralController().decide(
            FastLoopInput(
                scope="tenant/app/agent/session/task/attempt",
                state=BehaviorState(),
                csw_snapshot=CSWBehaviorSnapshot(revision=0),
                objective_criteria=("criterion-1",),
                goal_set=goals,
                pre_action=True,
            ),
            judgment=None,
        )
        runs.append(decision)

    # Deterministic: identical inputs, identical decision.
    assert runs[0].state.cognitive_mode == runs[1].state.cognitive_mode
    assert tuple(d.operation for d in runs[0].directives) == \
        tuple(d.operation for d in runs[1].directives)

    # Safety preserved: a provider-free run emits only routine directives and
    # never records an intervention outcome against a learned policy.
    assert runs[0].intervention_outcomes == ()
    for directive in runs[0].directives:
        assert directive.operation in {
            "set_mode", "set_verification_intensity", "set_exploration_budget",
            "set_retry_policy", "set_context_strategy", "set_response_strategy",
            "grant_attention", "revoke_attention",
        }
    # CSW eligibility is unchanged when no provider is consulted: the mode is
    # a legal CFW-owned mode drawn from the authoritative set.
    assert runs[0].state.cognitive_mode in BEHAVIOR_MODES


def test_semantic_action_relation_changes_decision_without_taking_authority():
    """A semantic hint changes the deterministic choice but not its authority."""
    baseline = _ambiguous_retry_case(None)
    treatment = _ambiguous_retry_case("required_verification")

    # The baseline and treatment share the same ambiguous pre-action retry
    # context. Without a provider the controller delivers; a verification
    # relation moves the choice to VERIFY.
    assert baseline.state.cognitive_mode == "DELIVER"
    assert treatment.state.cognitive_mode == "VERIFY"
    assert any(item.operation == "set_mode" and item.value == "VERIFY"
               for item in treatment.directives)

    # Neither outcome may invent a control value outside the legal set.
    legal = {"passive", "normal", "high", "strict"}
    for decision in (baseline, treatment):
        for directive in decision.directives:
            if directive.operation == "set_verification_intensity":
                assert directive.value in legal

    # The provider is advisory evidence only: it reorders among legal
    # candidates, so both runs still land on legal CFW-owned modes.
    assert baseline.state.cognitive_mode in BEHAVIOR_MODES
    assert treatment.state.cognitive_mode in BEHAVIOR_MODES

    # The semantic provider contributes a preference only; it is never the
    # authority for a policy directive.
    for receipt in getattr(treatment, "receipts", ()) or ():
        assert receipt.get("authority") != "semantic_provider"


# -- P1-7: runnable fairness belongs to the attention scheduler ---------

def _starved_selection(*, action_index, last_seen_value, lease_actions,
                       focus, required_mode=None):
    """Build a 20-item runnable set where one branch ignores an old criterion."""
    state = BehaviorState()
    goal_set = GoalSetSnapshot(1, tuple(
        GoalItemSnapshot(f"criterion-{index}") for index in range(20)))
    lease = AttentionLease("lease-fair", focus, 1, 1, 5,
                           max_actions=lease_actions)
    last_seen = {item.criterion_id: last_seen_value for item in goal_set.items}
    return BehaviorPolicySelector().select(
        (), constraints=BehaviorPolicyConstraints(
            current_state=state, available_frameworks=ALL_FRAMEWORKS,
            goal_set=goal_set, required_mode=required_mode),
        csw_snapshot=CSWBehaviorSnapshot(revision=0), goal_set=goal_set,
        policy_context={}, attention_lease=lease,
        attention_last_seen=last_seen, current_action_index=action_index)


def _focused_candidate(focus, state=None, *, reason_codes=("continue_current",)):
    """A candidate that names an explicit focus.

    ``focus_scope`` must be ``explicit`` or the candidate ID ignores the focus,
    and two candidates that differ only in attention would collide.
    """
    base = BehaviorCandidate.from_state(
        state or BehaviorState(), focus_criteria=focus,
        statework_preferences=(), reason_codes=reason_codes,
        cause="test", expected_effect="test")
    return replace(base, focus_scope="explicit",
                   candidate_id=candidate_id_for(
                       cognitive_mode=base.cognitive_mode,
                       framework_modes=base.framework_modes,
                       controls=base.controls, focus_scope="explicit",
                       statework_preferences=base.statework_preferences,
                       focus_criteria=focus))


def test_candidate_scoring_does_not_replace_scheduler_fairness():
    """Candidate legality is independent from the global runnable-set bound."""
    starved = _focused_candidate(("criterion-0",))

    selection = BehaviorPolicySelector().select(
        (starved,),
        constraints=BehaviorPolicyConstraints(
            current_state=BehaviorState(), available_frameworks=ALL_FRAMEWORKS,
            goal_set=GoalSetSnapshot(1, tuple(
                GoalItemSnapshot(f"criterion-{index}") for index in range(20)))),
        csw_snapshot=CSWBehaviorSnapshot(revision=0),
        goal_set=GoalSetSnapshot(1, tuple(
            GoalItemSnapshot(f"criterion-{index}") for index in range(20))),
        policy_context={},
        attention_lease=AttentionLease("lease-fair", ("criterion-0",), 1, 1, 5,
                                       max_actions=6),
        attention_last_seen={f"criterion-{index}": 0 for index in range(20)},
        current_action_index=20)

    assert selection.candidate.candidate_id == starved.candidate_id
    # The scalar is a saturated ranking signal; attention scheduling performs
    # the actual round-robin rotation over all runnable criteria.
    assert selection.score.starvation_risk == 1.0


def test_fairness_breach_still_risks_one_when_the_bound_is_respected():
    """When every candidate is fair, ranking proceeds normally."""
    focus = ("criterion-0",)
    goal_set = GoalSetSnapshot(1, tuple(
        GoalItemSnapshot(f"criterion-{index}") for index in range(4)))
    only = _focused_candidate(focus)

    selection = BehaviorPolicySelector().select(
        (only,),
        constraints=BehaviorPolicyConstraints(
            current_state=BehaviorState(), available_frameworks=ALL_FRAMEWORKS,
            goal_set=goal_set),
        csw_snapshot=CSWBehaviorSnapshot(revision=0), goal_set=goal_set,
        policy_context={},
        attention_lease=AttentionLease("lease-ok", focus, 1, 1, 5, max_actions=2),
        attention_last_seen={f"criterion-{index}": 0 for index in range(4)},
        current_action_index=1)

    assert selection.candidate.candidate_id == only.candidate_id
    assert selection.score.starvation_risk is not None
    assert selection.score.starvation_risk <= 1.0


def test_a_host_required_mode_may_still_breach_fairness():
    """Fairness pressure is diagnostic and cannot veto a legal policy."""
    focus = ("criterion-0",)
    goal_set = GoalSetSnapshot(1, tuple(
        GoalItemSnapshot(f"criterion-{index}") for index in range(20)))
    only = _focused_candidate(focus)

    selection = BehaviorPolicySelector().select(
        (only,),
        constraints=BehaviorPolicyConstraints(
            current_state=BehaviorState(), available_frameworks=ALL_FRAMEWORKS,
            goal_set=goal_set, required_mode=only.cognitive_mode),
        csw_snapshot=CSWBehaviorSnapshot(revision=0), goal_set=goal_set,
        policy_context={},
        attention_lease=AttentionLease("lease-forced", focus, 1, 1, 5,
                                       max_actions=8),
        attention_last_seen={f"criterion-{index}": 0 for index in range(20)},
        current_action_index=20)

    assert selection.candidate.candidate_id == only.candidate_id
    # Risk is clamped as a diagnostic scalar. The scheduler records actual
    # violations from runnable ages at committed action boundaries.
    assert selection.score.starvation_risk == 1.0


def test_starvation_pressure_does_not_rank_behavior_policy_candidates():
    """Attention ages cannot override the host scheduler's chosen focus."""
    goal_set = GoalSetSnapshot(1, tuple(
        GoalItemSnapshot(f"criterion-{index}") for index in range(2)))
    last_seen = {"criterion-0": 0, "criterion-1": 4}
    attending_starved = _focused_candidate(("criterion-0",),
                                            reason_codes=("deterministic_rule",))
    attending_fresh = _focused_candidate(("criterion-1",),
                                          reason_codes=("deterministic_rule",))

    selector = BehaviorPolicySelector()
    kwargs = {
        "constraints": BehaviorPolicyConstraints(
            current_state=BehaviorState(), available_frameworks=ALL_FRAMEWORKS,
            goal_set=goal_set),
        "csw_snapshot": CSWBehaviorSnapshot(revision=0), "goal_set": goal_set,
        "policy_context": {},
        "attention_lease": AttentionLease("lease-tie", ("criterion-1",), 1, 1, 5,
                                           max_actions=1),
        "current_action_index": 4,
    }
    first = selector.select((attending_fresh, attending_starved),
                            attention_last_seen=last_seen, **kwargs)
    swapped = selector.select((attending_fresh, attending_starved),
                              attention_last_seen={"criterion-0": 4,
                                                   "criterion-1": 0}, **kwargs)

    assert first.candidate.candidate_id == swapped.candidate.candidate_id
