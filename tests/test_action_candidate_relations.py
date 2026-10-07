from __future__ import annotations

import pytest

from lib.runtime.contracts import ActionCandidate
from lib.system_one_contracts import ActivityFact


def _candidate(*, action_class="diagnostic", criteria=(), certainty="unknown",
               mutating=False):
    return ActionCandidate(
        action_id="inspect-1", action_class=action_class,
        proposed_purpose="Inspect the active failure", affected_criterion_ids=criteria,
        tool_name="read_file", operation="inspect",
        mutating=mutating, reversible=True, relation_certainty=certainty,
    )


@pytest.mark.parametrize("action_class", ["diagnostic", "support", "read", "read_file"])
def test_diagnostic_support_and_read_actions_can_have_unknown_criterion_relation(action_class):
    candidate = _candidate(action_class=action_class)

    assert candidate.affected_criterion_ids == ()
    assert candidate.relation_certainty == "unknown"
    assert candidate.to_dict()["relation_certainty"] == "unknown"


def test_mutating_diagnostic_action_can_remain_unattributed_before_execution():
    candidate = _candidate(action_class="diagnostic", mutating=True)

    assert candidate.affected_criterion_ids == ()
    assert candidate.mutating


def test_uncertain_write_can_remain_unattributed_before_execution():
    candidate = _candidate(action_class="write", mutating=True)

    assert candidate.affected_criterion_ids == ()
    assert candidate.relation_certainty == "unknown"
    assert candidate.to_dict()["affected_criterion_ids"] == []


def test_goal_directed_action_still_requires_explicit_or_inferred_criterion_binding():
    with pytest.raises(ValueError, match="criterion-free candidates"):
        _candidate(action_class="file_edit", certainty="explicit")
    with pytest.raises(ValueError, match="unknown relation certainty"):
        _candidate(action_class="diagnostic", criteria=("criterion-1",))


def test_semantic_activity_fact_preserves_unknown_relation_certainty():
    fact = ActivityFact(
        sequence=1, activity_type="action", source="model_proposed",
        action_class="diagnostic", host_declared_relation="ambiguous",
        affected_criterion_ids=(), relation_certainty="unknown",
    )

    assert fact.to_dict()["affected_criterion_ids"] == []
    assert fact.to_dict()["relation_certainty"] == "unknown"
