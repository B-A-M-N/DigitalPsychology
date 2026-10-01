"""Independent, host-evidence-based intervention outcome assessment.

This module never asks the controlled agent whether an intervention worked.
The MCP runtime supplies applied-intervention receipts and evidence references;
an injected authority resolves those references from CSW and trusted host
validators before this evaluator assigns an outcome.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol

from .behavior_state import BehaviorIntervention, InterventionOutcome


_SOURCE_KINDS = {
    "csw_authority": {"objective_progress", "criterion_verified", "objective_regression"},
    "host_validator": {"objective_progress", "criterion_verified", "criterion_rejected",
                       "objective_regression", "verification_quality_improved",
                       "verification_quality_unchanged", "verification_quality_degraded"},
    "host_artifact_observer": {"artifact_changed"},
    "cfw_observation": {"materially_different_attempt", "equivalent_approach"},
    "host_experiment_evaluator": {
        "intervention_effect_confirmed", "intervention_no_effect",
        "progress_not_attributed",
    },
}
_COMPARATIVE_KINDS = {
    "intervention_effect_confirmed", "intervention_no_effect",
    "progress_not_attributed",
}


@dataclass(frozen=True)
class HostOutcomeFact:
    """A fact returned by a trusted evidence authority, never by an agent."""

    evidence_ref: str
    scope: str
    source: str
    kind: str
    # Source-local revision, useful for evidence traceability but not compared
    # with the DP behavior-state revision.
    observed_revision: int
    criterion_id: str | None = None
    action_index: int | None = None
    intervention_id: str | None = None
    comparison_id: str | None = None
    comparison_design: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.evidence_ref, str) or not self.evidence_ref.strip() \
                or len(self.evidence_ref) > 256:
            raise ValueError("outcome evidence reference must be bounded text")
        if not isinstance(self.scope, str) or not self.scope.strip() or len(self.scope) > 256:
            raise ValueError("outcome evidence scope must be bounded text")
        if self.kind not in _SOURCE_KINDS.get(self.source, set()):
            raise ValueError("outcome fact source is not authorized for its kind")
        if not isinstance(self.observed_revision, int) or self.observed_revision < 0:
            raise ValueError("outcome evidence revision must be non-negative")
        if (self.action_index is not None
                and (not isinstance(self.action_index, int) or self.action_index < 0)):
            raise ValueError("outcome evidence action index must be non-negative")
        if self.criterion_id is not None and (
                not isinstance(self.criterion_id, str) or not self.criterion_id
                or len(self.criterion_id) > 128):
            raise ValueError("outcome criterion ID must be bounded text")
        for name in ("intervention_id", "comparison_id"):
            value = getattr(self, name)
            if value is not None and (
                    not isinstance(value, str) or not value or len(value) > 128):
                raise ValueError(f"outcome {name} must be bounded text or null")
        if self.kind in _COMPARATIVE_KINDS:
            if (self.source != "host_experiment_evaluator"
                    or not self.intervention_id or not self.comparison_id
                    or self.comparison_design not in {
                        "matched_baseline", "matched_baseline_holdout"}):
                raise ValueError(
                    "comparative outcome facts require an intervention-bound comparison")


class OutcomeEvidenceAuthority(Protocol):
    """Resolve a host-owned evidence reference into its validated fact."""

    def resolve_outcome_fact(self, evidence_ref: str, *, scope: str) -> HostOutcomeFact: ...


@dataclass(frozen=True)
class InterventionEventContext:
    """Trusted runtime identity needed to persist intervention telemetry."""

    namespace_id: str
    application_id: str
    application_instance_id: str
    agent_instance_id: str
    session_id: str
    task_id: str
    attempt_id: str
    segment_id: str
    agent_id: str
    behavioral_subject: str
    goal_contract_hash: str | None = None

    def __post_init__(self) -> None:
        for name in ("namespace_id", "application_id", "application_instance_id",
                     "agent_instance_id", "session_id", "task_id", "attempt_id",
                     "segment_id", "agent_id", "behavioral_subject"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or len(value) > 512:
                raise ValueError(f"event context {name} must be bounded text")
        if self.goal_contract_hash is not None and (
                not isinstance(self.goal_contract_hash, str)
                or len(self.goal_contract_hash) > 128):
            raise ValueError("goal contract hash must be bounded text")


@dataclass(frozen=True)
class InterventionEvaluationFrame:
    """Bounded end-of-window metadata emitted by the runtime host."""

    scope: str
    observed_revision: int
    action_index: int
    evaluation_complete: bool
    applied_intervention_ids: tuple[str, ...]
    competing_intervention_ids: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.scope, str) or not self.scope.strip() or len(self.scope) > 256:
            raise ValueError("evaluation scope must be bounded text")
        if self.observed_revision < 0 or self.action_index < 0:
            raise ValueError("evaluation revisions and action indexes must be non-negative")
        if not isinstance(self.evaluation_complete, bool):
            raise ValueError("evaluation_complete must be boolean")
        for name in ("applied_intervention_ids", "competing_intervention_ids", "evidence_refs"):
            values = getattr(self, name)
            limit = 16
            if not isinstance(values, (tuple, list)) or len(values) > limit:
                raise ValueError(f"{name} must be a bounded sequence")
            if any(not isinstance(value, str) or not value or len(value) > 256
                   for value in values) or len(values) != len(set(values)):
                raise ValueError(f"{name} must contain unique bounded strings")
            object.__setattr__(self, name, tuple(values))


class InterventionEvaluator:
    """Assign supported credit only from scoped, post-intervention host facts.

    ``effective`` means objective progress was independently evidenced during
    an isolated evaluation window. Overlapping interventions produce
    ``unattributed``. An explicit failed criterion validation can establish
    ``ineffective``; missing evidence remains ``inconclusive``.
    """

    def assess(self, intervention: BehaviorIntervention,
               frame: InterventionEvaluationFrame, *,
               authority: OutcomeEvidenceAuthority) -> InterventionOutcome:
        if frame.scope != intervention.scope:
            raise ValueError("intervention evaluation scope mismatch")
        if frame.observed_revision <= intervention.start_revision:
            raise ValueError("evaluation must follow the intervention revision")
        if frame.action_index < intervention.baseline_action_index:
            raise ValueError("evaluation action index predates the intervention")
        elapsed = frame.action_index - intervention.baseline_action_index
        horizon = int(intervention.evaluation_horizon_actions or
                      intervention.expires_after_actions)
        if frame.evaluation_complete and elapsed < horizon:
            raise ValueError("evaluation cannot complete before its action horizon")

        facts = []
        for reference in frame.evidence_refs:
            fact = authority.resolve_outcome_fact(reference, scope=frame.scope)
            if (not isinstance(fact, HostOutcomeFact)
                    or fact.evidence_ref != reference or fact.scope != frame.scope):
                raise ValueError("outcome authority returned an invalid evidence binding")
            if fact.action_index is None:
                raise ValueError("outcome evidence must be bound to a host action index")
            if not intervention.baseline_action_index < fact.action_index <= frame.action_index:
                raise ValueError("outcome evidence falls outside the intervention window")
            if fact.intervention_id is not None and fact.intervention_id != intervention.intervention_id:
                raise ValueError("comparative evidence is bound to another intervention")
            facts.append(fact)

        kinds = {fact.kind for fact in facts}
        target_ids = set(intervention.target_criteria)
        verified = {
            fact.criterion_id for fact in facts
            if fact.kind == "criterion_verified" and fact.criterion_id in target_ids
            and intervention.baseline_criterion_status.get(fact.criterion_id)
            in {"unmet", "in_progress"}
        }
        objective_progress_facts = {
            fact.criterion_id for fact in facts
            if fact.kind == "objective_progress"
            and fact.criterion_id in target_ids
            and intervention.baseline_criterion_status.get(fact.criterion_id)
            in {"unmet", "in_progress"}
        }
        objective_progress = bool(verified or objective_progress_facts)
        if not target_ids:
            # Some objective validators report task-level progress without a
            # criterion ID. Accept that only for interventions whose host did
            # not supply criterion targets at all.
            objective_progress = objective_progress or "objective_progress" in kinds
        artifact_changed = True if "artifact_changed" in kinds else None
        repeated_approach = (True if "equivalent_approach" in kinds else
                             False if "materially_different_attempt" in kinds else None)
        if "verification_quality_degraded" in kinds:
            verification_quality = "degraded"
        elif "verification_quality_improved" in kinds:
            verification_quality = "improved"
        elif "verification_quality_unchanged" in kinds:
            verification_quality = "unchanged"
        else:
            verification_quality = "unknown"

        regression = "objective_regression" in kinds
        comparative = [fact for fact in facts if fact.kind in _COMPARATIVE_KINDS]
        comparison_ids = {fact.comparison_id for fact in comparative}
        if len(comparison_ids) > 1:
            raise ValueError("outcome evidence contains conflicting comparison identities")
        comparison_id = next(iter(comparison_ids), None)
        comparison_verdicts = {fact.kind for fact in comparative}
        if ("intervention_effect_confirmed" in comparison_verdicts
                and len(comparison_verdicts) > 1):
            raise ValueError("outcome evidence contains conflicting comparison verdicts")
        explicit_no_credit = any(fact.kind in {
            "intervention_no_effect", "progress_not_attributed"}
            for fact in comparative)
        explicit_effect = any(fact.kind == "intervention_effect_confirmed"
                              for fact in comparative)
        criterion_rejected = {
            fact.criterion_id for fact in facts
            if fact.kind == "criterion_rejected" and fact.criterion_id in target_ids
        }
        objective_rejected = (bool(target_ids) and target_ids.issubset(criterion_rejected))
        applied = intervention.intervention_id in frame.applied_intervention_ids
        overlapping = (set(frame.competing_intervention_ids)
                       | set(intervention.concurrent_intervention_ids))
        if not applied:
            outcome = "unattributed"
            attribution = "insufficient"
            goal_progress = True if objective_progress else None
        elif explicit_no_credit:
            outcome = "unattributed"
            attribution = "not_attributed"
            goal_progress = True if objective_progress else False if regression else None
        elif regression and (objective_progress or overlapping) and not explicit_effect:
            outcome = "unattributed"
            attribution = "confounded"
            goal_progress = True if objective_progress else False
        elif explicit_no_credit:
            outcome = "unattributed"
            attribution = "not_attributed"
            goal_progress = True if objective_progress else False if regression else None
        elif regression:
            outcome = "regressed"
            attribution = "supported"
            goal_progress = False
        elif objective_progress:
            if overlapping and not explicit_effect:
                outcome = "unattributed"
                attribution = "confounded"
            else:
                outcome = "effective"
                attribution = "supported"
            goal_progress = True
        elif frame.evaluation_complete and objective_rejected:
            if overlapping and not explicit_effect:
                outcome = "unattributed"
                attribution = "confounded"
            else:
                outcome = "ineffective"
                attribution = "supported"
            goal_progress = False
        else:
            outcome = "inconclusive"
            attribution = "insufficient"
            goal_progress = None

        return InterventionOutcome(
            intervention_id=intervention.intervention_id,
            cause=intervention.cause,
            outcome=outcome,
            observed_revision=frame.observed_revision,
            evidence_refs=tuple(fact.evidence_ref for fact in facts),
            scope=frame.scope,
            attribution=attribution,
            criterion_ids_progressed=tuple(sorted(verified | objective_progress_facts)),
            goal_progress=goal_progress,
            artifact_changed=artifact_changed,
            repeated_approach=repeated_approach,
            verification_quality=verification_quality,
            comparison_id=comparison_id,
        )


__all__ = ["HostOutcomeFact", "InterventionEventContext", "InterventionEvaluationFrame",
           "InterventionEvaluator", "OutcomeEvidenceAuthority"]
