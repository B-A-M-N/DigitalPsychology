"""DecisionOutcomeRecord — one locally correlated record of a decision and its outcome.

Answers the directive's first question: *can existing DP/CSW/CFW records already
express a common DecisionOutcomeRecord?*

They can. Every identifier needed already exists; nothing here invents a new
contract. This module is an ADAPTER that joins them under one locally generated
correlation ID:

  * task context + candidate StateWorks + controller selection
        -> DP ``ControlSessionDescriptor`` + the prepared decision's basis
  * Laya shadow judgment and confidence
        -> DP ``SemanticInfluenceReceipt`` (already records the counterfactual)
  * CSW activation, eligibility, revision
        -> CSW ``CSWBehaviorSnapshot`` (revision, active/eligible, registry hash)
  * CFW-governed actions and enforcement
        -> CFW gate receipts carried on the DP commit receipt
  * observed outcome and whether the intervention helped
        -> host-authoritative evidence via DP's ``InterventionOutcome``

Three label tiers are kept DISTINCT, because they have different epistemic status
and conflating them is what makes a corpus circular:

  contract_validity   machine-checkable: was the action legal under CSW/CFW?
  observed_outcome    machine-checkable: what actually happened?
  semantic_preference NOT machine-derivable: was this the BEST legal route?

``semantic_preference`` is deliberately left ``None`` unless an independent
labeller supplies it. A contract validator can establish that an action was legal;
it cannot establish that an otherwise legal StateWork was the best choice.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

CONTRACT_VALIDITY = "contract_validity"
OBSERVED_OUTCOME = "observed_outcome"
SEMANTIC_PREFERENCE = "semantic_preference"


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=str,
        ensure_ascii=False).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class BeforeRecord:
    """What was known and chosen before the action ran."""

    task_context: Mapping[str, Any]
    candidate_stateworks: tuple[str, ...]
    controller_selected: str | None
    controller_reason: str
    # Laya's shadow hypothesis, if a provider was consulted.
    laya_question_id: str | None = None
    laya_selected: str | None = None
    laya_confidence: float | None = None
    laya_margin: float | None = None
    laya_distribution: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if len(self.candidate_stateworks) > 32:
            raise ValueError("candidate StateWork set exceeds its bound")
        if len(set(self.candidate_stateworks)) != len(self.candidate_stateworks):
            raise ValueError("candidate StateWorks must be unique")
        if self.controller_selected is not None and (
                self.controller_selected not in self.candidate_stateworks):
            raise ValueError(
                "controller selection is outside the candidate set")
        if self.laya_confidence is not None and not (
                0.0 <= float(self.laya_confidence) <= 1.0):
            raise ValueError("Laya confidence must be between zero and one")


@dataclass(frozen=True)
class DuringRecord:
    """What the agent actually did, and whether CSW/CFW permitted it."""

    # Ordered (tool_name, is_error) pairs as executed.
    actions: tuple[tuple[str, bool], ...] = ()
    # CSW/CFW enforcement outcomes observed during execution.
    transitions_rejected: tuple[str, ...] = ()
    gate_blocks: tuple[str, ...] = ()
    retries: int = 0

    def __post_init__(self) -> None:
        if self.retries < 0:
            raise ValueError("retry count cannot be negative")


@dataclass(frozen=True)
class AfterRecord:
    """Authoritative state once execution finished."""

    active_stateworks: tuple[str, ...]
    required_evidence: tuple[str, ...]
    evidence_satisfied: tuple[str, ...]
    task_success: bool | None
    # Whether the intervention improved on the counterfactual. UNKNOWN unless a
    # counterfactual was actually observed.
    counterfactual_available: bool = False
    intervention_improved: bool | None = None

    def __post_init__(self) -> None:
        if self.intervention_improved is not None and not self.counterfactual_available:
            raise ValueError(
                "an improvement claim requires an observed counterfactual")


@dataclass(frozen=True)
class DecisionOutcomeRecord:
    """One locally correlated decision and its outcome.

    ``correlation_id`` is generated locally and carried by every event in the
    episode, which is what makes the record joinable without reconstructing a
    join between anonymized historical datasets.
    """

    correlation_id: str
    recorded_at: str
    before: BeforeRecord
    during: DuringRecord
    after: AfterRecord
    # Identity binding, so a record cannot be attributed to the wrong artifact.
    checkpoint_revision: str
    checkpoint_content_hash: str
    statework_contract_version: str
    policy_snapshot_hash: str
    trajectory_id: str
    subject_ref: str

    # Label tiers, filled by different mechanisms.
    contract_violations: tuple[str, ...] = ()
    semantic_target: str | None = None
    semantic_label_provenance: str | None = None

    def __post_init__(self) -> None:
        if not self.correlation_id or len(self.correlation_id) > 128:
            raise ValueError("correlation ID must be bounded text")
        if self.semantic_target is not None and (
                self.semantic_target not in self.before.candidate_stateworks):
            raise ValueError(
                "semantic target is outside the candidate StateWork set")
        if self.semantic_label_provenance is not None and (
                self.semantic_target is None):
            raise ValueError("label provenance requires a semantic target")

    # ------------------------------------------------ machine-derivable labels
    @property
    def contract_validity(self) -> str:
        """Tier 1: legal or illegal, from CSW/CFW constraints alone."""
        return "illegal" if self.contract_violations else "legal"

    @property
    def observed_outcome(self) -> str:
        """Tier 2: what happened, from host-authoritative execution."""
        if self.after.task_success is None:
            return "unknown"
        return "succeeded" if self.after.task_success else "failed"

    @property
    def semantic_preference(self) -> str | None:
        """Tier 3: the independently established best route, if labelled."""
        return self.semantic_target

    @property
    def controller_was_correct(self) -> bool | None:
        """Whether the deterministic controller chose the labelled route."""
        if self.semantic_target is None:
            return None
        return self.before.controller_selected == self.semantic_target

    @property
    def laya_was_correct(self) -> bool | None:
        """Whether Laya's shadow hypothesis matched the labelled route."""
        if self.semantic_target is None or self.before.laya_selected is None:
            return None
        return self.before.laya_selected == self.semantic_target

    def to_dict(self) -> dict[str, Any]:
        return {
            "correlation_id": self.correlation_id,
            "recorded_at": self.recorded_at,
            "before": {
                "task_context": dict(self.before.task_context),
                "candidate_stateworks": list(self.before.candidate_stateworks),
                "controller_selected": self.before.controller_selected,
                "controller_reason": self.before.controller_reason,
                "laya": {
                    "question_id": self.before.laya_question_id,
                    "selected": self.before.laya_selected,
                    "confidence": self.before.laya_confidence,
                    "margin": self.before.laya_margin,
                    "distribution": dict(self.before.laya_distribution),
                },
            },
            "during": {
                "actions": [list(a) for a in self.during.actions],
                "transitions_rejected": list(self.during.transitions_rejected),
                "gate_blocks": list(self.during.gate_blocks),
                "retries": self.during.retries,
            },
            "after": {
                "active_stateworks": list(self.after.active_stateworks),
                "required_evidence": list(self.after.required_evidence),
                "evidence_satisfied": list(self.after.evidence_satisfied),
                "task_success": self.after.task_success,
                "counterfactual_available": self.after.counterfactual_available,
                "intervention_improved": self.after.intervention_improved,
            },
            "labels": {
                "contract_validity": self.contract_validity,
                "contract_violations": list(self.contract_violations),
                "observed_outcome": self.observed_outcome,
                "semantic_preference": self.semantic_preference,
                "semantic_label_provenance": self.semantic_label_provenance,
            },
            "identity": {
                "checkpoint_revision": self.checkpoint_revision,
                "checkpoint_content_hash": self.checkpoint_content_hash,
                "statework_contract_version": self.statework_contract_version,
                "policy_snapshot_hash": self.policy_snapshot_hash,
                "trajectory_id": self.trajectory_id,
                "subject_ref": self.subject_ref,
            },
        }


def new_correlation_id(*, seed: str) -> str:
    """Locally generated correlation ID for one decision episode."""
    return "dor-" + _digest(seed)[:24]


def derive_contract_violations(before: BeforeRecord, during: DuringRecord,
                               after: AfterRecord) -> tuple[str, ...]:
    """Tier-1 labels derived from CSW/CFW constraints, not from judgement.

    These are the violations a contract validator can establish. Deliberately
    narrow: it records illegality, never whether a legal route was preferable.
    """
    violations: list[str] = []
    selected = before.controller_selected
    active = set(after.active_stateworks)
    if selected is not None and selected not in active:
        violations.append(
            f"controller selected {selected!r} but it is not active after execution")
    for rejected in during.transitions_rejected:
        violations.append(f"transition rejected by CSW: {rejected}")
    for blocked in during.gate_blocks:
        violations.append(f"action blocked by CFW gate: {blocked}")
    missing = set(after.required_evidence) - set(after.evidence_satisfied)
    if missing:
        violations.append(
            f"required evidence unsatisfied: {sorted(missing)}")
    return tuple(violations)


def validate_episode(record: DecisionOutcomeRecord) -> tuple[str, ...]:
    """Actionable diagnostics for one record. Empty means well-formed."""
    problems: list[str] = []
    if not record.before.candidate_stateworks:
        problems.append("episode has no candidate StateWorks")
    if record.semantic_target is not None and record.controller_was_correct is None:
        problems.append("semantic target present but controller selection is missing")
    # A recorded improvement without a counterfactual is an unsupported claim.
    if (record.after.intervention_improved is not None
            and not record.after.counterfactual_available):
        problems.append(
            "intervention_improved asserted without an observed counterfactual")
    return tuple(problems)


__all__ = [
    "CONTRACT_VALIDITY",
    "OBSERVED_OUTCOME",
    "SEMANTIC_PREFERENCE",
    "AfterRecord",
    "BeforeRecord",
    "DecisionOutcomeRecord",
    "DuringRecord",
    "derive_contract_violations",
    "new_correlation_id",
    "validate_episode",
]
