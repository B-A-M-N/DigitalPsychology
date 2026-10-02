"""Advance active DP interventions and restore settings when their lease closes."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Callable

from ..behavior_state import (
    BehaviorDirective, BehaviorIntervention, BehaviorState, InterventionOutcome,
)
from .fast_loop_input import FastLoopInput
from .interventions import InterventionStateBuilder, _mode_lease_holds


@dataclass
class InterventionCycle:
    working: BehaviorState
    interventions: list[BehaviorIntervention]
    outcomes: list[InterventionOutcome]
    directives: list[BehaviorDirective]
    current_condition_ids: dict[str, str]
    previously_effective: set[str]
    triggers: set[str]
    next_focus: tuple[str, ...]
    mode_hold: bool
    failed_intervention: bool


class InterventionLifecycle:
    """Evaluate intervention receipts, age leases, and prepare reversals."""
    def __init__(self, *, state_builder: InterventionStateBuilder,
                 digest: Callable[[Any], str]) -> None:
        self.state_builder = state_builder
        self.digest = digest

    def _condition_history(self, context: FastLoopInput, original: BehaviorState,
                           *, trigger_codes: tuple[str, ...],
                           condition_for: Callable[..., Any],
                           applies_to_attention: Callable[[tuple[str, ...]], bool]
                           ) -> "ConditionHistory":
        triggers = set(trigger_codes)
        active_interventions_by_id = {
            item.intervention_id: item for item in original.active_interventions
        }
        normalized_assessments = []
        for assessment in context.intervention_assessments:
            active_intervention = active_interventions_by_id.get(
                assessment.intervention_id)
            if (active_intervention is not None
                    and assessment.condition_id == "legacy-condition"
                    and assessment.condition_basis_digest == "legacy-condition"):
                assessment = replace(
                    assessment,
                    condition_id=active_intervention.condition_id,
                    condition_basis_digest=active_intervention.condition_basis_digest,
                )
            normalized_assessments.append(assessment)

        current_condition_ids = {
            cause: condition_for(cause).condition_id for cause in triggers
        }
        previously_failed = {
            item.cause for item in original.intervention_history
            if item.outcome in {"ineffective", "regressed", "unattributed",
                                "inconclusive", "expired"}
            and applies_to_attention(item.target_criteria)
            and current_condition_ids.get(item.cause) == item.condition_id
        }
        blocking_failed_causes = previously_failed & triggers
        if blocking_failed_causes:
            triggers.add("failed_cause_blocked")
        previously_effective = {
            item.cause for item in original.intervention_history
            if item.outcome == "effective" and applies_to_attention(item.target_criteria)
            and current_condition_ids.get(item.cause) == item.condition_id
        } | {item.cause for item in normalized_assessments
             if item.outcome == "effective" and applies_to_attention(item.target_criteria)
             and current_condition_ids.get(item.cause) == item.condition_id}

        return ConditionHistory(
            normalized_assessments=normalized_assessments,
            current_condition_ids=current_condition_ids,
            previously_effective=previously_effective,
            blocking_failed_causes=blocking_failed_causes, triggers=triggers)

    def _advance_records(self, context: FastLoopInput, original: BehaviorState,
                         history: "ConditionHistory",
                         *, next_revision: int) -> "InterventionProgress":
        triggers = history.triggers
        normalized_assessments = history.normalized_assessments
        interventions: list[BehaviorIntervention] = []
        outcomes: list[InterventionOutcome] = []
        directives: list[BehaviorDirective] = []
        expired_causes: set[tuple[str, tuple[str, ...], str]] = set()
        assessments = {item.intervention_id: item
                       for item in normalized_assessments}
        active_ids = {item.intervention_id for item in original.active_interventions}
        if not set(assessments).issubset(active_ids):
            raise ValueError("outcome assessment does not match an active intervention")
        working = original
        for item in original.active_interventions:
            if context.pre_action:
                # Candidate evaluation can change the next behavior mode, but
                # it cannot age or close an intervention before an action runs.
                interventions.append(item)
                continue
            elapsed = item.actions_elapsed + (1 if context.action_completed else 0)
            updated = replace(item, actions_elapsed=min(elapsed, item.expires_after_actions))
            assessment = assessments.get(item.intervention_id)
            if assessment is not None:
                if (assessment.condition_id not in {"legacy-condition", item.condition_id}
                        or assessment.condition_basis_digest not in {
                            "legacy-condition", item.condition_basis_digest}):
                    raise ValueError("intervention outcome condition differs from its receipt")
                assessment = replace(
                    assessment, condition_id=item.condition_id,
                    condition_basis_digest=item.condition_basis_digest)
                if (assessment.cause != item.cause
                        or assessment.observed_revision <= item.start_revision
                        or assessment.condition_id != item.condition_id
                        or assessment.condition_basis_digest != item.condition_basis_digest):
                    raise ValueError("intervention outcome does not match its active record")
                if (assessment.outcome == "inconclusive"
                        and updated.actions_elapsed < item.evaluation_horizon_actions):
                    # Partial evidence is not a terminal outcome. Keep the
                    # intervention live until its declared observation
                    # horizon so a missing fact cannot trigger early rollback.
                    interventions.append(updated)
                    continue
                outcomes.append(assessment)
                if assessment.outcome in {"ineffective", "regressed", "unattributed",
                                          "inconclusive", "expired"}:
                    expired_causes.add((item.cause, item.target_criteria,
                                        item.condition_id))
                continue
            if item.cause not in triggers:
                outcomes.append(InterventionOutcome(
                    intervention_id=item.intervention_id, cause=item.cause,
                    outcome="withdrawn", observed_revision=next_revision,
                    scope=context.scope, attribution="insufficient",
                    target_criteria=item.target_criteria,
                    condition_id=item.condition_id,
                    condition_basis_digest=item.condition_basis_digest))
                continue
            if elapsed >= item.evaluation_horizon_actions:
                outcomes.append(InterventionOutcome(
                    intervention_id=item.intervention_id, cause=item.cause,
                    outcome="expired", observed_revision=next_revision,
                    scope=context.scope, attribution="insufficient",
                    target_criteria=item.target_criteria,
                    condition_id=item.condition_id,
                    condition_basis_digest=item.condition_basis_digest))
                expired_causes.add((item.cause, item.target_criteria,
                                    item.condition_id))
                continue
            interventions.append(updated)

        return InterventionProgress(
            working=working, interventions=interventions, outcomes=outcomes,
            directives=directives, expired_causes=expired_causes)

    def _restore_closed(self, context: FastLoopInput, original: BehaviorState,
                        detected: Any, progress: "InterventionProgress",
                        next_focus: tuple[str, ...], *, next_revision: int
                        ) -> tuple[BehaviorState, list[BehaviorDirective], bool]:
        working = progress.working
        interventions = progress.interventions
        outcomes = progress.outcomes
        directives = progress.directives
        focused_progress = detected.focused_progress
        runnable_count = detected.runnable_count
        task_complete = detected.task_complete
        contradiction = detected.contradiction
        # Restore the settings owned by a closed intervention, unless another
        # still-active intervention currently owns that same target.
        active_targets = {target.target for item in interventions for target in item.targets}
        # Attention is host-owned. A closing intervention must not restore a
        # stale focus value while the current lease still owns focus_criteria.
        if context.attention_lease is not None:
            active_targets.add("focus_criteria")
        if next_focus != working.focus_criteria:
            active_targets.add("focus_criteria")
        closed_ids = {item.intervention_id for item in original.active_interventions} - {
            item.intervention_id for item in interventions}
        adverse_outcome = any(item.outcome in {
            "ineffective", "regressed", "expired", "unattributed", "inconclusive",
        } for item in outcomes)
        hard_mode_interrupt = bool(
            contradiction or task_complete or runnable_count == 0
            or context.regression_observed or adverse_outcome
            or any(signal.active and (
                signal.severity == "critical" or signal.source.lower() == "ward")
                for signal in context.cfw_signals))
        mode_hold = _mode_lease_holds(
            original, context, material_progress=focused_progress,
            hard_interrupt=hard_mode_interrupt)
        if mode_hold:
            # A withdrawn temporary intervention cannot silently bypass the
            # mode lease by restoring its saved mode value.
            active_targets.add("mode")
        for closed in original.active_interventions:
            if closed.intervention_id not in closed_ids:
                continue
            for target in reversed(closed.targets):
                if target.target in active_targets:
                    continue
                if target.target.startswith("statework:"):
                    if target.previous_value == "unselected":
                        statework_id = target.target.split(":", 1)[1]
                        if statework_id in working.active_stateworks:
                            directives.append(BehaviorDirective(
                                directive_id="dir-" + self.digest({
                                    "intervention": closed.intervention_id,
                                    "target": target.target, "value": "unselected",
                                    "revision": next_revision})[:24],
                                operation="release_statework", target=statework_id,
                                value="unselected", cause=(
                                    "intervention_expired" if any(
                                        outcome.intervention_id == closed.intervention_id
                                        and outcome.outcome == "expired"
                                        for outcome in outcomes) else "trigger_cleared"),
                                expected_effect=(
                                    "Release the CSW StateWork activated by this temporary DP lease."),
                                scope=context.scope, expires_after_actions=1,
                                intervention_id=closed.intervention_id))
                    continue
                before = self.state_builder.target_value(working, target.target)
                working = self.state_builder.set_target(working, target.target, target.previous_value)
                if before == target.previous_value:
                    continue
                outcome = next((item.outcome for item in outcomes
                                if item.intervention_id == closed.intervention_id), "withdrawn")
                cause = ("intervention_expired" if outcome == "expired"
                         else "trigger_cleared")
                operation = ("set_framework" if target.target.startswith("framework:")
                             else "set_mode" if target.target == "mode" else "set_control")
                directive_target = (target.target.split(":", 1)[1]
                                    if target.target.startswith("framework:") else target.target)
                directives.append(BehaviorDirective(
                    directive_id="dir-" + self.digest({
                        "intervention": closed.intervention_id,
                        "target": target.target, "value": target.previous_value,
                        "revision": next_revision})[:24],
                    operation=operation, target=directive_target,
                    value=target.previous_value, cause=cause,
                    expected_effect="Release a temporary behavior change after its trigger clears or expires.",
                    scope=context.scope, expires_after_actions=1,
                    intervention_id=closed.intervention_id))

        return working, directives, mode_hold

    def advance(self, context: FastLoopInput, original: BehaviorState, detected: Any,
                *, next_revision: int,
                condition_for: Callable[..., Any],
                applies_to_attention: Callable[[tuple[str, ...]], bool]
                ) -> InterventionCycle:
        history = self._condition_history(
            context, original, trigger_codes=detected.trigger_codes,
            condition_for=condition_for,
            applies_to_attention=applies_to_attention)
        progress = self._advance_records(
            context, original, history, next_revision=next_revision)
        next_focus = (context.attention_lease.criterion_ids
                      if context.attention_lease is not None else ())
        working, directives, mode_hold = self._restore_closed(
            context, original, detected, progress, next_focus,
            next_revision=next_revision)
        triggers = history.triggers
        failed_intervention = bool(
            history.blocking_failed_causes
            or any(cause in triggers and applies_to_attention(target_criteria)
                   and history.current_condition_ids.get(cause) == condition_id
                   for cause, target_criteria, condition_id in progress.expired_causes))
        if failed_intervention:
            triggers.add("failed_cause_blocked")
        return InterventionCycle(
            working=working, interventions=progress.interventions,
            outcomes=progress.outcomes, directives=directives,
            current_condition_ids=history.current_condition_ids,
            previously_effective=history.previously_effective,
            triggers=triggers, next_focus=next_focus, mode_hold=mode_hold,
            failed_intervention=failed_intervention)


@dataclass
class ConditionHistory:
    normalized_assessments: list[InterventionOutcome]
    current_condition_ids: dict[str, str]
    previously_effective: set[str]
    blocking_failed_causes: set[str]
    triggers: set[str]


@dataclass
class InterventionProgress:
    working: BehaviorState
    interventions: list[BehaviorIntervention]
    outcomes: list[InterventionOutcome]
    directives: list[BehaviorDirective]
    expired_causes: set[tuple[str, tuple[str, ...], str]]
