"""Advisory semantic families: decomposition / integration / test_strategy.

Slice B of the policy adaptive layer. The three new families mirror the
route_preference plumbing (preference / confidence / support per family,
ranked through rank_stateworks with model_advisory provenance, limit 1)
but are advisory-only: a family absent from the advisory set is shadow
(no reorder), and route behaviour is unchanged.
"""
from __future__ import annotations

import pytest

from lib.behavior_state import (
    BehaviorState,
    CSWBehaviorSnapshot,
    StateWorkOption,
)
from lib.control.fast_loop_input import FastLoopInput
from lib.control.semantic_assessment import SemanticAssessment, assess_semantics
from lib.control.statework_selector import rank_stateworks
from lib.fast_loop import BehavioralController


NEW_FAMILIES = (
    ("decomposition", "decomposition_preference"),
    ("integration", "integration_preference"),
    ("test_strategy", "test_strategy_preference"),
)


class _P:
    def __init__(self, label, probability):
        self.label = label
        self.probability = probability


def _answer(selected, raw_confidence, raw_dist,
            calibrated_confidence=None, calibrated_dist=None):
    raw_probs = tuple(_P(label, prob) for label, prob in raw_dist)
    if calibrated_dist is None:
        calibrated_probs = None
    else:
        calibrated_probs = tuple(_P(label, prob) for label, prob in calibrated_dist)
    return type("Answer", (), {
        "selected": selected,
        "raw_confidence": raw_confidence,
        "calibrated_confidence": calibrated_confidence,
        "raw_probabilities": raw_probs,
        "calibrated_probabilities": calibrated_probs,
    })()


def _judgment(entries):
    """entries: question_id -> Answer (missing key answers None)."""

    class Judgment:
        def answer(self, question_id):
            return entries.get(question_id)

    return Judgment()


def _family_entries(question_id, preferred, rival, confidence, *,
                    tie=False, calibrated=True):
    if tie:
        dist = ((preferred, 0.5), (rival, 0.5))
    else:
        dist = ((preferred, 0.9), (rival, 0.1))
    if calibrated:
        return {question_id: _answer(preferred, confidence, dist,
                                     calibrated_confidence=confidence,
                                     calibrated_dist=dist)}
    return {question_id: _answer(preferred, confidence, dist)}


# ------------------------------------------------ assessment parsing

def test_assessment_parses_new_families_with_calibrated_confidence():
    judgment = _judgment({
        "decomposition_preference": _answer(
            "split-flow", 0.60, (("split-flow", 0.55), ("merge-flow", 0.45)),
            calibrated_confidence=0.95,
            calibrated_dist=(("split-flow", 0.90), ("merge-flow", 0.10))),
    })
    assessment = assess_semantics(judgment)
    assert assessment.decomposition_preference == "split-flow"
    assert assessment.decomposition_confidence == 0.95
    assert assessment.decomposition_support == (
        ("split-flow", 0.90), ("merge-flow", 0.10))
    # Untouched families stay at their defaults.
    assert assessment.integration_preference is None
    assert assessment.integration_confidence == 0.0
    assert assessment.integration_support == ()
    assert assessment.test_strategy_preference is None


def test_assessment_falls_back_to_raw_confidence_and_support():
    judgment = _judgment({
        "test_strategy_preference": _answer(
            "verify-flow", 0.70, (("verify-flow", 0.70), ("diagnose-flow", 0.30))),
    })
    assessment = assess_semantics(judgment)
    assert assessment.test_strategy_preference == "verify-flow"
    assert assessment.test_strategy_confidence == 0.70
    assert assessment.test_strategy_support == (
        ("verify-flow", 0.70), ("diagnose-flow", 0.30))


def test_assessment_empty_judgment_leaves_new_families_at_defaults():
    assert assess_semantics(None) == SemanticAssessment()
    assessment = assess_semantics(_judgment({}))
    assert assessment.decomposition_preference is None
    assert assessment.decomposition_confidence == 0.0
    assert assessment.decomposition_support == ()
    assert assessment.integration_preference is None
    assert assessment.integration_confidence == 0.0
    assert assessment.integration_support == ()
    assert assessment.test_strategy_preference is None
    assert assessment.test_strategy_confidence == 0.0
    assert assessment.test_strategy_support == ()


def test_assessment_confidence_max_includes_new_families():
    judgment = _judgment({
        "integration_preference": _answer(
            "merge-flow", 0.88, (("merge-flow", 0.88), ("split-flow", 0.12))),
    })
    assert assess_semantics(judgment).confidence == 0.88


@pytest.mark.parametrize("field", [
    "decomposition_preference", "integration_preference",
    "test_strategy_preference",
])
def test_assessment_rejects_unbounded_new_family_preference(field):
    with pytest.raises(ValueError):
        SemanticAssessment(**{field: ""})
    with pytest.raises(ValueError):
        SemanticAssessment(**{field: "x" * 129})


@pytest.mark.parametrize("field", [
    "decomposition_confidence", "integration_confidence",
    "test_strategy_confidence",
])
def test_assessment_rejects_out_of_range_new_family_confidence(field):
    with pytest.raises(ValueError):
        SemanticAssessment(**{field: 1.5})


# ------------------------------------------------ full controller

def _decide_with(judgment, advisory=()):
    csw = CSWBehaviorSnapshot(
        revision=1,
        eligible_stateworks=(
            StateWorkOption("diagnose-flow", behavior_tags=("diagnose",)),
            StateWorkOption("verify-flow", behavior_tags=("verify",)),
        ),
    )
    policy_context = {}
    if advisory:
        policy_context["advisory_semantic_families"] = list(advisory)
    return BehavioralController().decide(
        FastLoopInput(
            scope="tenant/app/agent/session/task/attempt",
            state=BehaviorState(cognitive_mode="DIAGNOSE"),
            csw_snapshot=csw,
            objective_criteria=("criterion-1",),
            semantic_opportunity_question_id="route_preference",
            policy_context=policy_context,
        ),
        judgment=judgment,
    )


@pytest.mark.parametrize("family,question_id", NEW_FAMILIES)
def test_shadow_by_default_no_reorder_without_advisory_membership(
        family, question_id):
    """A decisive advisory preference with no advisory membership is shadow."""
    judgment = _judgment(_family_entries(
        question_id, "verify-flow", "diagnose-flow", 0.96))
    decision = _decide_with(judgment)
    assert "verify-flow" not in tuple(decision.preferred_stateworks)
    assert not any(item.operation == "prefer_statework"
                   and item.target == "verify-flow"
                   for item in decision.directives)


@pytest.mark.parametrize("family,question_id", NEW_FAMILIES)
def test_enabled_decisive_advisory_reorders(family, question_id):
    judgment = _judgment(_family_entries(
        question_id, "verify-flow", "diagnose-flow", 0.96))
    decision = _decide_with(judgment, advisory=(family,))
    assert decision.preferred_stateworks == ("verify-flow",)
    assert any(item.operation == "prefer_statework"
               and item.target == "verify-flow"
               for item in decision.directives)


@pytest.mark.parametrize("family,question_id", NEW_FAMILIES)
def test_enabled_tie_cannot_reorder(family, question_id):
    judgment = _judgment(_family_entries(
        question_id, "verify-flow", "diagnose-flow", 0.99, tie=True))
    decision = _decide_with(judgment, advisory=(family,))
    assert "verify-flow" not in tuple(decision.preferred_stateworks)
    assert not any(item.operation == "prefer_statework"
                   and item.target == "verify-flow"
                   for item in decision.directives)


def test_missing_provenance_fails_closed():
    eligible = (
        StateWorkOption("repair", behavior_tags=("diagnose",), purpose="Repair"),
        StateWorkOption("verify", behavior_tags=("verify",), purpose="Verify"),
    )
    with pytest.raises(ValueError):
        rank_stateworks(
            "DIAGNOSE", eligible, semantic_preference="verify",
            semantic_confidence=0.95,
            preference_support=(("verify", 0.90), ("repair", 0.10)))


def test_route_preference_unchanged_without_advisory_set():
    """Route keeps its enforced path: decisive reorders, tie does not."""

    class P:
        def __init__(self, label, probability):
            self.label = label
            self.probability = probability

    def route_judgment(top, second):
        return _judgment({"route_preference": type("Answer", (), {
            "selected": "verify-flow",
            "raw_confidence": 0.96,
            "calibrated_confidence": 0.96,
            "raw_probabilities": (P("verify-flow", top), P("diagnose-flow", second)),
            "calibrated_probabilities": None,
        })()})

    decisive = _decide_with(route_judgment(0.90, 0.10))
    assert decisive.preferred_stateworks == ("verify-flow",)
    assert any(item.operation == "prefer_statework"
               and item.target == "verify-flow"
               for item in decisive.directives)

    tied = _decide_with(route_judgment(0.50, 0.50))
    assert "verify-flow" not in tuple(tied.preferred_stateworks)
