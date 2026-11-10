"""Deliverable E — label leakage and attribution checks.

Each check returns actionable diagnostics and fails validation rather than
silently accepting suspect data. Invalid evaluation data must not be able to
produce a passing result.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from .corpus_schema import LABEL_PROVENANCE_CONTROLLER, CorpusRecord


@dataclass(frozen=True)
class LeakageReport:
    problems: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return not self.problems

    def to_dict(self) -> dict[str, Any]:
        return {"valid": self.valid, "problems": list(self.problems)}


def check_label_leakage(records: Sequence[CorpusRecord]) -> list[str]:
    """Semantic labels must not be copied from controller decisions."""
    problems = []
    for index, record in enumerate(records):
        target = record.semantic_target
        if target.label_provenance == LABEL_PROVENANCE_CONTROLLER:
            problems.append(
                f"record {index} ({record.frame_id}): semantic label provenance "
                "is the controller decision; the label is not independent")
        elif (target.appropriate_route is not None
                and target.appropriate_route == record.controller_decision.selected_route
                and target.label_provenance.startswith("controller")):
            problems.append(
                f"record {index} ({record.frame_id}): label matches the "
                "controller selection with controller-derived provenance")
    return problems


def check_trajectory_overlap(train: Sequence[CorpusRecord],
                             holdout: Sequence[CorpusRecord]) -> list[str]:
    """The same trajectory must never span train and holdout.

    Frames from one task are correlated. Splitting them inflates apparent
    accuracy because the model is partly evaluated on near-duplicates.
    """
    train_ids = {r.trajectory_id for r in train}
    holdout_ids = {r.trajectory_id for r in holdout}
    overlap = sorted(train_ids & holdout_ids)
    if overlap:
        return [
            f"trajectory {tid!r} appears in BOTH train and holdout; "
            "frames from one task are correlated and must not be split"
            for tid in overlap]
    return []


def check_duplicates(records: Sequence[CorpusRecord]) -> list[str]:
    """Detect duplicate and near-duplicate fixtures."""
    problems = []
    seen: dict[tuple[Any, ...], str] = {}
    for record in records:
        target = record.semantic_target
        key = (tuple(sorted(target.eligible_routes)), target.appropriate_route,
               tuple(sorted(record.observed_outcome.evidence_refs)))
        if key in seen:
            problems.append(
                f"record {record.frame_id} is a near-duplicate of {seen[key]} "
                f"(same routes, label and evidence)")
        else:
            seen[key] = record.frame_id
    return problems


def check_outcome_leakage(records: Sequence[CorpusRecord],
                          model_input_fields: Iterable[str]) -> list[str]:
    """Outcome fields must not be present in model inputs.

    If the observed outcome is visible to the model at inference time, the
    evaluation measures recall of the answer rather than semantic judgement.
    """
    forbidden = {"success_criteria_met", "counterfactual_outcome",
                 "observed_outcome", "evidence_refs"}
    leaked = sorted(forbidden & {str(f) for f in model_input_fields})
    if leaked:
        return [f"model input exposes outcome field(s): {leaked}"]
    return []


def check_provenance(records: Sequence[CorpusRecord]) -> list[str]:
    """Every record needs complete label provenance."""
    problems = []
    for record in records:
        target = record.semantic_target
        if not target.labelled_by:
            problems.append(
                f"record {record.frame_id}: missing labelled_by; provenance is "
                "unverifiable")
        if not target.label_provenance:
            problems.append(
                f"record {record.frame_id}: missing label_provenance")
    return problems


def check_independence(records: Sequence[CorpusRecord]) -> list[str]:
    """Correlated observations must not be counted as independent samples.

    The sample unit is the trajectory, so multiple frames from one trajectory
    contribute one independent observation.
    """
    counts: dict[str, int] = {}
    for record in records:
        counts[record.trajectory_id] = counts.get(record.trajectory_id, 0) + 1
    total = len(records)
    unique = len(counts)
    problems = []
    if total != unique:
        problems.append(
            f"{total} frames span only {unique} trajectories; the independent "
            "sample count is the trajectory count, not the frame count")
    return problems


def check_counterfactual_attribution(records: Sequence[CorpusRecord]) -> list[str]:
    """A counterfactual must be credited to the route it actually describes."""
    problems = []
    for record in records:
        outcome = record.observed_outcome
        if outcome.counterfactual_route is None:
            continue
        if outcome.counterfactual_route not in record.semantic_target.eligible_routes:
            problems.append(
                f"record {record.frame_id}: counterfactual route "
                f"{outcome.counterfactual_route!r} is outside the eligible set")
        if (record.semantic_target.appropriate_route is not None
                and outcome.counterfactual_route
                == record.semantic_target.appropriate_route):
            problems.append(
                f"record {record.frame_id}: the counterfactual is the "
                "appropriate route, so the comparison is inverted")
    return problems


def run_all_checks(records: Sequence[CorpusRecord],
                   train: Sequence[CorpusRecord] = (),
                   holdout: Sequence[CorpusRecord] = (),
                   model_input_fields: Iterable[str] = ()
                   ) -> LeakageReport:
    """Run every check and aggregate actionable diagnostics."""
    problems: list[str] = []
    problems.extend(check_label_leakage(records))
    problems.extend(check_duplicates(records))
    problems.extend(check_provenance(records))
    problems.extend(check_independence(records))
    problems.extend(check_counterfactual_attribution(records))
    problems.extend(check_outcome_leakage(records, model_input_fields))
    if train or holdout:
        problems.extend(check_trajectory_overlap(train, holdout))
    return LeakageReport(tuple(problems))


__all__ = ["LeakageReport", "check_counterfactual_attribution",
           "check_duplicates", "check_independence", "check_label_leakage",
           "check_outcome_leakage", "check_provenance",
           "check_trajectory_overlap", "run_all_checks"]
