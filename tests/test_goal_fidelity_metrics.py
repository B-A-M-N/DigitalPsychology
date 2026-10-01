from __future__ import annotations

from lib.feedback_loop import Event, EventFactory, goal_fidelity_metrics
from lib.routing_profiles import comparison_context


def _event(event_id: str, category: str, event_type: str, second: int,
           *, goal_hash: str | None = None, payload: dict | None = None) -> Event:
    return Event(
        event_id=event_id,
        timestamp=f"2026-09-30T00:00:{second:02d}Z",
        task_id="goal-task",
        agent_id="agent",
        category=category,
        event_type=event_type,
        namespace_id="test",
        application_id="app",
        application_instance_id="instance",
        agent_instance_id="agent-instance",
        session_id="session",
        behavioral_subject="component",
        goal_contract_hash=goal_hash,
        payload=payload or {},
    )


def test_event_round_trip_preserves_goal_contract_hash():
    event = _event("e1", "action", "action_requested", 1,
                   goal_hash="a" * 64, payload={"route": "write"})

    restored = EventFactory.from_dict(event.to_dict())

    assert restored.goal_contract_hash == "a" * 64
    assert restored.to_dict()["goal_contract_hash"] == "a" * 64


def test_goal_fidelity_metrics_distinguish_progress_support_and_verification():
    events = [
        _event("e1", "action", "action_requested", 1,
               payload={"route": "inspect", "progress_class": "VERIFICATION_PROGRESS"}),
        _event("e2", "tool_result", "tool_result", 2,
               payload={"tool_type": "write"}),
        _event("e3", "decision", "goal_progress_recorded", 3,
               payload={"progress_class": "GOAL_PROGRESS"}),
        _event("e3b", "tool_result", "support_action_recorded", 3,
               payload={"progress_class": "DIAGNOSTIC_PROGRESS"}),
        _event("e4", "action", "action_requested", 4,
               payload={"route": "test", "progress_class": "VERIFICATION_PROGRESS"}),
        _event("e4b", "validation", "validator_result", 4,
               payload={"progress_class": "VALIDATION_EVIDENCE"}),
        _event("e5", "completion", "completion_attempted", 5,
               payload={"completion_criterion_coverage": 0.5}),
        _event("e6", "correction", "intent_deviation_detected", 6,
               payload={"intent_deviation": True,
                        "scope_expansion": True,
                        "scope_expansion_necessary": False}),
    ]

    metrics = goal_fidelity_metrics(events)

    assert metrics == {
        "goal_progress_ratio": 1 / 3,
        "support_actions_since_goal_progress": 1,
        "verification_action_ratio": 2 / 3,
        "validation_evidence_count": 1,
        "intent_deviation_count": 1,
        "unnecessary_scope_expansion": 1,
        "completion_criterion_coverage": 0.5,
    }


def test_comparison_context_uses_goal_dimensions_without_exact_policy_hash():
    baseline = comparison_context({
        "work_mode": "delivery", "deliverable_kind": "visual_ui",
        "acceptance_criterion_kinds": ["static_visual"],
        "policy_hash": "task-specific-policy-a",
    })
    equivalent = comparison_context({
        "work_mode": "delivery", "deliverable_kind": "visual_ui",
        "acceptance_criterion_kinds": ["static_visual"],
        "policy_hash": "task-specific-policy-b",
    })
    changed_mode = comparison_context({
        "work_mode": "review", "deliverable_kind": "visual_ui",
        "acceptance_criterion_kinds": ["static_visual"],
        "policy_hash": "task-specific-policy-b",
    })

    assert baseline == equivalent
    assert baseline != changed_mode
    assert "policy_hash" not in baseline
