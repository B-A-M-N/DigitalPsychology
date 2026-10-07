from __future__ import annotations

from lib.telemetry.trial_report import aggregate_semantic_transitions


def test_session_semantic_metrics_join_later_outcomes_to_origin_question():
    transitions = [
        {
            "semantic_query_opportunity": True,
            "semantic_query_opportunity_question_id": "retry_relation",
            "semantic_query_attempted": False,
            "semantic_question_ids": [],
        },
        {
            "semantic_query_opportunity": True,
            "semantic_query_opportunity_question_id": "progress_relation",
            "semantic_query_attempted": True,
            "semantic_judgment_returned": True,
            "semantic_judgment_used": True,
            "semantic_query_failed": False,
            "semantic_question_ids": ["progress_relation"],
            "semantic_influence_receipt_id": "receipt-progress",
            "semantic_influenced_decision": True,
            "semantic_candidate_change": True,
            "semantic_mode_change": False,
            "semantic_framework_changes": 1,
            "semantic_statework_change": False,
            "semantic_latency_ms": 24.0,
            "semantic_influenced_intervention_ids": ["intervention-progress"],
            "semantic_intervention_outcomes": [],
        },
        {
            "semantic_query_opportunity": False,
            "semantic_query_attempted": False,
            "semantic_question_ids": [],
            "semantic_intervention_outcomes": [{
                "influence_receipt_id": "receipt-progress",
                "intervention_id": "intervention-progress",
                "outcome": "effective",
                "observed_revision": 8,
            }],
        },
        {
            "semantic_query_opportunity": True,
            "semantic_query_opportunity_question_id": "goal_drift",
            "semantic_query_attempted": True,
            "semantic_judgment_returned": True,
            "semantic_judgment_used": True,
            "semantic_question_ids": ["goal_drift"],
            "semantic_influence_receipt_id": "receipt-drift",
            "semantic_influenced_decision": True,
            "semantic_influenced_intervention_ids": ["intervention-drift"],
            "semantic_latency_ms": 40.0,
        },
    ]

    result = aggregate_semantic_transitions(transitions)

    assert result["query_opportunities"] == 3
    assert result["query_attempts"] == 2
    assert result["valid_results"] == 2
    assert result["advisory_results"] == 2
    progress = result["by_question_family"]["progress_relation"]
    assert progress["influenced_decisions"] == 1
    assert progress["candidate_changes"] == 1
    assert progress["framework_changes"] == 1
    assert progress["influenced_interventions"] == 1
    assert progress["effective"] == 1
    assert progress["ineffective"] == 0
    assert progress["unresolved"] == 0
    assert progress["mean_latency_ms"] == 24.0
    drift = result["by_question_family"]["goal_drift"]
    assert drift["influenced_interventions"] == 1
    assert drift["unresolved"] == 1
    retry = result["by_question_family"]["retry_relation"]
    assert retry["query_opportunities"] == 1
    assert retry["query_attempts"] == 0


def test_session_semantic_metrics_leave_uninstrumented_history_unobserved():
    result = aggregate_semantic_transitions([
        {"semantic_query_attempted": True,
         "semantic_question_ids": ["retry_relation"]},
    ])

    assert result["by_question_family"]["retry_relation"]["query_attempts"] == 1
    assert result["by_question_family"]["retry_relation"]["status"] == "not_observed"
