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
        "host_evidence_ref",)), authoritative_evidence=True)
    trace.record(_action(2, mode="DELIVER", authoritative_evidence_classes=(
        "host_evidence_ref",)), authoritative_evidence=True)
    assert trace.mode_transitions == 2
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
    trace.record(_action(0, equivalent_retry=True, goal_drift=True, abstained=True,
                          cfw_preflight_rejection=True, cfw_final_rejection=True,
                          semantic_query=True, attention_rotated=True,
                          fairness_forced_rotation=True,
                          attention_lease_started=True))
    assert trace.equivalent_retries == 1
    assert trace.goal_drift_interventions == 1
    assert trace.abstentions == 1
    assert trace.cfw_preflight_rejections == 1
    assert trace.cfw_final_gate_rejections == 1
    assert trace.semantic_queries == 1
    assert trace.forced_attention_rotations == 1
    assert trace.attention_lease_starts == 1
    assert trace.attention_rotations == 1


def test_projection_churn_counts_distinct_digests():
    trace = BehavioralTrace()
    for index, digest in enumerate(("d1", "d1", "d2", None, "d3")):
        trace.record(_action(index, projection_digest=digest))
    # Three real replacements, two of which changed the projected guidance.
    assert trace.projection_updates == 4
    assert trace.unique_projection_digests == 3


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
