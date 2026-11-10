"""Tests for the Laya training-data exporter.

The upstream JSONL schema ({state, questions, gold}) is consumed by
`laya.train.finetune`, which ships in laya >= 0.4.6. The pinned .venv-laya has
0.3.22, so these tests validate the CONTRACT locally rather than by running
training. Each check below mirrors a documented upstream rejection reason.
"""
from __future__ import annotations

import json
from pathlib import Path

from qualification.contrastive_fixtures import generate_cases
from qualification.training_export import (
    QUESTION_ID,
    ROUTE_LABELS,
    build_gold,
    build_question,
    build_state,
    export_rows,
    split_disjoint,
    write_jsonl,
)


def _rows():
    return export_rows(generate_cases())


def test_row_shape_matches_upstream_schema():
    """{state, questions, gold} with the ids matching on both sides."""
    for row in _rows():
        assert set(row) == {"state", "questions", "gold"}
        assert set(row["questions"]) == set(row["gold"]) == {QUESTION_ID}
        question = row["questions"][QUESTION_ID]
        # The PUBLIC contract _check_question reads, NOT the internal shape
        # _to_internal produces.
        assert question["type"] == "choice"
        assert isinstance(question["criteria"], dict) and question["criteria"]
        assert isinstance(question["instructions"], str)
        assert question["instructions"].strip()


def test_gold_probabilities_keyed_by_option_label():
    """laya's target_from_gold looks probabilities up by CHOICE LABEL."""
    for row in _rows():
        labels = set(row["questions"][QUESTION_ID]["criteria"])
        probs = row["gold"][QUESTION_ID]["probabilities"]
        assert set(probs) == labels, (set(probs), labels)
        assert all(v >= 0 for v in probs.values())


def test_probabilities_are_normalised():
    """target_from_gold normalises; we should not depend on that fallback."""
    for row in _rows():
        probs = row["gold"][QUESTION_ID]["probabilities"]
        total = sum(probs.values())
        assert abs(total - 1.0) < 1e-6, total


def test_unambiguous_gold_is_a_point_mass():
    cases = generate_cases()
    definite = [c for c in cases if c.gold is not None]
    assert definite
    for case in definite:
        probs = build_gold(case)["probabilities"]
        expected = {ROUTE_LABELS[s]: (1.0 if s == case.gold else 0.0)
                    for s in case.eligible}
        assert probs == expected
        # Labels must be the StateWork ids, matching the production adapter.
        assert set(probs) == set(case.eligible)


def test_ambiguous_gold_is_uniform_and_declared():
    """A no-preference case must be trainable as no-preference, not dropped."""
    cases = generate_cases()
    ambiguous = [c for c in cases if c.gold is None]
    assert ambiguous, "a set with no ambiguous cases teaches guessing"
    for case in ambiguous:
        probs = build_gold(case)["probabilities"]
        share = 1.0 / len(probs)
        assert all(abs(v - share) < 1e-6 for v in probs.values())
        assert case.ambiguity_reason


def test_options_carry_obligation_but_not_the_answer():
    """The load-bearing anti-leakage property.

    An option may state its own obligation and whether that evidence is held. It
    must NOT state that it is the answer, or training optimises a leak.
    """
    for row in _rows():
        for label, text in row["questions"][QUESTION_ID]["criteria"].items():
            assert text != ""
            # No option may assert it is correct/incorrect.
            lowered = text.lower()
            for banned in ("correct", "this is the answer", "should be selected",
                           "preferred", "gold", "winner"):
                assert banned not in lowered, f"{label}: {text!r} leaks {banned!r}"


def test_option_text_reveals_held_vs_missing_evidence():
    """The information that makes the choice decidable must survive."""
    cases = [c for c in generate_cases() if c.gold is not None][:20]
    assert cases
    for case in cases:
        assert case.gold is not None
        winner_text = build_question(case)["criteria"][ROUTE_LABELS[case.gold]]
        # The correct route's obligation must be satisfiable from held evidence.
        assert "missing required evidence" not in winner_text, (
            f"{case.case_id}: gold {case.gold} is selected but its evidence is "
            "missing -- the derivation and the text disagree")
        held = set(case.held_evidence)
        obligation = set(case.obligations[case.gold])
        assert obligation <= held


def test_splits_are_disjoint_by_case():
    train, calibration, holdout = split_disjoint(generate_cases())
    train_ids = {c.case_id for c in train}
    calib_ids = {c.case_id for c in calibration}
    holdout_ids = {c.case_id for c in holdout}
    assert not (train_ids & calib_ids)
    assert not (train_ids & holdout_ids)
    assert not (calib_ids & holdout_ids)
    assert train_ids | calib_ids | holdout_ids == {
        c.case_id for c in generate_cases()}


def test_holdout_shares_no_task_instance_with_train():
    """Holdout independence must be by TASK INSTANCE, not by case id.

    The earlier split separated rotations, which left related cases about the
    same referent in different partitions -- a leak a unique case_id cannot
    detect. The assertion is therefore on the instance field.
    """
    train, calib, holdout = split_disjoint(generate_cases())
    train_instances = {c.task_instance for c in train}
    calib_instances = {c.task_instance for c in calib}
    holdout_instances = {c.task_instance for c in holdout}
    assert holdout_instances
    assert not (holdout_instances & train_instances), (
        "a held-out task instance also appears in train")
    assert not (holdout_instances & calib_instances), (
        "a held-out task instance also appears in calibration")


def test_holdout_contains_contradictory_golds():
    """Holdout must still be adversarial to name-matching.

    Instance grouping must not cost this property: at least one held-out case
    must have a gold that is NOT the route the canonical assignment rewards.
    """
    from qualification.contrastive_fixtures import _assign_obligations
    _train, _calib, holdout = split_disjoint(generate_cases())
    contradictions = 0
    for case in holdout:
        canonical = _assign_obligations(case.capability, 0)
        if (case.gold is not None and case.permutation
                and canonical.get(case.gold) != case.obligations.get(case.gold)):
            contradictions += 1
    assert contradictions > 0, (
        "no held-out gold contradicts the canonical assignment; a name-matching "
        "model could pass this holdout")


def test_write_jsonl_round_trips(tmp_path: Path):
    cases = generate_cases()[:7]
    path = tmp_path / "train.jsonl"
    written = write_jsonl(path, cases)
    assert written == len(cases)
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(lines) == len(cases)
    for row in lines:
        assert set(row) == {"state", "questions", "gold"}


def test_state_matches_the_canonical_serving_form():
    """The state must be the canonical one, not a private variant.

    The capability label is intentionally NOT in the state: it is a
    generator-side bookkeeping field, and emitting it taught the model to
    shortcut on a field production never provides. The task, eligible routes
    and held evidence are the facts both sides share.
    """
    from qualification.canonical_input import canonical_state
    cases = generate_cases()
    for case in cases:
        state = build_state(case)
        assert case.objective in state
        assert state == canonical_state(case.objective, case.eligible,
                                        case.held_evidence)
        for sid in case.eligible:
            assert sid in state


def test_state_does_not_leak_generator_bookkeeping():
    """`capability` must not appear -- it is not a production fact."""
    cases = generate_cases()
    for case in cases:
        assert case.capability not in build_state(case), case.case_id
