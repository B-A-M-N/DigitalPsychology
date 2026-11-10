"""Tests for the five offline route_preference qualification deliverables.

These verify the MEASUREMENT, not Laya's performance. No inference is run.

The most important assertions are the negative ones: a corpus with leaked labels,
a split that crosses trajectories, a forged counterfactual, or a fixture whose
label contradicts its contract must all FAIL rather than produce a passing
result. A benchmark that cannot reject bad data is worse than no benchmark.
"""
from __future__ import annotations

import pytest

from qualification.corpus_schema import (
    LABEL_PROVENANCE_CONTROLLER,
    ControllerDecision,
    CorpusRecord,
    ObservedOutcome,
    SemanticTarget,
    validate_corpus,
)
from qualification.leakage_checks import run_all_checks
from qualification.offline_eval import (
    OfflineRouteEvaluator,
    checkpoint_digest,
    split_by_trajectory,
)
from qualification.qualification_spec import (
    AGGREGATE_MARGIN_FLOOR,
    PER_DECISION_MARGIN_FLOOR,
    QualificationSpec,
)
from qualification.route_fixtures import (
    BUNDLED_CONTRACTS,
    CASE_CLASSES,
    build_fixture_set,
    coverage_report,
)

CHECKPOINT_PATH = ("/home/bamn/.cache/laya/"
                   "convaiinnovations-laya-typed-decisions-1a793eb")


def _record(frame_id, trajectory, *, appropriate="observe",
            provenance="independent_expert", selected="observe",
            abstention=False, evidence=None,
            counterfactual_route=None, counterfactual_outcome=None,
            eligible=("observe", "plan")):
    # Evidence defaults to a per-record value so a small corpus is not a set of
    # near-duplicates; duplicate detection has its own dedicated test.
    if evidence is None:
        evidence = (f"repository_evidence-{frame_id}",)
    return CorpusRecord(
        frame_id=frame_id,
        semantic_target=SemanticTarget(
            frame_id=frame_id, appropriate_route=appropriate,
            eligible_routes=eligible, label_provenance=provenance,
            labelled_by="expert-1", abstention_expected=abstention),
        controller_decision=ControllerDecision(
            selected_route=selected, available_candidates=eligible,
            policy_snapshot_hash="1" * 64,
            decision_reason="deterministic tag match", abstained=abstention),
        observed_outcome=ObservedOutcome(
            success_criteria_met=True, evidence_refs=evidence,
            observed_by="host_validator",
            counterfactual_route=counterfactual_route,
            counterfactual_outcome=counterfactual_outcome),
        checkpoint_revision="1a793eb", checkpoint_content_hash="a" * 64,
        statework_contract_version="getter-v1", calibration_id="cal-1",
        policy_snapshot_hash="1" * 64, trajectory_id=trajectory,
        subject_ref="repo:example")


# ------------------------------------------------------- Deliverable A
def test_three_observations_are_kept_separate():
    """The schema must not collapse semantic target into controller decision."""
    record = _record("f1", "t1")
    assert record.semantic_target.appropriate_route is not None
    assert record.controller_decision.selected_route is not None
    assert record.observed_outcome.success_criteria_met is not None
    # Each has its own provenance field.
    assert record.semantic_target.label_provenance != (
        record.controller_decision.decision_reason)


def test_label_derived_from_controller_is_rejected():
    """The circularity the schema exists to prevent."""
    with pytest.raises(ValueError, match="must not be derived"):
        _record("f1", "t1", provenance=LABEL_PROVENANCE_CONTROLLER)


def test_unknown_counterfactual_stays_unknown():
    """An observed failure is not proof an alternative would have succeeded."""
    record = _record("f1", "t1")
    assert record.counterfactual_available is False


def test_forged_counterfactual_is_rejected():
    """Claiming availability without an outcome is rejected."""
    with pytest.raises(ValueError, match="requires its outcome"):
        _record("f1", "t1", counterfactual_route="plan")


def test_validate_corpus_flags_duplicates_and_unusable_targets():
    records = [_record("f1", "t1"), _record("f1", "t2")]
    problems = validate_corpus(records)
    assert any("duplicate frame_id" in p for p in problems)


# ------------------------------------------------------- Deliverable B
def test_fixture_set_covers_all_ten_case_classes():
    coverage = coverage_report(build_fixture_set())
    assert coverage["complete"] is True, (
        f"missing classes: {coverage['missing_classes']}")
    assert coverage["total"] == len(CASE_CLASSES)


def test_fixture_labels_are_contract_derived():
    """A label naming a route whose evidence is unavailable must be rejected."""
    from qualification.route_fixtures import RouteFixture
    with pytest.raises(ValueError, match="requires evidence"):
        RouteFixture(
            case_id="bad", case_class="correct_deterministic", bundle="getter",
            eligible_routes=("observe", "mutate"), appropriate_route="mutate",
            abstention_expected=False, available_evidence=(),
            rationale="mutate needs evidence the state cannot supply")


def test_fixture_rejects_routes_outside_the_bundle_contract():
    from qualification.route_fixtures import RouteFixture
    with pytest.raises(ValueError, match="outside the getter"):
        RouteFixture(
            case_id="bad", case_class="correct_deterministic", bundle="getter",
            eligible_routes=("observe", "decommission"),
            appropriate_route="observe", abstention_expected=False,
            available_evidence=("repository_evidence",), rationale="x")


def test_harmful_intervention_traps_are_present():
    """Classes 1 and 9 must exist, or the benchmark rewards overconfidence."""
    fixtures = build_fixture_set()
    classes = {f.case_class for f in fixtures}
    assert "correct_deterministic" in classes
    assert "semantic_would_worsen" in classes
    assert any(f.is_trap for f in fixtures), (
        "no trap fixtures: a model overriding correct selections would score well")


def test_bundled_contract_evidence_counts():
    """The contract evidence sets match the bundled transitions.yaml files."""
    assert len(BUNDLED_CONTRACTS["getter"]["evidence"]) == 6
    assert len(BUNDLED_CONTRACTS["gitter"]["evidence"]) == 3
    assert len(BUNDLED_CONTRACTS["infrae"]["evidence"]) >= 7


# ------------------------------------------------------- Deliverable C
def test_aggregate_and_per_decision_margins_are_distinct():
    """The 0.15 bar is aggregate; the per-decision floor only rejects coin flips."""
    spec = QualificationSpec()
    assert AGGREGATE_MARGIN_FLOOR == 0.15
    assert PER_DECISION_MARGIN_FLOOR == 0.05

    # The Stage-B run's own per-case margins must not all be rejected.
    stage_b = (0.1398, 0.0298, 0.2090, 0.1228, 0.0886, 0.1844, 0.1326, 0.0584)
    result = spec.evaluate_margin(per_case_margins=stage_b)
    assert result["aggregate_passed"] is False, "mean 0.121 < 0.15 as recorded"
    # But the per-decision floor must not reject the whole set.
    assert result["per_decision_abstentions"] < len(stage_b)


def test_underpowered_corpus_reports_inconclusive():
    """Too few samples must not be manufacturable into a pass."""
    spec = QualificationSpec()
    result = spec.evaluate_sample_size(outcomes=12, trajectories=3,
                                       cohort_trajectories=3)
    assert result["verdict"] == "INCONCLUSIVE_UNDERPOWERED"


def test_calibration_does_not_imply_discrimination():
    """A calibrated chance-level model is still useless; the spec says so."""
    spec = QualificationSpec()
    calibration = spec.evaluate_calibration(brier=0.10, ece=0.05)
    assert calibration["brier_passed"] is True
    assert "does not measure" in calibration["note"]


def test_incremental_utility_nets_out_false_intervention():
    """Accuracy gains from overriding correct selections must count as harm."""
    spec = QualificationSpec()
    result = spec.evaluate_incremental_utility(
        baseline_accuracy=0.90, model_accuracy=0.95,
        false_intervention_rate=0.20)
    assert result["delta"] > 0
    assert result["net_utility"] < 0, (
        "a model that improves accuracy while intervening wrongly is net harmful")


# ------------------------------------------------------- Deliverable D
def test_trajectory_split_keeps_frames_together():
    records = [_record(f"f{i}", f"t{i // 3}") for i in range(12)]
    train, holdout = split_by_trajectory(records)
    train_ids = {r.trajectory_id for r in train}
    holdout_ids = {r.trajectory_id for r in holdout}
    assert not (train_ids & holdout_ids), "a trajectory spanned both splits"


def test_split_is_reproducible():
    records = [_record(f"f{i}", f"t{i // 3}") for i in range(12)]
    first = split_by_trajectory(records)
    second = split_by_trajectory(records)
    assert [r.frame_id for r in first[0]] == [r.frame_id for r in second[0]]


def test_prepare_performs_no_inference_and_binds_artifacts():
    """Readiness must not be mistaken for a performance result."""
    records = [_record(f"f{i}", f"t{i}") for i in range(5)]
    result = OfflineRouteEvaluator().prepare(
        records, model_path=CHECKPOINT_PATH,
        statework_contract_version="getter-v1")

    assert result.inference_count == 0
    assert result.discrimination is None
    assert result.calibration is None
    assert len(result.checkpoint_content_hash) == 64
    assert result.verdict == "INCONCLUSIVE_UNDERPOWERED"
    assert "no Laya performance" in result.to_dict()["note"]


def test_checkpoint_digest_binds_the_real_artifacts():
    digest = checkpoint_digest(CHECKPOINT_PATH)
    assert len(digest) == 64
    assert all(c in "0123456789abcdef" for c in digest)


# ------------------------------------------------------- Deliverable E
def test_leakage_checks_accept_a_clean_corpus():
    """A single independent record with no duplicates is valid."""
    report = run_all_checks([_record("f1", "t1")])
    assert report.valid is True, report.problems


def test_trajectory_overlap_is_detected():
    train = [_record("f1", "shared")]
    holdout = [_record("f2", "shared")]
    report = run_all_checks(train + holdout, train=train, holdout=holdout)
    assert report.valid is False
    assert any("BOTH train and holdout" in p for p in report.problems)


def test_outcome_leakage_into_model_inputs_is_detected():
    records = [_record("f1", "t1")]
    report = run_all_checks(records,
                            model_input_fields=["state", "success_criteria_met"])
    assert report.valid is False
    assert any("outcome field" in p for p in report.problems)


def test_independence_counts_trajectories_not_frames():
    records = [_record(f"f{i}", "same-trajectory") for i in range(4)]
    report = run_all_checks(records)
    assert report.valid is False
    assert any("independent sample count" in p for p in report.problems)


def test_duplicate_fixtures_are_detected():
    # Genuinely identical apart from IDs: same routes, label and evidence.
    records = [_record("f1", "t1", evidence=("repository_evidence",)),
               _record("f2", "t2", evidence=("repository_evidence",))]
    report = run_all_checks(records)
    assert report.valid is False
    assert any("near-duplicate" in p for p in report.problems)


def test_inverted_counterfactual_is_detected():
    records = [_record("f1", "t1", appropriate="observe",
                       counterfactual_route="observe",
                       counterfactual_outcome=False)]
    report = run_all_checks(records)
    assert report.valid is False
    assert any("inverted" in p for p in report.problems)
