"""Emitter formats judgments into proposal payloads. No authority involved."""

from __future__ import annotations

import os
from pathlib import Path
import sys

import pytest

from lib.control.proposal_emitter import (
    FAMILY_TO_KIND,
    FAMILY_TO_OWNER,
    emit_proposals,
)


class _Prob:
    def __init__(self, label, probability):
        self.label = label
        self.probability = probability


class _Answer:
    def __init__(self, selected, raw, calibrated=None):
        self.selected = selected
        self.raw_probabilities = tuple(_Prob(label, value) for label, value in raw)
        self.calibrated_probabilities = (
            None if calibrated is None
            else tuple(_Prob(label, value) for label, value in calibrated))


class _Judgment:
    def __init__(self, answers, model_id="typesafe/jev-1.14",
                 model_revision="jev-1.14", request_id="req-1",
                 state_digest="s" * 64):
        self._answers = answers
        self.model_id = model_id
        self.model_revision = model_revision
        self.request_id = request_id
        self.state_digest = state_digest

    def answer(self, question_id):
        return self._answers.get(question_id)


def _judgment_all_families(**overrides):
    dist = [("godkiller", 0.7), ("testworks", 0.2), ("terraformer", 0.1)]
    answers = {
        "route_preference": _Answer("getter", [("getter", 0.6), ("gitter", 0.4)]),
        "test_strategy_preference": _Answer("testworks", dist),
        "decomposition_preference": _Answer("godkiller", dist),
        "integration_preference": _Answer("terraformer", dist),
        "action_relation": _Answer("advances_primary_goal",
                                  [("advances_primary_goal", 0.9), ("unrelated", 0.1)]),
    }
    params = dict(model_id="typesafe/jev-1.14", model_revision="jev-1.14",
                  request_id="req-7", state_digest="d" * 64)
    params.update(overrides)
    return _Judgment(answers, **params)


def test_emits_one_proposal_per_mapped_family_in_fixed_order():
    proposals = emit_proposals(_judgment_all_families(), subject_ref="repo:x")
    assert [item["kind"] for item in proposals] == [
        "test_selection_draft", "partition_draft", "integration_plan_draft"]
    assert [item["statework_id"] for item in proposals] == [
        "testworks", "godkiller", "terraformer"]
    first = proposals[0]
    assert first["author_model"] == "typesafe/jev-1.14"
    assert first["model_revision"] == "jev-1.14"
    assert first["subject_ref"] == "repo:x"
    assert first["body"]["advisory_family"] == "test_strategy_preference"
    assert first["body"]["selected"] == "testworks"
    assert first["body"]["confidence"] == pytest.approx(0.2)
    assert first["body"]["provider_decision_id"] == "req-7"
    assert first["body"]["judged_state_digest"] == "d" * 64
    assert {entry["label"] for entry in first["body"]["support"]} == {
        "godkiller", "testworks", "terraformer"}


def test_unmapped_families_yield_nothing():
    judgment = _Judgment({
        "route_preference": _Answer("getter", [("getter", 1.0)]),
        "action_relation": _Answer("advances_primary_goal",
                                  [("advances_primary_goal", 1.0)]),
    })
    assert emit_proposals(judgment, subject_ref="repo:x") == ()


def test_calibrated_distribution_wins_and_flag_tracks_source():
    calibrated = [("testworks", 0.4), ("godkiller", 0.5), ("terraformer", 0.1)]
    raw = [("testworks", 0.7), ("godkiller", 0.2), ("terraformer", 0.1)]
    judgment = _Judgment({"test_strategy_preference": _Answer(
        "godkiller", raw, calibrated)})
    (proposal,) = emit_proposals(judgment, subject_ref="repo:x")
    assert proposal["body"]["selected"] == "godkiller"
    assert proposal["body"]["confidence"] == pytest.approx(0.5)
    assert proposal["body"]["calibrated"] is True
    raw_only = _Judgment({"test_strategy_preference": _Answer("testworks", raw)})
    (proposal,) = emit_proposals(raw_only, subject_ref="repo:x")
    assert proposal["body"]["calibrated"] is False


def test_statework_override_basis_and_top_n():
    judgment = _judgment_all_families()
    proposals = emit_proposals(judgment, subject_ref="repo:x",
                               statework_id="custom", basis_revision=9, top_n=2)
    assert {item["statework_id"] for item in proposals} == {"custom"}
    assert all(item["basis_revision"] == 9 for item in proposals)
    assert all(len(item["body"]["support"]) == 2 for item in proposals)


def test_malformed_input_fails_closed():
    judgment = _judgment_all_families()
    with pytest.raises(ValueError):
        emit_proposals(object(), subject_ref="repo:x")
    with pytest.raises(ValueError):
        emit_proposals(judgment, subject_ref="")
    with pytest.raises(ValueError):
        emit_proposals(judgment, subject_ref="repo:x", top_n=0)
    with pytest.raises(ValueError):
        emit_proposals(judgment, subject_ref="repo:x", basis_revision=-1)
    bad_model = _judgment_all_families(model_id="")
    with pytest.raises(ValueError):
        emit_proposals(bad_model, subject_ref="repo:x")
    outside = _Judgment({"test_strategy_preference": _Answer(
        "nobody", [("testworks", 0.7), ("godkiller", 0.3)])})
    with pytest.raises(ValueError, match="outside its distribution"):
        emit_proposals(outside, subject_ref="repo:x")


def test_emitted_payloads_validate_against_csw_inbox_when_available():
    root = os.environ.get("COGNITIVE_STATEWORK_ROOT", "")
    csw_scripts = Path(root, "scripts", "proposal_inbox.py")
    if not root or not csw_scripts.exists():
        pytest.skip("CSW checkout not available")
    sys.path.insert(0, str(Path(root, "scripts")))
    try:
        from proposal_inbox import validate_proposal
    finally:
        sys.path.remove(str(Path(root, "scripts")))
    for proposal in emit_proposals(_judgment_all_families(), subject_ref="repo:x"):
        validate_proposal(proposal)
