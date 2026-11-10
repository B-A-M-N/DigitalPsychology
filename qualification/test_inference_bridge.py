"""Tests for the inference bridge: corpus record -> frame -> prediction -> metrics.

The critical assertions are negative. A metric pipeline that cannot report
INCONCLUSIVE, cannot detect a collapsed predictor, or cannot flag a false
intervention would manufacture a pass from bad data.
"""
from __future__ import annotations

import pytest

from qualification.corpus_schema import (
    ControllerDecision,
    CorpusRecord,
    ObservedOutcome,
    SemanticTarget,
)
from qualification.inference_bridge import (
    FrameConstructionError,
    TaskContext,
    build_frame,
    compute_metrics,
    prediction_from_judgment,
)
from qualification.qualification_spec import QualificationSpec

CKPT_REVISION = "1a793eb568e6718f15941d08f85432581df534e3"
CKPT_HASH = "c" * 64


def _record(frame_id, trajectory, *, appropriate="observe", controller="observe",
            abstention=False, eligible=("observe", "mutate")):
    return CorpusRecord(
        frame_id=frame_id,
        semantic_target=SemanticTarget(
            frame_id=frame_id, appropriate_route=appropriate,
            eligible_routes=eligible,
            label_provenance="independent_expert_review",
            labelled_by="human-reviewer-1", abstention_expected=abstention),
        controller_decision=ControllerDecision(
            selected_route=None if abstention else controller,
            available_candidates=eligible,
            policy_snapshot_hash="1" * 64,
            decision_reason="deterministic tag match", abstained=abstention),
        observed_outcome=ObservedOutcome(
            success_criteria_met=True, evidence_refs=(f"evidence-{frame_id}",),
            observed_by="host_validator"),
        checkpoint_revision=CKPT_REVISION, checkpoint_content_hash=CKPT_HASH,
        statework_contract_version="getter-v1", calibration_id="none-yet",
        policy_snapshot_hash="1" * 64, trajectory_id=trajectory,
        subject_ref="repo:example")


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


class _J:
    def __init__(self, selected, distribution):
        self.answers = (_A(selected, distribution),)


def _prediction(selected, distribution, *, appropriate="observe",
                controller="observe", frame_id="f1", trajectory="t1",
                abstention=False):
    record = _record(frame_id, trajectory, appropriate=appropriate,
                     controller=controller, abstention=abstention)
    return prediction_from_judgment(
        record, _J(selected, distribution),
        checkpoint_revision=CKPT_REVISION, checkpoint_content_hash=CKPT_HASH)


# ------------------------------------------------------------- frame building
def test_frame_construction_uses_dp_real_contract():
    """The bridge must produce a genuine SystemOneFrame, not a stand-in."""
    from lib.system_one_contracts import SystemOneFrame
    record = _record("f1", "t1")
    frame = build_frame(
        record, TaskContext(objective="Establish repository truth.",
                            work_mode="delivery",
                            deliverable_kind="code_behavior"),
        sequence=1, backend_id="digitalpsychology-laya-native",
        model_id="laya-typed-decisions", model_revision=CKPT_REVISION,
        model_content_hash=CKPT_HASH, provider_protocol_hash="b" * 64)
    assert isinstance(frame, SystemOneFrame)
    assert frame.state.legal_routes == ("observe", "mutate")
    assert frame.questions[0].question_id == "route_preference"


@pytest.mark.parametrize("kwargs", [
    {"work_mode": "not-a-mode"},
    {"deliverable_kind": "not-a-kind"},
    {"task_complexity": "not-a-complexity"},
])
def test_invalid_task_context_fails_closed(kwargs):
    """SemanticState validates its vocabularies; the bridge must not hide that."""
    base = {"objective": "x", "work_mode": "delivery",
            "deliverable_kind": "code_behavior"}
    base.update(kwargs)
    with pytest.raises(FrameConstructionError):
        TaskContext(**base)


# ------------------------------------------------------------- predictions
def test_prediction_carries_independent_label_not_the_prediction():
    """The label must come from the record, never from what the model said."""
    prediction = _prediction("mutate", {"observe": 0.4, "mutate": 0.6},
                             appropriate="observe")
    assert prediction.selected == "mutate"
    assert prediction.appropriate_route == "observe"
    assert prediction.correct is False


def test_abstention_expected_yields_no_score():
    """An unusable label must not count as either success or failure."""
    prediction = _prediction("observe", {"observe": 0.9, "mutate": 0.1},
                             appropriate=None, abstention=True)
    assert prediction.correct is None
    assert prediction.controller_correct is None


def test_margin_is_top1_minus_top2():
    prediction = _prediction("observe", {"observe": 0.8, "mutate": 0.2})
    assert prediction.margin == pytest.approx(0.6)


def test_missing_route_answer_fails_closed():
    record = _record("f1", "t1")

    class NoRoute:
        answers = ()

    with pytest.raises(FrameConstructionError, match="no route_preference"):
        prediction_from_judgment(
            record, NoRoute(), checkpoint_revision=CKPT_REVISION,
            checkpoint_content_hash=CKPT_HASH)


# ------------------------------------------------------------- metrics
def test_tiny_corpus_is_inconclusive_never_a_pass():
    """Four cases cannot satisfy the sample gates; that must be reported."""
    metrics = compute_metrics([
        _prediction("observe", {"observe": 0.9, "mutate": 0.1}),
    ], spec=QualificationSpec(), trajectories=1)
    assert metrics.verdict == "INCONCLUSIVE_UNDERPOWERED"
    assert metrics.balanced_accuracy is not None, "the metric is still measured"


def test_no_scorable_labels_is_reported_not_scored_as_zero():
    metrics = compute_metrics([
        _prediction("observe", {"observe": 0.9, "mutate": 0.1},
                    appropriate=None, abstention=True),
    ], spec=QualificationSpec(), trajectories=1)
    assert metrics.verdict == "INCONCLUSIVE_NO_SCORABLE_LABELS"
    assert metrics.n_scored == 0


def test_collapsed_predictor_scores_zero_not_one():
    """A model that always answers one label must not look accurate."""
    metrics = compute_metrics([
        _prediction("observe", {"observe": 0.9, "mutate": 0.1},
                    appropriate="observe", frame_id="a", trajectory="a"),
        _prediction("observe", {"observe": 0.9, "mutate": 0.1},
                    appropriate="mutate", frame_id="b", trajectory="b"),
    ], spec=QualificationSpec(), trajectories=2)
    # One class is perfect, the other is zero -> balanced accuracy 0.5, not 1.0.
    assert metrics.balanced_accuracy == pytest.approx(0.5)


def test_false_intervention_is_counted_as_harm():
    """Overriding a CORRECT deterministic selection must be flagged.

    The controller chose 'observe' and was right; the model chose 'mutate' and
    was wrong. That is harm, not complementarity.
    """
    prediction = _prediction("mutate", {"observe": 0.4, "mutate": 0.6},
                             appropriate="observe", controller="observe")
    metrics = compute_metrics([prediction], spec=QualificationSpec(),
                              trajectories=1)
    assert metrics.false_interventions == 1
    assert metrics.false_intervention_rate == pytest.approx(1.0)


def test_complementarity_is_measured_on_controller_failures():
    """Near-chance aggregate does not rule out complementary signal."""
    predictions = [
        # Deterministic wrong, Laya right -> complementary.
        _prediction("observe", {"observe": 0.9, "mutate": 0.1},
                    appropriate="observe", controller="mutate",
                    frame_id="a", trajectory="a"),
        # Deterministic right, Laya wrong -> harmful.
        _prediction("mutate", {"observe": 0.4, "mutate": 0.6},
                    appropriate="observe", controller="observe",
                    frame_id="b", trajectory="b"),
    ]
    metrics = compute_metrics(predictions, spec=QualificationSpec(),
                              trajectories=2)
    assert metrics.complementarity["controller_wrong"] == 1
    assert metrics.complementarity["laya_fixed_those"] == 1
    assert metrics.complementarity["laya_broke_correct_ones"] == 1


def test_discrimination_and_calibration_are_separate():
    """A confident wrong answer must score well on neither, independently."""
    metrics = compute_metrics([
        _prediction("mutate", {"observe": 0.05, "mutate": 0.95},
                    appropriate="observe", frame_id="a", trajectory="a"),
    ], spec=QualificationSpec(), trajectories=1)
    assert metrics.balanced_accuracy == 0.0
    # High confidence in a wrong answer is exactly poor calibration.
    assert metrics.brier > 0.5
    assert metrics.ece > 0.5


def test_qualified_verdict_requires_all_gates():
    """Even a large, accurate, well-calibrated set must clear every gate."""
    predictions = []
    for index in range(200):
        predictions.append(_prediction(
            "observe", {"observe": 0.95, "mutate": 0.05},
            appropriate="observe", controller="observe",
            frame_id=f"f{index}", trajectory=f"t{index}"))
    metrics = compute_metrics(predictions, spec=QualificationSpec(),
                              trajectories=200)
    assert metrics.balanced_accuracy == 1.0
    assert metrics.brier < 0.05
    # Sample gates are satisfied, so this reaches a real verdict.
    # Sample gates pass, so a real verdict is reached -- and it is a REFUSAL,
    # because only one class was ever tested.
    assert metrics.verdict == "NOT_QUALIFIED_SINGLE_CLASS_CORPUS", (
        "balanced accuracy 1.0 and margin 0.90 on a SINGLE-class corpus must "
        "not read as qualified discrimination")
