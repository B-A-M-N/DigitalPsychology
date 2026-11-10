"""Deliverable A — three-observation corpus schema for route_preference.

Three observations are kept structurally separate because they have different
provenance and answer different questions:

  A. SemanticTarget   -- what the situation MEANT, independently labelled.
  B. ControllerDecision -- what the deterministic controller CHOSE, and why.
  C. ObservedOutcome  -- what actually HAPPENED, host-authoritative.

Collapsing them is what makes a corpus circular. If the semantic target is derived
from the controller's selected action, the evaluation measures agreement with the
controller rather than semantic correctness.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

LABEL_PROVENANCE_CONTROLLER = "controller_decision"

_REQUIRED_IDENTITY = (
    "checkpoint_revision", "checkpoint_content_hash", "statework_contract_version",
    "calibration_id", "policy_snapshot_hash", "trajectory_id", "subject_ref",
)


@dataclass(frozen=True)
class SemanticTarget:
    """What the situation meant, established INDEPENDENTLY of the controller.

    ``label_provenance`` records who established the label. A value naming the
    controller decision is rejected: that is the circularity this schema exists
    to prevent.
    """

    frame_id: str
    appropriate_route: str | None
    eligible_routes: tuple[str, ...]
    label_provenance: str
    labelled_by: str
    abstention_expected: bool = False

    def __post_init__(self) -> None:
        if not self.frame_id or len(self.frame_id) > 128:
            raise ValueError("semantic target frame ID is malformed")
        if not self.eligible_routes or len(self.eligible_routes) > 32:
            raise ValueError("semantic target must name the eligible route set")
        if len(set(self.eligible_routes)) != len(self.eligible_routes):
            raise ValueError("semantic target repeats an eligible route")
        if not self.label_provenance or len(self.label_provenance) > 128:
            raise ValueError("semantic target requires label provenance")
        if self.label_provenance == LABEL_PROVENANCE_CONTROLLER:
            raise ValueError(
                "semantic target must not be derived from the controller decision")
        if self.appropriate_route is not None:
            if self.appropriate_route not in self.eligible_routes:
                raise ValueError(
                    "semantic target names a route outside the eligible set")
            if self.abstention_expected:
                raise ValueError(
                    "a target naming a route cannot also expect abstention")


@dataclass(frozen=True)
class ControllerDecision:
    """What the deterministic controller chose, and the constraints it faced."""

    selected_route: str | None
    available_candidates: tuple[str, ...]
    policy_snapshot_hash: str
    decision_reason: str
    abstained: bool = False

    def __post_init__(self) -> None:
        if not self.policy_snapshot_hash or len(self.policy_snapshot_hash) > 128:
            raise ValueError("controller decision requires a policy snapshot hash")
        if not self.decision_reason or len(self.decision_reason) > 512:
            raise ValueError("controller decision requires a bounded reason")
        if self.selected_route is not None and self.abstained:
            raise ValueError("a controller decision cannot both select and abstain")


@dataclass(frozen=True)
class ObservedOutcome:
    """What actually happened, from host-authoritative evidence."""

    success_criteria_met: bool | None
    evidence_refs: tuple[str, ...]
    observed_by: str
    # The alternative route's outcome. UNKNOWN unless counterfactual_available.
    counterfactual_route: str | None = None
    counterfactual_outcome: bool | None = None

    def __post_init__(self) -> None:
        if not self.observed_by or len(self.observed_by) > 128:
            raise ValueError("observed outcome requires an authoritative observer")
        if self.counterfactual_route is not None and self.counterfactual_outcome is None:
            raise ValueError(
                "a counterfactual route requires its outcome, or must be left unknown")


@dataclass(frozen=True)
class CorpusRecord:
    """One labelled observation, binding the three views to one artifact set."""

    frame_id: str
    semantic_target: SemanticTarget
    controller_decision: ControllerDecision
    observed_outcome: ObservedOutcome
    checkpoint_revision: str
    checkpoint_content_hash: str
    statework_contract_version: str
    calibration_id: str
    policy_snapshot_hash: str
    trajectory_id: str
    subject_ref: str

    def __post_init__(self) -> None:
        if self.frame_id != self.semantic_target.frame_id:
            raise ValueError("corpus record and semantic target frame IDs differ")
        for name in _REQUIRED_IDENTITY:
            value = getattr(self, name)
            if not isinstance(value, str) or not value or len(value) > 256:
                raise ValueError(f"corpus record requires a bounded {name}")
        if self.policy_snapshot_hash != self.controller_decision.policy_snapshot_hash:
            raise ValueError(
                "corpus policy snapshot must match the controller decision basis")

    @property
    def counterfactual_available(self) -> bool:
        """True only when the alternative route's outcome is actually known.

        Unknown counterfactuals stay explicitly unknown. An observed failure is
        NOT proof that an alternative StateWork would have succeeded.
        """
        return (self.observed_outcome.counterfactual_route is not None
                and self.observed_outcome.counterfactual_outcome is not None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "frame_id": self.frame_id,
            "semantic_target": {
                "appropriate_route": self.semantic_target.appropriate_route,
                "eligible_routes": list(self.semantic_target.eligible_routes),
                "label_provenance": self.semantic_target.label_provenance,
                "labelled_by": self.semantic_target.labelled_by,
                "abstention_expected": self.semantic_target.abstention_expected,
            },
            "controller_decision": {
                "selected_route": self.controller_decision.selected_route,
                "available_candidates": list(
                    self.controller_decision.available_candidates),
                "policy_snapshot_hash": self.controller_decision.policy_snapshot_hash,
                "decision_reason": self.controller_decision.decision_reason,
                "abstained": self.controller_decision.abstained,
            },
            "observed_outcome": {
                "success_criteria_met": self.observed_outcome.success_criteria_met,
                "evidence_refs": list(self.observed_outcome.evidence_refs),
                "observed_by": self.observed_outcome.observed_by,
                "counterfactual_route": self.observed_outcome.counterfactual_route,
                "counterfactual_outcome": self.observed_outcome.counterfactual_outcome,
            },
            "counterfactual_available": self.counterfactual_available,
            "identity": {name: getattr(self, name) for name in _REQUIRED_IDENTITY},
        }


def validate_corpus(records: Sequence[CorpusRecord]) -> list[str]:
    """Return actionable diagnostics for an invalid corpus. Empty means valid."""
    problems: list[str] = []
    seen_frames: dict[str, int] = {}
    for index, record in enumerate(records):
        if record.frame_id in seen_frames:
            problems.append(
                f"record {index}: duplicate frame_id {record.frame_id!r} "
                f"(first seen at {seen_frames[record.frame_id]})")
        seen_frames[record.frame_id] = index
        if (record.semantic_target.appropriate_route is None
                and not record.semantic_target.abstention_expected):
            problems.append(
                f"record {index}: neither a route nor expected abstention; "
                "the semantic target is unusable")
    return problems


__all__ = [
    "LABEL_PROVENANCE_CONTROLLER",
    "ControllerDecision",
    "CorpusRecord",
    "ObservedOutcome",
    "SemanticTarget",
    "validate_corpus",
]
