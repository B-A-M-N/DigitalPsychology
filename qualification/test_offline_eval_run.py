"""Tests for OfflineRouteEvaluator.run() — the documented inference entry point.

The module docstring promises `run()` exists; these tests make that true and
pin its safety properties: it must refuse an invalid corpus, refuse a corpus
over the inference ceiling, and refuse records lacking a TaskContext, rather
than producing metrics from incomplete data.
"""
from __future__ import annotations

import pytest

from qualification.corpus_schema import (
    ControllerDecision,
    CorpusRecord,
    ObservedOutcome,
    SemanticTarget,
)
from qualification.inference_bridge import TaskContext
from qualification.offline_eval import OfflineRouteEvaluator

CKPT = "/home/bamn/.cache/laya/convaiinnovations-laya-typed-decisions-1a793eb"


class _P:
    def __init__(self, label, probability):
        self.label = label
        self.probability = probability


class _A:
    def __init__(self, selected, distribution):
        self.question_id = "route_preference"
        self.selected = selected
        self.raw_probabilities = tuple(_P(k, v) for k, v in distribution.items())
        self.calibrated_probabilities = None


class _Judgment:
    def __init__(self, selected, distribution):
        self.answers = (_A(selected, distribution),)


class _Capability:
    backend_id = "digitalpsychology-laya-native"
    model_id = "laya-typed-decisions"
    model_revision = "1a793eb568e6718f15941d08f85432581df534e3"
    model_content_hash = "cda55a3e793f4c4268a25066ceb1568aa302fd210d96656e3032756fd722b642"
    provider_protocol_hash = "b" * 64


class _FakeProvider:
    """A deterministic stand-in that records what it was asked to judge."""

    def __init__(self, selected="observe", distribution=None):
        self.selected = selected
        self.distribution = distribution or {"observe": 0.8, "mutate": 0.2}
        self.seen = []

    def capability(self):
        return _Capability()

    def judge(self, frame):
        self.seen.append(frame)
        return _Judgment(self.selected, self.distribution)


def _record(frame_id, trajectory, *, appropriate="observe", controller="observe",
            evidence=None):
    return CorpusRecord(
        frame_id=frame_id,
        semantic_target=SemanticTarget(
            frame_id=frame_id, appropriate_route=appropriate,
            eligible_routes=("observe", "mutate"),
            label_provenance="independent_expert_review",
            labelled_by="human-reviewer-1"),
        controller_decision=ControllerDecision(
            selected_route=controller, available_candidates=("observe", "mutate"),
            policy_snapshot_hash="1" * 64,
            decision_reason="deterministic tag match"),
        observed_outcome=ObservedOutcome(
            success_criteria_met=True,
            evidence_refs=evidence or (f"evidence-{frame_id}",),
            observed_by="host_validator"),
        checkpoint_revision="1a793eb568e6718f15941d08f85432581df534e3",
        checkpoint_content_hash="c" * 64,
        statework_contract_version="getter-v1", calibration_id="none-yet",
        policy_snapshot_hash="1" * 64, trajectory_id=trajectory,
        subject_ref="repo:example")


def _context():
    return TaskContext(objective="Establish repository truth.",
                       work_mode="delivery",
                       deliverable_kind="code_behavior")


def _contexts(records):
    return {r.frame_id: _context() for r in records}


def test_run_produces_measured_metrics():
    """run() must return real metrics, not a readiness placeholder."""
    records = [_record("f1", "t1"), _record("f2", "t2", appropriate="mutate")]
    provider = _FakeProvider(selected="observe",
                             distribution={"observe": 0.8, "mutate": 0.2})

    result, metrics = OfflineRouteEvaluator().run(
        records, provider=provider, task_contexts=_contexts(records),
        model_path=CKPT, statework_contract_version="getter-v1")

    assert result.inference_count == 2
    assert len(provider.seen) == 2
    # Discrimination and calibration are MEASURED, not None.
    assert result.discrimination["balanced_accuracy"] is not None
    assert result.calibration["brier"] is not None
    assert result.calibration["ece"] is not None
    assert result.margin["mean_margin"] is not None
    assert metrics.balanced_accuracy is not None


def test_run_refuses_an_invalid_corpus():
    """Metrics must never be produced from suspect data."""
    # Two records with identical routes, label and evidence -> near-duplicates.
    records = [_record("f1", "t1", evidence=("same",)),
               _record("f2", "t2", evidence=("same",))]
    provider = _FakeProvider()

    with pytest.raises(ValueError, match="refusing to evaluate"):
        OfflineRouteEvaluator().run(
            records, provider=provider, task_contexts=_contexts(records),
            model_path=CKPT, statework_contract_version="getter-v1")
    assert provider.seen == [], "no inference may run on an invalid corpus"


def test_run_refuses_records_without_a_task_context():
    records = [_record("f1", "t1")]
    provider = _FakeProvider()

    with pytest.raises(ValueError, match="lack a TaskContext"):
        OfflineRouteEvaluator().run(
            records, provider=provider, task_contexts={},
            model_path=CKPT, statework_contract_version="getter-v1")


def test_run_enforces_the_inference_ceiling():
    """A corpus larger than the ceiling must be refused, not silently truncated."""
    records = [_record(f"f{i}", f"t{i}") for i in range(5)]
    provider = _FakeProvider()

    with pytest.raises(ValueError, match="inference ceiling"):
        OfflineRouteEvaluator(inference_ceiling=2).run(
            records, provider=provider, task_contexts=_contexts(records),
            model_path=CKPT, statework_contract_version="getter-v1")
    assert provider.seen == []


def test_run_builds_real_frames_for_the_provider():
    """The provider must receive genuine SystemOneFrames, not stand-ins."""
    from lib.system_one_contracts import SystemOneFrame
    records = [_record("f1", "t1")]
    provider = _FakeProvider()

    OfflineRouteEvaluator().run(
        records, provider=provider, task_contexts=_contexts(records),
        model_path=CKPT, statework_contract_version="getter-v1")

    assert isinstance(provider.seen[0], SystemOneFrame)
    assert provider.seen[0].state.legal_routes == ("observe", "mutate")
    assert provider.seen[0].questions[0].question_id == "route_preference"


def test_run_verdict_is_inconclusive_for_a_tiny_corpus():
    records = [_record("f1", "t1")]
    provider = _FakeProvider()

    result, _metrics = OfflineRouteEvaluator().run(
        records, provider=provider, task_contexts=_contexts(records),
        model_path=CKPT, statework_contract_version="getter-v1")

    assert result.verdict == "INCONCLUSIVE_UNDERPOWERED"
    assert result.verdict != "QUALIFIED"
