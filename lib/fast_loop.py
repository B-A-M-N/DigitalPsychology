"""Fast behavioral adaptation owned by DigitalPsychology.

The controller is a pure decision function.  A trusted runtime host supplies
CFW signals, a bounded authoritative CSW snapshot, outcomes, and an optional
DP System-1 judgment; the host persists the returned BehaviorState, checks its
revision, intersects directives with hard constraints, and applies them.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Optional

from .behavior_state import BehaviorIntervention, BehaviorState, BehavioralDecision
from .condition_identity import identify_condition
from .control.fast_loop_input import FastLoopInput
from .control.semantic_assessment import SemanticAssessment, _Judgment, assess_semantics
from .control.interventions import InterventionStateBuilder
from .control.intervention_lifecycle import InterventionLifecycle
from .control.directive_builder import DirectiveBuilder
from .control.candidate_generator import (
    BehaviorCandidateGenerator, CandidateConditions,
)
from .control.detectors import evaluate_conditions
from .control.policy_selector import (
    BehaviorPolicyConstraints, BehaviorPolicyPrior, BehaviorPolicySelector,
)


class BehavioralController:
    """DP's deterministic fast-loop policy over host-observed inputs."""

    def __init__(self, *, support_action_threshold: int = 4,
                 retry_threshold: int = 2, semantic_threshold: float = 0.80,
                 intervention_expiry_actions: int = 3) -> None:
        if not 1 <= support_action_threshold <= 64:
            raise ValueError("support action threshold is out of bounds")
        if not 1 <= retry_threshold <= 16:
            raise ValueError("retry threshold is out of bounds")
        if not 0.5 <= semantic_threshold <= 1.0:
            raise ValueError("semantic threshold must be between 0.5 and one")
        if not 1 <= intervention_expiry_actions <= 16:
            raise ValueError("intervention expiry is out of bounds")
        self.support_action_threshold = support_action_threshold
        self.retry_threshold = retry_threshold
        self.semantic_threshold = semantic_threshold
        self.intervention_expiry_actions = intervention_expiry_actions
        self.policy_selector = BehaviorPolicySelector()
        self.candidate_generator = BehaviorCandidateGenerator()
        self.intervention_builder = InterventionStateBuilder(
            expiry_actions=intervention_expiry_actions, digest=self._digest)
        self.intervention_lifecycle = InterventionLifecycle(
            state_builder=self.intervention_builder, digest=self._digest)
        self.directive_builder = DirectiveBuilder(
            intervention_state_builder=self.intervention_builder,
            intervention_expiry_actions=intervention_expiry_actions, digest=self._digest)

    @staticmethod
    def _digest(value: Any) -> str:
        body = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(body.encode("utf-8")).hexdigest()

    @staticmethod
    def _semantic(judgment: Optional[_Judgment]) -> SemanticAssessment:
        return assess_semantics(judgment)

    @staticmethod
    def _target_value(state: BehaviorState, target: str) -> str:
        return InterventionStateBuilder.target_value(state, target)

    @staticmethod
    def _set_target(state: BehaviorState, target: str, value: str) -> BehaviorState:
        return InterventionStateBuilder.set_target(state, target, value)

    def _new_intervention(self, **kwargs: Any) -> BehaviorIntervention:
        return self.intervention_builder.new_intervention(**kwargs)

    def decide(self, context: FastLoopInput, *,
               judgment: Optional[_Judgment] = None,
               policy_priors: tuple[BehaviorPolicyPrior, ...] = ()) -> BehavioralDecision:
        """Return a DP decision. The caller must CAS and constrain its application."""
        original = context.state
        semantic = self._semantic(judgment)
        next_revision = original.revision + 1
        detected = evaluate_conditions(
            context, semantic, retry_threshold=self.retry_threshold,
            support_action_threshold=self.support_action_threshold,
            semantic_threshold=self.semantic_threshold)
        attention_ids = detected.attention_ids
        focused_progress = detected.focused_progress
        runnable_count = detected.runnable_count
        task_complete = detected.task_complete
        contradiction = detected.contradiction
        retry = detected.retry
        drift = detected.drift
        pressure = detected.pressure

        condition_criteria = tuple(context.attention_lease.criterion_ids
                                   if context.attention_lease is not None
                                   else original.focus_criteria)[:8]

        def condition_for(cause: str,
                          target_criteria: tuple[str, ...] = condition_criteria):
            return identify_condition(
                cause=cause,
                target_criteria=target_criteria,
                goal_set=context.goal_set,
                csw_snapshot=context.csw_snapshot,
                evidence_refs=context.evidence_refs,
                approach_epoch=context.approach_epoch,
            )

        def applies_to_attention(criterion_ids: tuple[str, ...]) -> bool:
            return not criterion_ids or not attention_ids or bool(
                set(criterion_ids) & set(attention_ids))

        cycle = self.intervention_lifecycle.advance(
            context, original, detected, next_revision=next_revision,
            condition_for=condition_for, applies_to_attention=applies_to_attention)
        working = cycle.working
        interventions = cycle.interventions
        outcomes = cycle.outcomes
        directives = cycle.directives
        current_condition_ids = cycle.current_condition_ids
        previously_effective = cycle.previously_effective
        triggers = cycle.triggers
        next_focus = cycle.next_focus
        mode_hold = cycle.mode_hold
        failed_intervention = cycle.failed_intervention

        candidate_conditions = CandidateConditions(
            contradiction=contradiction,
            task_complete=task_complete,
            failed_intervention=failed_intervention,
            no_runnable_work=(
                (context.goal_set is not None and runnable_count == 0)
                or (failed_intervention and context.goal_set is None
                    and not context.objective_criteria)),
            retry=retry,
            drift=drift,
            pressure=pressure,
            focused_progress=focused_progress,
            artifact_changed=context.artifact_changed,
            trigger_codes=tuple(sorted(triggers)),
            contradiction_cause=("operator_contradiction"
                                 if "operator_contradiction" in triggers
                                 else "stale_assumption"),
        )
        candidate_pool = self.candidate_generator.generate(
            working,
            next_focus=next_focus,
            conditions=candidate_conditions,
            csw_snapshot=context.csw_snapshot,
            available_frameworks=context.available_frameworks,
            semantic_action_relation=semantic.action_relation,
            semantic_progress_relation=semantic.progress_relation,
            semantic_retry_relation=semantic.retry_relation,
            semantic_goal_drift=semantic.goal_drift,
            semantic_statework_preference=semantic.statework_preference,
            semantic_statework_confidence=semantic.statework_confidence,
            semantic_action_confidence=semantic.action_confidence,
            semantic_progress_confidence=semantic.progress_confidence,
            semantic_retry_confidence=semantic.retry_confidence,
            semantic_drift_confidence=semantic.goal_drift_confidence,
        )
        required_mode = None
        if contradiction:
            required_mode = "REINSPECT"
        elif task_complete:
            required_mode = "READY_TO_COMPLETE"
        elif failed_intervention:
            required_mode = "REPLAN" if runnable_count > 0 else "BLOCKED"
        elif context.goal_set is not None and runnable_count == 0:
            required_mode = "BLOCKED"
        elif mode_hold:
            required_mode = original.cognitive_mode
        selection = self.policy_selector.select(
            (item.candidate for item in candidate_pool.options),
            constraints=BehaviorPolicyConstraints(
                current_state=working,
                available_frameworks=context.available_frameworks,
                goal_set=context.goal_set,
                required_mode=required_mode),
            csw_snapshot=context.csw_snapshot,
            goal_set=context.goal_set,
            policy_context=context.policy_context,
            priors=policy_priors,
            semantic_evidence=candidate_pool.semantic_evidence,
            rule_support={item.candidate.candidate_id: item.deterministic_support
                          for item in candidate_pool.options},
            attention_lease=context.attention_lease,
            attention_last_seen=context.attention_last_seen,
            current_action_index=(context.host_action_index
                                  if context.attention_lease is not None else None))
        return self.directive_builder.build(
            context=context, original=original, working=working,
            next_revision=next_revision, next_focus=next_focus,
            attention_ids=attention_ids, interventions=interventions, outcomes=outcomes,
            directives=directives, selection=selection, candidate_pool=candidate_pool,
            current_condition_ids=current_condition_ids,
            previously_effective=previously_effective, triggers=triggers,
            condition_for=condition_for, applies_to_attention=applies_to_attention)


__all__ = ["BehavioralController", "FastLoopInput", "SemanticAssessment"]
