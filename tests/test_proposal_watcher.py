"""Watcher feeds judgments in and surfaces inbox listings. No authority."""

from __future__ import annotations

import pytest

from lib.control.proposal_watcher import drain_judgment, review_snapshot


class _Prob:
    def __init__(self, label, probability):
        self.label = label
        self.probability = probability


class _Answer:
    def __init__(self, selected, raw):
        self.selected = selected
        self.raw_probabilities = tuple(_Prob(label, value) for label, value in raw)
        self.calibrated_probabilities = None


class _Judgment:
    def __init__(self, answers):
        self._answers = answers
        self.model_id = "typesafe/jev-1.14"
        self.model_revision = "jev-1.14"
        self.request_id = "req-9"

    def answer(self, question_id):
        return self._answers.get(question_id)


def _judgment():
    return _Judgment({
        "test_strategy_preference": _Answer(
            "testworks", [("testworks", 0.8), ("godkiller", 0.2)]),
        "decomposition_preference": _Answer(
            "godkiller", [("godkiller", 0.6), ("testworks", 0.4)]),
    })


def test_drain_submits_each_proposal_and_returns_receipts():
    seen = []
    receipts = drain_judgment(_judgment(), submit=seen.append, subject_ref="repo:x")
    assert [item["ok"] for item in receipts] == [True, True]
    assert [item["kind"] for item in receipts] == [
        "test_selection_draft", "partition_draft"]
    assert len(seen) == 2
    assert all(item["subject_ref"] == "repo:x" for item in seen)


def test_drain_with_no_mapped_answers_submits_nothing():
    judgment = _Judgment({"route_preference": _Answer(
        "getter", [("getter", 1.0)])})
    assert drain_judgment(judgment, submit=lambda item: "never", subject_ref="s") == []


def test_submit_failure_is_reported_not_swallowed():
    def failing(payload):
        raise RuntimeError("inbox unreachable")

    receipts = drain_judgment(_judgment(), submit=failing, subject_ref="repo:x")
    assert len(receipts) == 2
    assert all(item["ok"] is False for item in receipts)
    assert all("RuntimeError" in item["error"] for item in receipts)


def test_non_callable_submit_rejected():
    with pytest.raises(ValueError):
        drain_judgment(_judgment(), submit="inbox", subject_ref="repo:x")


def test_review_snapshot_is_bounded_and_shaped():
    listing = [{
        "proposal_id": "prop-abc",
        "kind": "test_selection_draft",
        "statework_id": "testworks",
        "author_model": "typesafe/jev-1.14",
        "model_revision": "jev-1.14",
        "submitted_at": "2026-11-10T00:00:00Z",
        "body": {
            "selected": "testworks",
            "confidence": 0.8,
            "support": [{"label": "testworks", "probability": 0.8},
                        {"label": "godkiller", "probability": 0.2}],
        },
    }]
    (row,) = review_snapshot(listing)
    assert row["proposal_id"] == "prop-abc"
    assert row["kind"] == "test_selection_draft"
    assert row["selected"] == "testworks"
    assert row["confidence"] == pytest.approx(0.8)
    assert row["support_preview"] == [
        {"label": "testworks", "probability": 0.8},
        {"label": "godkiller", "probability": 0.2},
    ]
    with pytest.raises(ValueError):
        review_snapshot("not-a-list")
