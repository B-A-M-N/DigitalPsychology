"""Tests for DecisionOutcomeRecord.

The critical property is TIER SEPARATION. A contract validator can establish that
an action was legal; it cannot establish that an otherwise legal StateWork was the
best choice. If those collapse, a weakly-labelled record masquerades as an
independently labelled one -- the exact circularity the corpus schema rejects.
"""
from __future__ import annotations

import pytest

from qualification.decision_outcome import (
    AfterRecord,
    BeforeRecord,
    DecisionOutcomeRecord,
    DuringRecord,
    derive_contract_violations,
    new_correlation_id,
    validate_episode,
)


def _before(**kw):
    base = dict(
        task_context={"objective": "Establish repository truth."},
        candidate_stateworks=("observe", "mutate", "verify"),
        controller_selected="observe",
        controller_reason="repository state unknown",
        laya_question_id="route_preference",
        laya_selected="observe", laya_confidence=0.72, laya_margin=0.24,
        laya_distribution={"observe": 0.72, "verify": 0.48, "mutate": 0.30},
    )
    base.update(kw)
    return BeforeRecord(**base)


def _during(**kw):
    base = dict(
        actions=(("read_file", False), ("run_tests", True), ("run_tests", False)),
        transitions_rejected=(), gate_blocks=(), retries=1)
    base.update(kw)
    return DuringRecord(**base)


def _after(**kw):
    base = dict(
        active_stateworks=("observe",),
        required_evidence=("repository_truth",),
        evidence_satisfied=("repository_truth",),
        task_success=True)
    base.update(kw)
    return AfterRecord(**base)


def _record(**kw):
    base = dict(
        correlation_id=new_correlation_id(seed="episode-1"),
        recorded_at="2026-10-13T00:00:00Z",
        before=_before(), during=_during(), after=_after(),
        checkpoint_revision="1a793eb", checkpoint_content_hash="c" * 64,
        statework_contract_version="getter-v1", policy_snapshot_hash="1" * 64,
        trajectory_id="traj-1", subject_ref="repo:example")
    base.update(kw)
    return DecisionOutcomeRecord(**base)


# ------------------------------------------------------- tier separation
def test_contract_validity_is_machine_derivable():
    """Legality is checkable without any semantic judgement."""
    record = _record()
    assert record.contract_validity == "legal"

    illegal = _record(
        contract_violations=("transition rejected by CSW: mutate",))
    assert illegal.contract_validity == "illegal"


def test_semantic_preference_is_not_machine_derived():
    """An unlabelled record must NOT silently acquire a semantic target.

    This is the load-bearing property: contract validity is automatic, the
    semantic preference is not. Conflating them is what makes a corpus circular.
    """
    record = _record()
    assert record.semantic_preference is None
    assert record.controller_was_correct is None
    assert record.laya_was_correct is None


def test_labelled_record_scores_both_controller_and_laya():
    """With an independent label, both selections become comparable."""
    record = _record(
        semantic_target="verify", semantic_label_provenance="independent_expert")
    assert record.semantic_preference == "verify"
    assert record.controller_was_correct is False
    assert record.laya_was_correct is False


def test_semantic_target_must_be_a_candidate():
    with pytest.raises(ValueError, match="outside the candidate"):
        _record(semantic_target="not-a-route")


def test_provenance_without_a_target_is_rejected():
    with pytest.raises(ValueError, match="provenance requires"):
        _record(semantic_label_provenance="someone")


# ------------------------------------------------------- counterfactual honesty
def test_improvement_claim_requires_an_observed_counterfactual():
    """Observing a Laya route succeed does not prove the deterministic route
    would have failed. That claim is structurally impossible without the
    counterfactual."""
    with pytest.raises(ValueError, match="counterfactual"):
        _record(after=AfterRecord(
            active_stateworks=("observe",),
            required_evidence=("repository_truth",),
            evidence_satisfied=("repository_truth",),
            task_success=True, counterfactual_available=False,
            intervention_improved=True))


def test_validate_flags_an_unsupported_improvement_claim():
    record = _record()
    # Construct through the dataclass to bypass __post_init__ for this check.
    object.__setattr__(record.after, "intervention_improved", True)
    problems = validate_episode(record)
    assert any("without an observed counterfactual" in p for p in problems)


def test_unknown_counterfactual_stays_unknown():
    after = _after()
    assert after.counterfactual_available is False
    assert after.intervention_improved is None


# ------------------------------------------------------- contract derivation
def test_violations_are_derived_from_constraints():
    derived = derive_contract_violations(
        _before(controller_selected="mutate"),
        _during(transitions_rejected=("mutate",), gate_blocks=("ward:blast_radius",)),
        _after(required_evidence=("intent", "risk"),
               evidence_satisfied=("intent",)))
    assert derived == (
        "controller selected 'mutate' but it is not active after execution",
        "transition rejected by CSW: mutate",
        "action blocked by CFW gate: ward:blast_radius",
        "required evidence unsatisfied: ['risk']",
    )


def test_no_violations_means_legal():
    assert derive_contract_violations(_before(), _during(), _after()) == ()


# ------------------------------------------------------- identity + reuse
def test_correlation_id_is_locally_generated_and_bounded():
    first = new_correlation_id(seed="episode-a")
    second = new_correlation_id(seed="episode-a")
    other = new_correlation_id(seed="episode-b")
    assert first == second, "the same seed must yield a stable ID"
    assert first != other
    assert first.startswith("dor-")


def test_serialization_round_trip_preserves_all_tiers():
    record = _record(
        contract_violations=("transition rejected by CSW: mutate",),
        semantic_target="verify", semantic_label_provenance="independent_expert")
    payload = record.to_dict()

    assert payload["labels"]["contract_validity"] == "illegal"
    assert payload["labels"]["observed_outcome"] == "succeeded"
    assert payload["labels"]["semantic_preference"] == "verify"
    # The three tiers are separate keys, not one merged verdict.
    assert set(payload["labels"]) >= {
        "contract_validity", "observed_outcome", "semantic_preference"}
    # Identity binding survives.
    assert payload["identity"]["checkpoint_revision"] == "1a793eb"


def test_candidate_set_is_validated():
    with pytest.raises(ValueError, match="unique"):
        _before(candidate_stateworks=("observe", "observe"))
    with pytest.raises(ValueError, match="outside the candidate"):
        _before(controller_selected="unregistered")


def test_laya_confidence_is_bounded():
    with pytest.raises(ValueError, match="between zero and one"):
        _before(laya_confidence=1.4)
