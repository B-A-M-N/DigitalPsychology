"""Independent, host-evidence-based intervention outcome assessment.

This module never asks the controlled agent whether an intervention worked.
The MCP runtime supplies applied-intervention receipts and evidence references;
an injected authority resolves those references from CSW and trusted host
validators before this evaluator assigns an outcome.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol

from .behavior_state import (PREVENTIVE_UTILITY_KINDS, BehaviorIntervention,
                             GoalSetSnapshot, InterventionOutcome, TaskSetOutcome)
from .attention import fairness_bound_actions


_SOURCE_KINDS = {
    "csw_authority": {"objective_progress", "criterion_verified", "objective_regression"},
    "host_validator": {"objective_progress", "criterion_verified", "criterion_rejected",
                       "objective_regression", "verification_quality_improved",
                       "verification_quality_unchanged", "verification_quality_degraded"},
    "host_artifact_observer": {"artifact_changed"},
    "cfw_observation": {"materially_different_attempt", "equivalent_approach"},
    "host_experiment_evaluator": {
        "intervention_effect_confirmed", "intervention_no_effect",
        "preventive_utility_confirmed", "progress_not_attributed",
    },
}
_COMPARATIVE_KINDS = {
    "intervention_effect_confirmed", "preventive_utility_confirmed",
    "intervention_no_effect", "progress_not_attributed",
}
_COMPARATIVE_CONFIRMATIONS = {
    "intervention_effect_confirmed", "preventive_utility_confirmed",
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
    preventive_utility: tuple[str, ...] = ()

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
        utility = self.preventive_utility
        if (not isinstance(utility, (tuple, list)) or len(utility) > 4
                or any(not isinstance(item, str) or item not in PREVENTIVE_UTILITY_KINDS
                       for item in utility)
                or len(utility) != len(set(utility))):
            raise ValueError("preventive utility evidence is malformed")
        if self.kind == "preventive_utility_confirmed":
            if not utility:
                raise ValueError("preventive confirmation requires a utility classification")
        elif utility:
            raise ValueError("only preventive confirmation may classify preventive utility")
        object.__setattr__(self, "preventive_utility", tuple(utility))


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
    goal_set: GoalSetSnapshot | None = None
    attention_last_seen: Mapping[str, int] = None

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
        if self.goal_set is not None and not isinstance(self.goal_set, GoalSetSnapshot):
            raise ValueError("evaluation goal set must be host validated")
        attention = dict(self.attention_last_seen or {})
        if (len(attention) > 256
                or any(not isinstance(key, str) or not key or len(key) > 128
                       or type(value) is not int or value < 0
                       for key, value in attention.items())):
            raise ValueError("evaluation attention history is malformed")
        object.__setattr__(self, "attention_last_seen", attention)


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
            if (fact.kind == "preventive_utility_confirmed"
                    and fact.action_index != frame.action_index):
                raise ValueError(
                    "preventive utility evidence must be bound to the evaluation boundary")
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
        task_set_outcome = self._task_set_outcome(intervention, frame)
        if task_set_outcome is not None:
            objective_progress = objective_progress or bool(
                task_set_outcome.targeted_progress_delta
                or (not target_ids and task_set_outcome.newly_verified))
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
        if task_set_outcome is not None:
            regression = regression or bool(task_set_outcome.regressions)
            regression = regression or (
                task_set_outcome.max_starvation_after > fairness_bound_actions(
                    len(frame.goal_set.runnable_items) if frame.goal_set is not None else 0))
        comparative = [fact for fact in facts if fact.kind in _COMPARATIVE_KINDS]
        comparison_ids = {fact.comparison_id for fact in comparative}
        if len(comparison_ids) > 1:
            raise ValueError("outcome evidence contains conflicting comparison identities")
        comparison_id = next(iter(comparison_ids), None)
        comparison_verdicts = {fact.kind for fact in comparative}
        if (_COMPARATIVE_CONFIRMATIONS & comparison_verdicts
                and comparison_verdicts - _COMPARATIVE_CONFIRMATIONS):
            raise ValueError("outcome evidence contains conflicting comparison verdicts")
        explicit_no_credit = any(fact.kind in {
            "intervention_no_effect", "progress_not_attributed"}
            for fact in comparative)
        explicit_effect = any(fact.kind == "intervention_effect_confirmed"
                              for fact in comparative)
        preventive_confirmation = any(
            fact.kind == "preventive_utility_confirmed" for fact in comparative)
        criterion_rejected = {
            fact.criterion_id for fact in facts
            if fact.kind == "criterion_rejected" and fact.criterion_id in target_ids
        }
        objective_rejected = (bool(target_ids) and target_ids.issubset(criterion_rejected))
        applied = intervention.intervention_id in frame.applied_intervention_ids
        overlapping = (set(frame.competing_intervention_ids)
                       | set(intervention.concurrent_intervention_ids))
        preventive_effect_supported = (
            preventive_confirmation
            and (not overlapping or all(
                fact.comparison_design == "matched_baseline_holdout"
                for fact in comparative
                if fact.kind == "preventive_utility_confirmed")))
        attention_only = (len(intervention.targets) == 1
                          and intervention.targets[0].target == "focus_criteria")
        fairness_progress = bool(
            attention_only and task_set_outcome is not None
            and task_set_outcome.targeted_progress_delta > 0
            and task_set_outcome.attention_coverage_delta > 0
            and (task_set_outcome.total_required_remaining_after
                 < task_set_outcome.total_required_remaining_before))
        if not applied:
            outcome = "unattributed"
            attribution = "insufficient"
            goal_progress = True if objective_progress else None
        elif explicit_no_credit:
            outcome = "unattributed"
            attribution = "not_attributed"
            goal_progress = True if objective_progress else False if regression else None
        elif regression:
            if overlapping and not explicit_effect:
                outcome = "unattributed"
                attribution = "confounded"
            else:
                outcome = "regressed"
                attribution = "supported"
            goal_progress = False
        elif fairness_progress or objective_progress:
            if overlapping and not explicit_effect:
                outcome = "unattributed"
                attribution = "confounded"
            else:
                outcome = "effective"
                attribution = "supported"
            goal_progress = True if objective_progress else False
        elif frame.evaluation_complete and objective_rejected:
            if overlapping and not explicit_effect:
                outcome = "unattributed"
                attribution = "confounded"
            else:
                outcome = "ineffective"
                attribution = "supported"
            goal_progress = False
        elif frame.evaluation_complete and preventive_effect_supported:
            outcome = "effective"
            attribution = "supported"
            goal_progress = False
        else:
            outcome = "inconclusive"
            attribution = "insufficient"
            goal_progress = None
        preventive_utility = ()
        if outcome == "effective" and preventive_effect_supported:
            preventive_utility = tuple(sorted({
                kind for fact in facts
                if fact.kind == "preventive_utility_confirmed"
                for kind in fact.preventive_utility
            }))

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
            target_criteria=intervention.target_criteria,
            task_set_outcome=task_set_outcome,
            preventive_utility=preventive_utility,
            semantic_decision_refs=intervention.semantic_decision_refs,
            semantic_influence_receipt_id=(
                intervention.semantic_influence_receipt_id),
        )

    @staticmethod
    def _task_set_outcome(intervention: BehaviorIntervention,
                          frame: InterventionEvaluationFrame) -> TaskSetOutcome | None:
        before = intervention.baseline_goal_set
        after = frame.goal_set
        if before is None or after is None:
            return None
        before_by_id = {item.criterion_id: item for item in before.items if item.required}
        after_by_id = {item.criterion_id: item for item in after.items if item.required}
        newly_verified = tuple(sorted(
            criterion_id for criterion_id, item in after_by_id.items()
            if item.status == "VERIFIED"
            and (criterion_id not in before_by_id
                 or before_by_id[criterion_id].status != "VERIFIED")))
        blocked_statuses = {"BLOCKED", "DEPENDENCY_BLOCKED", "DEFERRED_BY_OPERATOR"}
        newly_blocked = tuple(sorted(
            criterion_id for criterion_id, item in after_by_id.items()
            if item.status in blocked_statuses
            and (criterion_id not in before_by_id
                 or before_by_id[criterion_id].status not in blocked_statuses)))
        regressions = {
            criterion_id for criterion_id, item in before_by_id.items()
            if item.status == "VERIFIED"
            and (criterion_id not in after_by_id
                 or after_by_id[criterion_id].status != "VERIFIED")
        }
        progress_rank = {"READY": 0, "ACTIVE": 1, "VERIFIED": 2}
        for criterion_id, old in before_by_id.items():
            current = after_by_id.get(criterion_id)
            if current is None:
                regressions.add(criterion_id)
                continue
            if (old.status in progress_rank and current.status in progress_rank
                    and progress_rank[current.status] < progress_rank[old.status]):
                regressions.add(criterion_id)
            elif (current.status in blocked_statuses
                  and old.status not in blocked_statuses):
                regressions.add(criterion_id)
        targeted_progress = 0
        for criterion_id in intervention.target_criteria:
            old = before_by_id.get(criterion_id)
            current = after_by_id.get(criterion_id)
            if old is None or current is None:
                continue
            if (old.status in progress_rank and current.status in progress_rank
                    and progress_rank[current.status] > progress_rank[old.status]):
                targeted_progress += 1

        def max_starvation(snapshot: GoalSetSnapshot,
                           last_seen: Mapping[str, int], action_index: int) -> int:
            return max((max(0, action_index - last_seen.get(item.criterion_id,
                                                           action_index))
                        for item in snapshot.runnable_items), default=0)

        before_counts = before.required_counts
        after_counts = after.required_counts
        covered_before = sum(
            intervention.baseline_action_index
            <= intervention.baseline_attention_last_seen.get(item.criterion_id, -1)
            for item in before_by_id.values())
        covered_after = sum(
            intervention.baseline_action_index
            <= frame.attention_last_seen.get(item.criterion_id, -1)
            <= frame.action_index
            for item in after_by_id.values())
        return TaskSetOutcome(
            targeted_progress_delta=targeted_progress,
            total_required_remaining_before=before_counts["remaining_required"],
            total_required_remaining_after=after_counts["remaining_required"],
            newly_verified=newly_verified,
            newly_blocked=newly_blocked,
            regressions=tuple(sorted(regressions)),
            max_starvation_before=max_starvation(
                before, intervention.baseline_attention_last_seen,
                intervention.baseline_action_index),
            max_starvation_after=max_starvation(
                after, frame.attention_last_seen, frame.action_index),
            attention_coverage_delta=covered_after - covered_before,
        )


__all__ = ["HostOutcomeFact", "InterventionEventContext", "InterventionEvaluationFrame",
           "InterventionEvaluator", "OutcomeEvidenceAuthority"]
