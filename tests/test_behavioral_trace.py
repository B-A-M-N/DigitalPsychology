"""Behavioral telemetry for the production cohort gate (Pass 3).

These tests pin the definitions the gate depends on. The two that matter most:

- starvation is measured against DP's own fairness bound, and
- mode thrash is a reversal without new evidence, not a raw transition count.
"""
from __future__ import annotations

import pytest

from lib.attention import fairness_bound_actions
from lib.telemetry.behavioral_trace import (REVERSAL_WINDOW_ACTIONS,
                                            ActionTrace, BehavioralTrace)


def _action(index, *, mode="DELIVER", focus=("a",), bound=4, age=0,
            **kwargs):
    return ActionTrace(
        action_index=index, cognitive_mode=mode, focus_criteria=focus,
        attention_ages={c: age for c in focus}, fairness_bound=bound,
        max_starvation_age=age, **kwargs)


# -- starvation ---------------------------------------------------------

def test_starvation_is_measured_against_dps_own_fairness_bound():
    trace = BehavioralTrace()
    trace.record(_action(0, bound=6, age=6))
    # At the bound is still compliant; one past it is a violation.
    assert trace.fairness_violation_count == 0
    trace.record(_action(1, bound=6, age=7))
    assert trace.fairness_violation_count == 1
    assert trace.max_starvation_age == 7
    assert trace.max_starvation_ratio == pytest.approx(7 / 6)


def test_fairness_bound_matches_the_shared_invariant():
    """The trace must use the same bound DP schedules against."""
    trace = BehavioralTrace()
    bound = fairness_bound_actions(3)
    trace.record(_action(0, bound=bound, age=bound + 1))
    assert trace.fairness_violation_count == 1


def test_no_runnable_criteria_means_no_violation():
    trace = BehavioralTrace()
    trace.record(_action(0, bound=0, age=0))
    assert trace.fairness_violation_count == 0
    assert trace.max_starvation_ratio == 0.0


def test_required_criterion_coverage_is_first_seen_order():
    trace = BehavioralTrace()
    trace.record(_action(0, focus=("a", "b")))
    trace.record(_action(1, focus=("b", "c")))
    assert trace.required_criterion_coverage == ("a", "b", "c")


def test_semantic_query_attempt_return_use_and_failure_are_separate():
    trace = BehavioralTrace()
    trace.record(_action(
        0, semantic_query_attempted=True, semantic_judgment_returned=True,
        semantic_judgment_used=False,
    ))
    trace.record(_action(
        1, semantic_query_attempted=True, semantic_query_failed=True,
    ))

    record = trace.to_dict(trial_id="semantic-states")
    assert record["semantic_queries"] == 2
    assert record["semantic_judgment_returns"] == 1
    assert record["semantic_uses"] == 0
    assert record["semantic_query_failures"] == 1


def test_private_transition_trace_records_semantic_and_drift_outcomes():
    from lib.behavior_state import (BehaviorDirective, BehavioralDecision,
                                    BehaviorState)
    from lib.telemetry.trace_builder import build_control_transition_trace

    prior = BehaviorState(revision=0)
    state = BehaviorState(revision=1)
    directive = BehaviorDirective(
        directive_id="drift-fix", operation="set_mode", target="mode",
        value="VERIFY", cause="goal_drift", expected_effect="reanchor goal",
        scope="scope-1", expires_after_actions=2,
        intervention_id="intervention-1", expires_at_action=2)
    decision = BehavioralDecision(
        decision_id="decision-1", scope="scope-1", expected_revision=0,
        state=state, directives=(directive,), reason="goal drift detected")
    basis = {
        "boundary_sequence": 3, "host_action_index": 2,
        "boundary_kind": "pre_action", "semantic_query_attempted": True,
        "semantic_judgment_returned": True, "semantic_judgment_used": False,
        "semantic_query_failed": False,
        "behavioral_facts": {
            "active_trigger_codes": ["semantic_goal_drift"],
            "candidate_rejection_stage": "cfw_final_gate",
        },
    }

    trace = build_control_transition_trace(
        "scope-1", prior, state, decision, basis, ("drift-fix",))

    assert trace["semantic_query_attempted"] is True
    assert trace["semantic_judgment_returned"] is True
    assert trace["semantic_judgment_used"] is False
    assert trace["semantic_query_failed"] is False
    assert trace["goal_drift_detected"] is True
    assert trace["goal_drift_intervention_applied"] is True
    assert trace["ward_hard_safety"] is False
    assert trace["justification_class"] == "heuristic"


def test_private_cfw_signal_codes_normalize_retry_drift_and_hard_safety():
    from lib.behavior_state import BehavioralDecision, BehaviorState
    from lib.telemetry.trace_builder import build_control_transition_trace

    prior = BehaviorState(revision=0)
    state = BehaviorState(revision=1)
    decision = BehavioralDecision(
        decision_id="signals", scope="scope-1", expected_revision=0,
        state=state, directives=(), reason="signal observed")

    def transition(code, *, ward=False):
        return build_control_transition_trace(
            "scope-1", prior, state, decision,
            {"boundary_sequence": 0, "host_action_index": 1,
             "boundary_kind": "pre_action", "behavioral_facts": {
                 "cfw_signal_codes": [code], "ward_hard_safety": ward,
                 "candidate_rejection_stage": "cfw_final_gate"}}, ())

    retry = transition("owl.approach_failed")
    drift = transition("owl.constraint_drift")
    ward = transition("ward.secret_exposure", ward=True)
    assert retry["justification_class"] == "heuristic"
    assert drift["goal_drift_detected"] is True
    assert drift["ward_hard_safety"] is False
    assert ward["justification_class"] == "hard_safety"


def test_material_progress_requires_a_criterion_revision_delta():
    from lib.behavior_state import GoalItemSnapshot, GoalSetSnapshot
    from lib.telemetry.material_progress import material_progress_delta

    before = GoalSetSnapshot(revision=4, items=(
        GoalItemSnapshot(criterion_id="a", material_progress_revision=2),
        GoalItemSnapshot(criterion_id="b", material_progress_revision=1),
    ))
    after = GoalSetSnapshot(revision=5, items=(
        GoalItemSnapshot(criterion_id="a", material_progress_revision=2),
        GoalItemSnapshot(criterion_id="b", material_progress_revision=3),
    ))
    assert material_progress_delta(before, after) == ("b",)


def test_private_dwell_uses_all_boundaries_and_allows_zero_action_dwell():
    trace = BehavioralTrace()
    trace.record_control_transition({
        "boundary_sequence": 7, "host_action_index": 10,
        "prior_mode": "DELIVER", "resulting_mode": "VERIFY",
        "justification_class": "heuristic"})
    trace.record_control_transition({
        "boundary_sequence": 8, "host_action_index": 10,
        "prior_mode": "VERIFY", "resulting_mode": "DELIVER",
        "justification_class": "heuristic"})
    record = trace.to_dict(trial_id="same-action-mode-flip")
    assert record["median_mode_dwell_actions"] == 0.0
    assert record["median_mode_dwell_boundaries"] == 1.0


# -- mode thrash --------------------------------------------------------

def test_rapid_reversal_without_new_evidence_counts_as_thrash():
    """A -> B -> A inside the window with no evidence is pathological."""
    trace = BehavioralTrace()
    trace.record(_action(0, mode="DELIVER"))
    trace.record(_action(1, mode="VERIFY"))
    trace.record(_action(2, mode="DELIVER"))
    assert trace.mode_transitions == 2
    assert trace.rapid_mode_reversals == 1


def test_evidence_backed_transitions_are_not_counted_as_thrash():
    """DELIVER -> VERIFY -> DELIVER with real evidence is correct behavior."""
    trace = BehavioralTrace()
    trace.record(_action(0, mode="DELIVER"))
    trace.record(_action(1, mode="VERIFY", authoritative_evidence_classes=(
        "host_evidence_ref",), justification_class="validator_evidence"))
    trace.record(_action(2, mode="DELIVER", authoritative_evidence_classes=(
        "host_evidence_ref",), justification_class="validator_evidence"))
    assert trace.mode_transitions == 2
    assert trace.rapid_mode_reversals == 0


@pytest.mark.parametrize("justification", ["semantic_advisory", "heuristic"])
def test_semantic_or_heuristic_transition_does_not_erase_mode_thrash(justification):
    trace = BehavioralTrace()
    trace.record(_action(0, mode="DELIVER"))
    trace.record(_action(1, mode="VERIFY", justification_class=justification))
    trace.record(_action(2, mode="DELIVER"))
    assert trace.rapid_mode_reversals == 1


def test_private_control_mode_thrashing_uses_boundary_order_and_action_age():
    trace = BehavioralTrace()
    trace.record_control_transition({
        "boundary_sequence": 1, "host_action_index": 4,
        "prior_mode": "DELIVER", "resulting_mode": "VERIFY",
        "justification_class": "heuristic",
    })
    trace.record_control_transition({
        "boundary_sequence": 2, "host_action_index": 4,
        "prior_mode": "VERIFY", "resulting_mode": "DELIVER",
        "justification_class": "semantic_advisory",
    })
    assert trace.mode_transitions == 2
    assert trace.rapid_mode_reversals == 1

    justified = BehavioralTrace()
    justified.record_control_transition({
        "boundary_sequence": 1, "host_action_index": 4,
        "prior_mode": "DELIVER", "resulting_mode": "VERIFY",
        "justification_class": "heuristic",
    })
    justified.record_control_transition({
        "boundary_sequence": 2, "host_action_index": 4,
        "prior_mode": "VERIFY", "resulting_mode": "DELIVER",
        "justification_class": "validator_evidence",
    })
    assert justified.rapid_mode_reversals == 0


def test_private_ward_hard_safety_evidence_exempts_a_rapid_reversal():
    from lib.behavior_state import BehavioralDecision, BehaviorState
    from lib.telemetry.trace_builder import build_control_transition_trace

    deliver = BehaviorState(revision=0, cognitive_mode="DELIVER")
    verify = BehaviorState(revision=1, cognitive_mode="VERIFY")
    returned = BehaviorState(revision=2, cognitive_mode="DELIVER")

    def transition(sequence, prior, state, *, facts=None):
        decision = BehavioralDecision(
            decision_id=f"ward-{sequence}", scope="scope-ward",
            expected_revision=prior.revision, state=state, directives=(),
            reason="control boundary")
        return build_control_transition_trace(
            "scope-ward", prior, state, decision,
            {"boundary_sequence": sequence, "host_action_index": 4,
             "boundary_kind": "pre_action",
             "behavioral_facts": facts or {}}, ())

    trace = BehavioralTrace()
    trace.record_control_transition(transition(1, deliver, verify))
    ward_return = transition(2, verify, returned, facts={
        "cfw_signal_codes": ["ward.secret_exposure"],
        "ward_hard_safety": True,
    })
    trace.record_control_transition(ward_return)

    assert ward_return["ward_hard_safety"] is True
    assert ward_return["justification_class"] == "hard_safety"
    assert trace.rapid_mode_reversals == 0


def test_slow_reversal_is_not_thrash():
    """Returning much later is not oscillation."""
    trace = BehavioralTrace()
    trace.record(_action(0, mode="DELIVER"))
    trace.record(_action(1, mode="VERIFY"))
    for index in range(2, 10):
        trace.record(_action(index, mode="VERIFY"))
    trace.record(_action(10, mode="DELIVER"))
    assert trace.rapid_mode_reversals == 0


def test_median_mode_dwell_is_reported_not_guessed():
    trace = BehavioralTrace()
    trace.record(_action(0, mode="DELIVER"))
    trace.record(_action(4, mode="VERIFY"))
    trace.record(_action(6, mode="DELIVER"))
    # Dwells of 4 and 2 actions; the median is 3.0, not a guess.
    assert trace.median_mode_dwell_actions == 3.0


def test_median_dwell_is_none_for_a_single_mode():
    trace = BehavioralTrace()
    trace.record(_action(0, mode="DELIVER"))
    trace.record(_action(1, mode="DELIVER"))
    assert trace.median_mode_dwell_actions is None


# -- counters and projection churn ---------------------------------------

def test_behavior_counters_accumulate():
    trace = BehavioralTrace()
    trace.record(_action(0, equivalent_retry=True, goal_drift=True,
                          goal_drift_detected=True,
                          goal_drift_intervention_applied=True, abstained=True,
                          cfw_preflight_rejection=True, cfw_final_rejection=True,
                          semantic_query_attempted=True, attention_rotated=True,
                          fairness_forced_rotation=True,
                          attention_lease_started=True))
    assert trace.equivalent_retries == 1
    assert trace.goal_drift_detections == 1
    assert trace.goal_drift_interventions == 1
    assert trace.abstentions == 1
    assert trace.cfw_preflight_rejections == 1
    assert trace.cfw_final_gate_rejections == 1
    assert trace.semantic_queries == 1
    assert trace.forced_attention_rotations == 1
    assert trace.attention_lease_starts == 1
    assert trace.attention_rotations == 1


def test_goal_drift_detection_is_distinct_from_applied_intervention():
    trace = BehavioralTrace()
    trace.record(_action(0, goal_drift_detected=True))
    record = trace.to_dict(trial_id="trial-drift")
    assert record["goal_drift_detections"] == 1
    assert record["goal_drift_interventions"] == 0


def test_projection_churn_counts_distinct_digests():
    trace = BehavioralTrace()
    for index, digest in enumerate(("d1", "d1", "d2", None, "d3")):
        trace.record(_action(index, projection_digest=digest))
    # Three real replacements, two of which changed the projected guidance.
    assert trace.projection_updates == 4
    assert trace.unique_projection_digests == 3


def test_action_trace_persists_only_canonical_semantic_fields():
    row = _action(0, semantic_query_attempted=True,
                  semantic_judgment_returned=True,
                  semantic_judgment_used=True,
                  semantic_query_failed=False).to_dict()
    assert row["semantic_query_attempted"] is True
    assert row["semantic_judgment_returned"] is True
    assert row["semantic_judgment_used"] is True
    assert row["semantic_query_failed"] is False
    assert not {"semantic_query", "semantic_attempted", "semantic_result",
                "semantic_used", "semantic_failed"} & set(row)
    assert ActionTrace.from_dict(row).semantic_judgment_used is True
    legacy_row = dict(row)
    legacy_row["semantic_attempted"] = legacy_row.pop("semantic_query_attempted")
    legacy_row["semantic_result"] = legacy_row.pop("semantic_judgment_returned")
    legacy_row["semantic_used"] = legacy_row.pop("semantic_judgment_used")
    legacy_row["semantic_failed"] = legacy_row.pop("semantic_query_failed")
    assert ActionTrace.from_dict(legacy_row).semantic_judgment_used is True
    assert not {"semantic_query", "semantic_attempted", "semantic_result",
                "semantic_used", "semantic_failed"} & {
                    field.name for field in __import__("dataclasses").fields(ActionTrace)}


def test_trace_is_bounded():
    trace = BehavioralTrace()
    from lib.telemetry.behavioral_trace import _MAX_TRACE_ACTIONS
    for index in range(_MAX_TRACE_ACTIONS):
        trace.record(_action(index))
    with pytest.raises(ValueError, match="bounded action count"):
        trace.record(_action(_MAX_TRACE_ACTIONS))


# -- provider attestation and record shape -------------------------------

def test_trial_record_carries_provider_attestation_and_outcome():
    trace = BehavioralTrace(configuration="cfw+csw+dp+laya", task_id="task-7",
                            semantic_provider_status="enabled",
                            semantic_model_hash="a" * 64)
    trace.record(_action(0))
    record = trace.to_dict(trial_id="trial-1", task_outcome_score=0.86,
                           task_completed=True)
    assert record["trial_id"] == "trial-1"
    assert record["evidence_source"] == "internal_host_telemetry"
    assert record["semantic_provider_status"] == "enabled"
    assert record["semantic_model_hash"] == "a" * 64
    assert record["task_outcome_score"] == 0.86
    assert record["task_completed"] is True


def test_outcome_fields_are_absent_rather_than_invented():
    """Unknown outcome must stay unknown, never default to zero or false."""
    trace = BehavioralTrace()
    trace.record(_action(0))
    record = trace.to_dict(trial_id="trial-2")
    assert "task_outcome_score" not in record
    assert "task_completed" not in record


# -- live session integration --------------------------------------------

def test_runtime_session_records_telemetry_from_real_committed_actions(tmp_path):
    """The trace must come from real committed runtime state.

    This drives the production RuntimeHost through two committed actions and
    then reads the internal trial record the cohort gate joins on. Nothing is
    stubbed: the ages, fairness bound and coverage come from DP's scheduler and
    its persisted state store.
    """
    from lib.runtime.host import RuntimeHost
    from lib.runtime.contracts import RuntimeIdentity, RuntimeObservation
    from lib.runtime.adapters.cfw import CFWRuntimeAdapter
    from lib.service import DigitalPsychologyService
    from test_runtime_host import _CFWPort, _CSWPort, _EvidenceAuthority, _identity

    service = DigitalPsychologyService(event_db=tmp_path / "events.sqlite3")
    csw = _CSWPort()
    facts: dict = {}
    try:
        host = RuntimeHost(service=service,
                           state_path=tmp_path / "behavior-state.sqlite3")
        session = host.open_session(
            identity=_identity(), objective="Render the requested page",
            objective_criteria=("criterion-visual",),
            cfw=CFWRuntimeAdapter(_CFWPort()), csw=csw,
            outcome_evidence_authority=_EvidenceAuthority(facts),
            policy_context={"configuration": "dp+laya",
                            "semantic_provider_status": "enabled",
                            "semantic_model_hash": "e" * 64})
        session.process_action(RuntimeObservation(
            action_index=10, equivalent_retry_count=2,
            criterion_status={"criterion-visual": "unmet"}))
        session.process_action(RuntimeObservation(
            action_index=11, criterion_status={"criterion-visual": "unmet"}))

        record = session.behavioral_trial_record(trial_id="trial-1")
        assert record["evidence_source"] == "internal_host_telemetry"
        assert record["committed_actions"] == 2
        assert record["task_id"] == "task-1"
        assert record["configuration"] == "cfw+csw+dp"
        assert record["semantic_provider_status"] == "disabled"
        assert record["semantic_model_hash"] is None
        # The fairness bound comes from DP's own scheduler, not a constant here.
        assert record["fairness_bound"] > 0
        assert record["required_criterion_coverage"] == ["criterion-visual"]
        # Two committed actions with no starvation must be compliant.
        assert record["fairness_violation_count"] == 0
    finally:
        service.close()
