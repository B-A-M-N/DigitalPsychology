"""Tests for the laya contract pre-check.

The point of this module is a CHEAP FIRST PASS before `dry_run`, so its tests
must prove it rejects the things the real one rejects -- a validator that says
"clean" about everything is worse than none. Most cases here are deliberately
malformed rows; one is the real export.
"""
from __future__ import annotations

import copy

import pytest

from qualification.laya_contract_precheck import (
    SKIP_REASONS,
    validate_jsonl,
    validate_row,
)


def _good_row() -> dict:
    """A row copied from the real export, so the baseline is genuinely valid."""
    from qualification.contrastive_fixtures import generate_cases
    from qualification.training_export import case_to_row
    decidable = [c for c in generate_cases() if c.gold is not None]
    assert decidable
    return case_to_row(decidable[0])


def test_exported_rows_are_all_clean(tmp_path):
    """Every exported split must pass the same rules the real dry_run applies."""
    from qualification.training_export import (
        generate_cases,
        split_disjoint,
        write_jsonl,
    )
    cases = generate_cases()
    train, calib, holdout = split_disjoint(cases)
    for name, subset in (("train", train), ("calibration", calib),
                         ("holdout", holdout)):
        path = tmp_path / f"{name}.jsonl"
        write_jsonl(path, subset)
        verdicts, skipped = validate_jsonl(path)
        assert not skipped, f"{name}: {skipped}"
        assert all(v.ok for v in verdicts)


def test_reason_vocabulary_matches_laya():
    """These are laya's own skip reasons, so numbers compare with dry_run."""
    assert set(SKIP_REASONS) == {
        "empty_text", "invalid_question", "invalid_target", "target_mismatch",
        "options_collapsed", "options_beyond_max_len"}


@pytest.mark.parametrize("mutator,expected", [
    (lambda r: r.update(state=""), "empty_text"),
    (lambda r: r.update(state=None), "empty_text"),
    (lambda r: r.pop("state"), "empty_text"),
    (lambda r: r.update(questions={"bad": None}), "invalid_question"),
    (lambda r: r.pop("questions"), "invalid_question"),
    (lambda r: r.update(gold=[]), "invalid_target"),
])
def test_structural_rejections(mutator, expected):
    row = _good_row()
    mutator(row)
    verdict = validate_row(0, row)
    assert not verdict.ok
    assert verdict.reason == expected


def _rewrite_internal(row: dict) -> None:
    """Rewrite the question in the INTERNAL shape to prove it is rejected."""
    question = row["questions"]["route_preference"]
    row["questions"]["route_preference"] = {
        "t": "choice",
        "crit": question["criteria"],
        "ins": question["instructions"],
    }


@pytest.mark.parametrize("mutator,expected", [
    (_rewrite_internal, "invalid_question"),
    (lambda r: r["questions"]["route_preference"].update(
        instructions=[]), "invalid_question"),
    (lambda r: r["questions"]["route_preference"].update(
        instructions="  "), "invalid_question"),
    (lambda r: r["questions"]["route_preference"].update(
        instructions=None), "invalid_question"),
    (lambda r: r["questions"]["route_preference"].pop("instructions"),
     "invalid_question"),
    (lambda r: r["questions"]["route_preference"].update(type="magic"),
     "invalid_question"),
    (lambda r: r["questions"]["route_preference"].update(criteria={}),
     "invalid_question"),
    (lambda r: r["questions"]["route_preference"]["criteria"].update(
        getter=""), "invalid_question"),
])
def test_question_shape_rejections(mutator, expected):
    """to_internal only accepts the shape inference sees."""
    row = _good_row()
    mutator(row)
    verdict = validate_row(0, row)
    assert not verdict.ok
    assert verdict.reason == expected


@pytest.mark.parametrize("mutator,expected", [
    (lambda r: r.update(gold={"route_preference": {"probabilities": {"getter": -1.0}}}),
     "invalid_target"),
    (lambda r: r.update(gold={"route_preference": {"probabilities": {}}}),
     "invalid_target"),
    (lambda r: r.update(gold={"route_preference": {"nope": 1}}),
     "invalid_target"),
    (lambda r: r.update(gold={"route_preference": {"probabilities": {"not_a_route": 1.0}}}),
     "target_mismatch"),
])
def test_target_rejections(mutator, expected):
    row = _good_row()
    mutator(row)
    verdict = validate_row(0, row)
    assert not verdict.ok
    assert verdict.reason == expected


def test_internal_shape_is_rejected():
    """The defect this module exists to catch.

    `_to_internal` PRODUCES {"t", "crit", "ins"}. A row written in that shape has
    none of the keys `_check_question` reads, so upstream would reject it as
    `invalid_question`. The earlier export did exactly this and its precheck
    agreed with it, because the precheck validated the internal shape too.
    """
    row = _good_row()
    question = row["questions"]["route_preference"]
    row["questions"]["route_preference"] = {
        "t": "choice",
        "crit": question["criteria"],
        "ins": question["instructions"],
    }
    verdict = validate_row(0, row)
    assert not verdict.ok
    assert verdict.reason == "invalid_question"


def test_public_shape_uses_upstream_keys():
    """The export must hit the keys _check_question actually reads."""
    row = _good_row()
    question = row["questions"]["route_preference"]
    assert set(question) == {"type", "instructions", "criteria"}
    assert question["type"] == "choice"
    assert isinstance(question["criteria"], dict) and question["criteria"]


def test_option_order_is_accepted_at_input():
    """`option_order` is legal public input; only popped internally.

    An earlier version rejected it, which would have been a false rejection --
    upstream `_to_internal` reads it, so it is part of the input contract.
    """
    row = _good_row()
    row["questions"]["route_preference"]["option_order"] = [0, 1]
    verdict = validate_row(0, row)
    assert verdict.ok, verdict.to_dict()


def test_baseline_row_is_clean():
    verdict = validate_row(0, _good_row())
    assert verdict.ok, verdict.to_dict()


def test_summarise_counts_by_laya_reason():
    row = _good_row()
    bad = []
    for index, mutator in enumerate([
        lambda r: r.update(state=""),
        lambda r: r.update(state=None),
        lambda r: r.update(gold={"route_preference": {"probabilities": {"getter": -1.0}}}),
    ]):
        r = copy.deepcopy(row)
        mutator(r)
        bad.append(validate_row(index, r))
    from qualification.laya_contract_precheck import summarise
    summary = summarise(bad)
    assert summary["rows"] == 3 and summary["skipped"] == 3
    assert summary["skip_reasons"] == {"empty_text": 2, "invalid_target": 1}


def test_uniform_ambiguous_target_is_clean():
    """An ambiguous case must stay trainable, not become a skipped row."""
    from qualification.contrastive_fixtures import generate_cases
    from qualification.training_export import build_gold
    ambiguous = [c for c in generate_cases() if c.gold is None]
    assert ambiguous
    gold = build_gold(ambiguous[0])
    row = _good_row()
    row["gold"] = {"route_preference": gold}
    verdict = validate_row(0, row)
    assert verdict.ok, verdict.to_dict()
