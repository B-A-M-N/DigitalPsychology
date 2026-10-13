import pytest

from lib.behavior_state import StateWorkOption
from lib.control.statework_selector import (eligible_statework_descriptions,
                                            rank_stateworks, statework_ambiguity)


def test_semantic_preference_reorders_only_csw_eligible_stateworks():
    eligible = (
        StateWorkOption("repair", behavior_tags=("diagnose",), purpose="Repair"),
        StateWorkOption("verify", behavior_tags=("verify",), purpose="Verify"),
    )

    ranked = rank_stateworks(
        "DIAGNOSE", eligible, semantic_preference="verify",
        semantic_confidence=0.95, provenance="model_advisory",
        preference_support=(("verify", 0.90), ("repair", 0.10)))
    forged = rank_stateworks(
        "DIAGNOSE", eligible, semantic_preference="unregistered",
        semantic_confidence=0.99, provenance="model_advisory",
        preference_support=(("unregistered", 0.9), ("repair", 0.1)))

    assert ranked == ("verify", "repair")
    assert forged == ("repair", "verify")


def test_route_preference_requires_explicit_provenance():
    """An undeclared preference source must fail closed, not be trusted."""
    eligible = (
        StateWorkOption("repair", behavior_tags=("diagnose",), purpose="Repair"),
        StateWorkOption("verify", behavior_tags=("verify",), purpose="Verify"),
    )
    with pytest.raises(ValueError):
        rank_stateworks(
            "DIAGNOSE", eligible, semantic_preference="verify",
            semantic_confidence=1.0)


def test_statework_description_and_ambiguity_are_bounded_to_eligible_items():
    eligible = (
        StateWorkOption("repair", behavior_tags=("diagnose",), purpose="Inspect failure"),
        StateWorkOption("verify", behavior_tags=("diagnose",), purpose="Verify output"),
        StateWorkOption("active", behavior_tags=("diagnose",), purpose="Already active"),
    )
    ambiguity = statework_ambiguity("DIAGNOSE", eligible, active=("active",))
    descriptions = eligible_statework_descriptions(eligible, active=("active",))

    assert ambiguity is not None
    assert {ambiguity.top_candidate_id, ambiguity.second_candidate_id} == {
        "repair", "verify"}
    assert tuple(item[0] for item in descriptions) == ("repair", "verify")
    assert all(len(description) <= 512 for _, description in descriptions)
