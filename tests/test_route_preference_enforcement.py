"""Regression tests for P0-A (experimental provenance) and P0-B (route margin).

These tests encode the invariants the implementation pass must restore:

  P0-A  A host-authorized experimental route assignment is NOT a calibrated
        model prediction. It must not satisfy the semantic confidence gate by
        asserting confidence=1.0. Provenance must be explicit and auditable.

  P0-B  rank_stateworks() must fail closed on ambiguous / malformed input and
        must not reorder on a tie. Per-decision ambiguity is a separate rule
        from the Stage-B aggregate margin floor (0.15 over the benchmark mean).
"""
from __future__ import annotations

import pytest

from lib.behavior_state import StateWorkOption
from lib.control.statework_selector import (
    rank_stateworks,
    statework_scores,
)


def _eligible() -> tuple[StateWorkOption, ...]:
    return (
        StateWorkOption("repair", behavior_tags=("diagnose",), purpose="Repair"),
        StateWorkOption("verify", behavior_tags=("verify",), purpose="Verify"),
    )


# ---------------------------------------------------------------- P0-A
def test_host_experimental_assignment_cannot_satisfy_semantic_confidence_gate():
    """A forced experimental preference must not present as a confident prediction."""
    eligible = _eligible()
    # Undeclared provenance with an asserted confidence must fail closed rather
    # than being accepted as a confident model prediction.
    with pytest.raises(ValueError):
        rank_stateworks(
            "DIAGNOSE", eligible, semantic_preference="verify",
            semantic_confidence=1.0, minimum_semantic_confidence=0.80)
    # Deterministic baseline for this mode is unchanged.
    assert rank_stateworks("DIAGNOSE", eligible) == ("repair", "verify")

    # Post-fix: an explicitly host-authorized experiment still reorders, but it
    # must declare itself so provenance is auditable.
    authorized = rank_stateworks(
        "DIAGNOSE", eligible, semantic_preference="verify",
        semantic_confidence=1.0, minimum_semantic_confidence=0.80,
        provenance="host_experiment")
    assert authorized == ("verify", "repair")


def test_unknown_provenance_fails_closed():
    eligible = _eligible()
    with pytest.raises(ValueError):
        rank_stateworks(
            "DIAGNOSE", eligible, semantic_preference="verify",
            semantic_confidence=1.0, provenance="not_a_real_provenance")


def test_synthetic_confidence_cannot_qualify_a_model():
    """Confidence above the threshold is still not qualification."""
    eligible = _eligible()
    # 1.0 confidence with no provenance is a synthetic value; it must not be
    # accepted as a model prediction.
    with pytest.raises(ValueError):
        rank_stateworks(
            "DIAGNOSE", eligible, semantic_preference="verify",
            semantic_confidence=1.0)


def test_model_provenance_still_reorders_when_qualified():
    eligible = _eligible()
    ranked = rank_stateworks(
        "DIAGNOSE", eligible, semantic_preference="verify",
        semantic_confidence=0.95, provenance="model_advisory",
        preference_support=(("verify", 0.90), ("repair", 0.10)))
    assert ranked == ("verify", "repair")


# ---------------------------------------------------------------- P0-B
@pytest.mark.parametrize("confidence", [0.0, 0.5, 0.79, 1.0])
def test_tie_preference_cannot_reorder(confidence):
    """A 0.50/0.50 split carries no information; it must not reorder."""
    eligible = _eligible()
    ranked = rank_stateworks(
        "DIAGNOSE", eligible, semantic_preference="verify",
        semantic_confidence=confidence, provenance="model_advisory",
        preference_support=(("verify", 0.5), ("repair", 0.5)))
    assert ranked == ("repair", "verify"), "a tie must not reorder"


def test_near_tie_below_margin_cannot_reorder():
    eligible = _eligible()
    ranked = rank_stateworks(
        "DIAGNOSE", eligible, semantic_preference="verify",
        semantic_confidence=0.95, provenance="model_advisory",
        preference_support=(("verify", 0.52), ("repair", 0.48)))
    assert ranked == ("repair", "verify")


def test_clear_preference_reorders():
    eligible = _eligible()
    ranked = rank_stateworks(
        "DIAGNOSE", eligible, semantic_preference="verify",
        semantic_confidence=0.95, provenance="model_advisory",
        preference_support=(("verify", 0.90), ("repair", 0.10)))
    assert ranked == ("verify", "repair")


def test_confidence_exactly_at_threshold_with_clear_support_reorders():
    eligible = _eligible()
    ranked = rank_stateworks(
        "DIAGNOSE", eligible, semantic_preference="verify",
        semantic_confidence=0.80, provenance="model_advisory",
        preference_support=(("verify", 0.90), ("repair", 0.10)))
    assert ranked == ("verify", "repair")


def test_margin_exactly_at_boundary_reorders():
    eligible = _eligible()
    ranked = rank_stateworks(
        "DIAGNOSE", eligible, semantic_preference="verify",
        semantic_confidence=0.95, provenance="model_advisory",
        preference_support=(("verify", 0.60), ("repair", 0.40)))
    assert ranked == ("verify", "repair")


@pytest.mark.parametrize("bad", [
    (("verify", float("nan")), ("repair", 0.4)),
    (("verify", float("inf")), ("repair", 0.4)),
    (("verify", 1.5), ("repair", 0.4)),
    (("verify", -0.1), ("repair", 0.4)),
    (("unregistered", 0.9), ("repair", 0.1)),  # unknown identity
    (("verify", 0.9), ("verify", 0.1)),     # duplicate
])
def test_malformed_support_fails_closed(bad):
    eligible = _eligible()
    with pytest.raises(ValueError):
        rank_stateworks(
            "DIAGNOSE", eligible, semantic_preference="verify",
            semantic_confidence=0.95, provenance="model_advisory",
            preference_support=bad)


def test_ineligible_preference_never_reorders():
    eligible = _eligible()
    ranked = rank_stateworks(
        "DIAGNOSE", eligible, semantic_preference="unregistered",
        semantic_confidence=0.99, provenance="model_advisory",
        preference_support=(("unregistered", 0.9), ("repair", 0.1)))
    assert ranked == ("repair", "verify")


def test_deterministic_scores_unchanged():
    """The deterministic baseline ranking must be untouched by this change."""
    eligible = _eligible()
    assert statework_scores("DIAGNOSE", eligible) == (("repair", 1), ("verify", 0))


def test_single_entry_support_cannot_reorder():
    """A distribution with no rival cannot demonstrate a margin."""
    eligible = _eligible()
    ranked = rank_stateworks(
        "DIAGNOSE", eligible, semantic_preference="verify",
        semantic_confidence=0.95, provenance="model_advisory",
        preference_support=(("verify", 0.9),))
    assert ranked == ("repair", "verify")


# ------------------------------------------------- end-to-end (full controller)
def _tie_judgment(preferred: str, rival: str, confidence: float):
    """A route_preference judgment with an explicitly ambiguous distribution."""

    class Judgment:
        def answer(self, question_id):
            if question_id != "route_preference":
                return None

            class P:
                def __init__(self, label, probability):
                    self.label = label
                    self.probability = probability

            return type("Answer", (), {
                "selected": preferred,
                "raw_confidence": confidence,
                "calibrated_confidence": confidence,
                "raw_probabilities": (P(preferred, 0.5), P(rival, 0.5)),
                "calibrated_probabilities": None,
            })()

    return Judgment()


def _decide_with(judgment):
    from lib.behavior_state import (
        BehaviorState,
        CSWBehaviorSnapshot,
        StateWorkOption,
    )
    from lib.control.fast_loop_input import FastLoopInput
    from lib.fast_loop import BehavioralController

    csw = CSWBehaviorSnapshot(
        revision=1,
        eligible_stateworks=(
            StateWorkOption("diagnose-flow", behavior_tags=("diagnose",)),
            StateWorkOption("verify-flow", behavior_tags=("verify",)),
        ),
    )
    return BehavioralController().decide(
        FastLoopInput(
            scope="tenant/app/agent/session/task/attempt",
            state=BehaviorState(cognitive_mode="DIAGNOSE"),
            csw_snapshot=csw,
            objective_criteria=("criterion-1",),
            semantic_opportunity_question_id="route_preference",
        ),
        judgment=judgment,
    )


def test_tied_route_preference_cannot_reorder_through_full_controller():
    """End-to-end: an ambiguous preference must not reach a prefer_statework directive."""
    decision = _decide_with(_tie_judgment("verify-flow", "diagnose-flow", 0.99))
    assert "verify-flow" not in tuple(decision.preferred_stateworks)
    assert not any(item.operation == "prefer_statework"
                   and item.target == "verify-flow"
                   for item in decision.directives)


def test_decisive_route_preference_can_still_reorder_through_full_controller():
    """End-to-end: the legitimate advisory surface remains reachable."""

    class Judgment:
        def answer(self, question_id):
            if question_id != "route_preference":
                return None

            class P:
                def __init__(self, label, probability):
                    self.label = label
                    self.probability = probability

            return type("Answer", (), {
                "selected": "verify-flow",
                "raw_confidence": 0.96,
                "calibrated_confidence": 0.96,
                "raw_probabilities": (P("verify-flow", 0.90), P("diagnose-flow", 0.10)),
                "calibrated_probabilities": None,
            })()

    decision = _decide_with(Judgment())
    assert decision.preferred_stateworks == ("verify-flow",)
    assert any(item.operation == "prefer_statework" and item.target == "verify-flow"
               for item in decision.directives)
