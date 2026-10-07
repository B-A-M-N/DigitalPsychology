from __future__ import annotations

from pathlib import Path

import pytest

from lib.telemetry.production_metrics import (
    LAYA_EVENTS,
    QUESTION_FAMILIES,
    ProductionMetrics,
)


def test_laya_events_record_global_and_question_family_counts(tmp_path: Path):
    metrics = ProductionMetrics(tmp_path / "metrics.sqlite3")

    metrics.record_laya_event("opportunity", question_family="action_relation", amount=2)
    metrics.record_laya_event("attempt", question_family="action_relation", amount=2)
    metrics.record_laya_event("candidate_change", question_family="action_relation")
    metrics.record_laya_event("intervention_created", question_family="goal_drift")
    metrics.record_laya_event("intervention_effective", question_family="goal_drift")
    metrics.record_laya_event("intervention_ineffective", question_family="route_preference")

    counters = metrics.snapshot()["counters"]
    assert counters["laya_query_opportunities"] == 2
    assert counters["laya_question_action_relation_opportunity"] == 2
    assert counters["laya_query_attempts"] == 2
    assert counters["laya_question_action_relation_attempt"] == 2
    assert counters["laya_influenced_candidate_changes"] == 1
    assert counters["laya_question_action_relation_candidate_change"] == 1
    assert counters["laya_influenced_intervention_effective"] == 1
    assert counters["laya_influenced_interventions"] == 1
    assert counters["laya_question_goal_drift_intervention_created"] == 1
    assert counters["laya_question_goal_drift_intervention_effective"] == 1
    assert counters["laya_influenced_intervention_ineffective"] == 1
    assert counters["laya_question_route_preference_intervention_ineffective"] == 1


def test_laya_event_and_family_allowlists_are_fixed(tmp_path: Path):
    metrics = ProductionMetrics(tmp_path / "metrics.sqlite3")

    assert set(QUESTION_FAMILIES) == {
        "action_relation", "progress_relation", "retry_relation",
        "goal_drift", "route_preference",
    }
    for event in LAYA_EVENTS:
        metrics.record_laya_event(event, question_family="progress_relation")
    for family in QUESTION_FAMILIES:
        metrics.record_laya_event("attempt", question_family=family)

    with pytest.raises(ValueError):
        metrics.record_laya_event("caller_controlled_metric")
    with pytest.raises(ValueError):
        metrics.record_laya_event("attempt", question_family="arbitrary-family")
    with pytest.raises(ValueError):
        metrics.record_laya_event("attempt", amount=-1)

    counters = metrics.snapshot()["counters"]
    assert counters["laya_question_progress_relation_attempt"] == 2
    assert counters["laya_question_action_relation_attempt"] == 1
    assert counters["laya_question_retry_relation_attempt"] == 1
    assert counters["laya_question_goal_drift_attempt"] == 1
    assert counters["laya_question_route_preference_attempt"] == 1
    assert counters["laya_query_attempts"] == 6


def test_laya_latency_summary_uses_persisted_bounded_histogram(tmp_path: Path):
    path = tmp_path / "metrics.sqlite3"
    metrics = ProductionMetrics(path)
    for value in (0.5, 1.5, 10, 50, 100, 1_000):
        metrics.observe_duration("laya_query_latency_ms", value)

    reopened = ProductionMetrics(path)
    timer = reopened.snapshot()["timers"]["laya_query_latency_ms"]
    assert timer["count"] == 6
    assert timer["total_ms"] == 1_162
    assert timer["mean_ms"] == pytest.approx(1_162 / 6)
    # Percentiles are upper bounds of fixed histogram buckets.
    assert timer["p50_ms"] == 10
    assert timer["p95_ms"] == 1_000
    assert timer["p50_saturated"] is False
    assert timer["p95_saturated"] is False


def test_laya_family_only_events_do_not_duplicate_global_totals(tmp_path: Path):
    metrics = ProductionMetrics(tmp_path / "metrics.sqlite3")

    # A frame can include multiple questions; record its global opportunity
    # once, then attribute each distinct question without repeating the total.
    metrics.record_laya_event("opportunity")
    metrics.record_laya_family_event("opportunity", question_family="action_relation")
    metrics.record_laya_family_event("opportunity", question_family="progress_relation")
    metrics.record_laya_family_event("intervention_created", question_family="action_relation")

    counters = metrics.snapshot()["counters"]
    assert counters["laya_query_opportunities"] == 1
    assert counters["laya_query_attempts"] == 0
    assert counters["laya_question_action_relation_opportunity"] == 1
    assert counters["laya_question_progress_relation_opportunity"] == 1
    assert counters["laya_influenced_interventions"] == 0
    assert counters["laya_question_action_relation_intervention_created"] == 1


def test_laya_latency_reports_saturated_tail_and_zero_sample_shape(tmp_path: Path):
    metrics = ProductionMetrics(tmp_path / "metrics.sqlite3")
    empty = metrics.snapshot()["timers"]["laya_query_latency_ms"]
    assert empty["mean_ms"] == 0
    assert empty["p50_ms"] is None
    assert empty["p95_ms"] is None

    metrics.observe_duration("laya_query_latency_ms", 61_000)
    timer = metrics.snapshot()["timers"]["laya_query_latency_ms"]
    assert timer["p50_ms"] == 60_000
    assert timer["p95_ms"] == 60_000
    assert timer["p50_saturated"] is True
    assert timer["p95_saturated"] is True
