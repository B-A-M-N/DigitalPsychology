"""Materialize a selected policy as bounded interventions and directives."""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable, Mapping

from ..behavior_state import (
    MAX_ACTIVE_INTERVENTIONS, MAX_TARGETS_PER_INTERVENTION, BehaviorDirective,
    BehaviorIntervention, BehaviorState, BehavioralDecision, InterventionOutcome,
    InterventionTarget, ModeLease,
)
from .fast_loop_input import FastLoopInput
from .interventions import InterventionStateBuilder, _mode_exit_condition
from ..lease_window import lease_window
from .statework_selector import rank_stateworks


class DirectiveBuilder:
    """Turn one selected bounded candidate into versioned DP directives."""
    def __init__(self, *, intervention_state_builder: InterventionStateBuilder,
                 intervention_expiry_actions: int, digest: Callable[[Any], str]) -> None:
        self.intervention_state_builder = intervention_state_builder
        self.intervention_expiry_actions = intervention_expiry_actions
        self.digest = digest

    def build(self, *, context: FastLoopInput, original: BehaviorState,
              working: BehaviorState, next_revision: int,
              next_focus: tuple[str, ...], attention_ids: tuple[str, ...],
              interventions: list[BehaviorIntervention],
              outcomes: list[InterventionOutcome],
              directives: list[BehaviorDirective], selection: Any,
              candidate_pool: Any, current_condition_ids: Mapping[str, str],
              previously_effective: set[str], triggers: set[str],
              condition_for: Callable[..., Any],
              applies_to_attention: Callable[[tuple[str, ...]], bool],
              semantic_statework_preference: str | None = None,
              semantic_statework_confidence: float = 0.0,
              semantic_statework_support: tuple[tuple[str, float], ...] = (),
              semantic_test_strategy_preference: str | None = None,
              semantic_test_strategy_confidence: float = 0.0,
              semantic_test_strategy_support: tuple[tuple[str, float], ...] = (),
              semantic_decomposition_preference: str | None = None,
              semantic_decomposition_confidence: float = 0.0,
              semantic_decomposition_support: tuple[tuple[str, float], ...] = (),
              semantic_integration_preference: str | None = None,
              semantic_integration_confidence: float = 0.0,
              semantic_integration_support: tuple[tuple[str, float], ...] = (),
              advisory_semantic_families: frozenset[str] = frozenset(),
              experimental_route_preference: str | None = None,
              ) -> BehavioralDecision:
            selected_option = (candidate_pool.option_for(selection.candidate.candidate_id)
                               if selection.candidate is not None else None)
            if selection.abstained:
                # Abstention gathers information through a bounded diagnostic
                # policy; it does not turn uncertainty into an empirical estimate.
                selected_option = candidate_pool.fallback()
                if selected_option is None:
                    selected_option = next(
                        item for item in candidate_pool.options if item.role == "CONTINUE")
                reason = "abstain and gather more evidence: " + str(
                    selection.abstention_reason or "policy confidence is insufficient")
            if selected_option is None:
                raise RuntimeError("selected policy was not generated for the current conditions")
            selected_candidate = selected_option.candidate
            proposed = list(selected_option.changes)
            if not selection.abstained:
                reason = ("no behavioral change required" if selected_option.role == "CONTINUE"
                          else f"selected {selected_option.role.lower()} policy for "
                          + (selected_candidate.cause or "current conditions"))

            existing_condition_ids = ({item.condition_id for item in interventions
                                       if applies_to_attention(item.target_criteria)}
                                      | {current_condition_ids[cause]
                                         for cause in previously_effective & triggers})
            grouped: dict[str, list[tuple[str, str, str, str]]] = {}
            intervention_capacity_exhausted = False
            target_capacity_exhausted = False
            for target, value, cause, effect in proposed:
                if (target.startswith("framework:")
                        and target.split(":", 1)[1].lower()
                        not in context.available_frameworks):
                    continue
                if target.startswith("framework:") and target.split(":", 1)[1] == "ward":
                    continue
                previous = self.intervention_state_builder.target_value(working, target)
                if previous == value:
                    continue
                grouped.setdefault(cause, []).append((target, previous, value, effect))

            for cause, target_values in grouped.items():
                target_criteria = tuple(next_focus or attention_ids)[:8]
                condition = condition_for(cause, target_criteria)
                if condition.condition_id in existing_condition_ids:
                    continue
                if len(interventions) >= MAX_ACTIVE_INTERVENTIONS:
                    intervention_capacity_exhausted = True
                    continue
                if len(target_values) > MAX_TARGETS_PER_INTERVENTION:
                    target_capacity_exhausted = True
                    continue
                expected_effect = target_values[0][3]
                expiry = 1 if cause == "progress_observed" else self.intervention_expiry_actions
                window = lease_window(
                    "PRE_ACTION" if context.pre_action else "POST_ACTION",
                    context.host_action_index, expiry)
                intervention = self.intervention_state_builder.new_intervention(
                    scope=context.scope, revision=next_revision, cause=cause,
                    expected_effect=expected_effect,
                    targets=tuple((target, previous, value)
                                  for target, previous, value, _effect in target_values),
                    expiry=expiry,
                    expires_at_action=window.expires_at_action,
                    target_criteria=tuple(next_focus or attention_ids),
                    baseline_criterion_status=context.criterion_status,
                    baseline_action_index=max(0, window.effective_from_action - 1),
                    concurrent_intervention_ids=tuple(
                        item.intervention_id for item in interventions),
                    baseline_goal_set=context.goal_set,
                    baseline_attention_last_seen=context.attention_last_seen,
                    condition_id=condition.condition_id,
                    condition_basis_digest=condition.condition_basis_digest,
                    recovery_epoch_id=condition.condition_id)
                interventions.append(intervention)
                for target, _previous, value, effect in target_values:
                    operation = ("set_framework" if target.startswith("framework:")
                                 else "set_mode" if target == "mode" else "set_control")
                    directive_target = target.split(":", 1)[1] if target.startswith("framework:") else target
                    directives.append(BehaviorDirective(
                        directive_id="dir-" + self.digest({
                            "intervention": intervention.intervention_id,
                            "target": target, "value": value})[:24],
                        operation=operation, target=directive_target, value=value,
                        cause=cause, expected_effect=effect, scope=context.scope,
                        expires_after_actions=expiry,
                        intervention_id=intervention.intervention_id,
                        expires_at_action=window.expires_at_action))
                    working = self.intervention_state_builder.set_target(working, target, value)

            mode_lease = original.mode_lease
            if working.cognitive_mode != original.cognitive_mode:
                mode_cause = next((item.cause for item in reversed(directives)
                                   if item.target == "mode"),
                                  selected_candidate.cause or "mode_transition")
                mode_lease = ModeLease(
                    mode=working.cognitive_mode,
                    entered_action=max(
                        0, context.host_action_index - (1 if context.pre_action else 0)),
                    minimum_actions=2, max_actions=6, cause=mode_cause,
                    exit_condition=_mode_exit_condition(working.cognitive_mode),
                    baseline_approach_epoch=context.approach_epoch or None,
                )
            elif mode_lease is not None and mode_lease.mode != working.cognitive_mode:
                mode_lease = None
            working = replace(
                working,
                revision=next_revision,
                mode_lease=mode_lease,
                focus_criteria=next_focus,
                attention_lease=context.attention_lease,
                attention_last_seen=context.attention_last_seen,
                active_stateworks=context.csw_snapshot.active_stateworks,
                active_interventions=tuple(interventions),
                intervention_history=tuple(
                    (list(original.intervention_history) + outcomes)[-32:]),
                # WARD's hard floor is invariant in BehaviorState.
                framework_modes={**dict(working.framework_modes), "ward": "mandatory"},
            )

            if experimental_route_preference is not None:
                # Host-owned experimental assignment: an authorized forced
                # treatment, not a model prediction. It is declared as such so
                # provenance stays auditable and it cannot satisfy the model
                # confidence gate by asserting a synthetic confidence value.
                preference = rank_stateworks(
                    selected_candidate.cognitive_mode,
                    context.csw_snapshot.eligible_stateworks,
                    context.csw_snapshot.active_stateworks,
                    semantic_preference=experimental_route_preference,
                    semantic_confidence=1.0,
                    provenance="host_experiment",
                    limit=1)
            else:
                advisory = (advisory_semantic_families
                            if isinstance(advisory_semantic_families,
                                           (frozenset, set, tuple, list))
                            else frozenset())
                preference = None
                for _family, _preference, _confidence, _support in (
                    ("route", semantic_statework_preference,
                     semantic_statework_confidence, semantic_statework_support),
                    ("test_strategy", semantic_test_strategy_preference,
                     semantic_test_strategy_confidence,
                     semantic_test_strategy_support),
                    ("decomposition", semantic_decomposition_preference,
                     semantic_decomposition_confidence,
                     semantic_decomposition_support),
                    ("integration", semantic_integration_preference,
                     semantic_integration_confidence,
                     semantic_integration_support),
                ):
                    if _family == "route":
                        if not (context.semantic_opportunity_question_id == "route_preference"
                                and semantic_statework_preference is not None):
                            continue
                    else:
                        if (_family not in advisory
                                and f"{_family}_preference" not in advisory):
                            continue
                        if _preference is None:
                            continue
                    preference = rank_stateworks(
                        selected_candidate.cognitive_mode,
                        context.csw_snapshot.eligible_stateworks,
                        context.csw_snapshot.active_stateworks,
                        semantic_preference=_preference,
                        semantic_confidence=_confidence,
                        preference_support=_support or None,
                        provenance="model_advisory",
                        limit=1)
                    break
                if preference is None:
                    preference = selected_candidate.statework_preferences[:1]
            if preference and not any(
                    any(target.target == f"statework:{preference[0]}" for target in item.targets)
                    for item in working.active_interventions):
                # Keep one causal unit when a workflow preference accompanies a
                # behavioral intervention. Separate simultaneous intervention IDs
                # would make progress unassignable even though both settings were
                # applied as one bounded DP decision.
                prior_intervention_ids = {
                    item.intervention_id for item in original.active_interventions}
                new_owner = next((item for item in working.active_interventions
                                  if item.intervention_id not in prior_intervention_ids), None)
                if new_owner is not None:
                    intervention = new_owner
                    if len(intervention.targets) >= MAX_TARGETS_PER_INTERVENTION:
                        target_capacity_exhausted = True
                        preference = ()
                    else:
                        updated = replace(
                            intervention,
                            targets=intervention.targets + (InterventionTarget(
                                target=f"statework:{preference[0]}",
                                previous_value="unselected", applied_value="preferred"),))
                        interventions = [updated if item.intervention_id == updated.intervention_id
                                         else item for item in working.active_interventions]
                        working = replace(working, active_interventions=tuple(interventions))
                elif working.active_interventions:
                    # Do not overlap a new workflow-selection intervention with a
                    # still-evaluating behavior change. Reconsider after its
                    # evidence window closes.
                    preference = ()
                else:
                    if len(interventions) >= MAX_ACTIVE_INTERVENTIONS:
                        intervention_capacity_exhausted = True
                        preference = ()
                if preference and new_owner is None and not working.active_interventions:
                    condition = condition_for(
                        "statework_fit", tuple(next_focus or attention_ids)[:8])
                    statework_window = lease_window(
                        "PRE_ACTION" if context.pre_action else "POST_ACTION",
                        context.host_action_index, 1)
                    intervention = self.intervention_state_builder.new_intervention(
                        scope=context.scope, revision=next_revision, cause="statework_fit",
                        expected_effect="Use the eligible StateWork that best fits the current behavioral mode; CSW validates and commits it.",
                        targets=((f"statework:{preference[0]}", "unselected", "preferred"),),
                        expiry=1,
                        expires_at_action=statework_window.expires_at_action,
                        target_criteria=tuple(next_focus or attention_ids),
                        baseline_criterion_status=context.criterion_status,
                        baseline_action_index=max(0, statework_window.effective_from_action - 1),
                        concurrent_intervention_ids=tuple(
                            item.intervention_id for item in interventions),
                        baseline_goal_set=context.goal_set,
                        baseline_attention_last_seen=context.attention_last_seen,
                        condition_id=condition.condition_id,
                        condition_basis_digest=condition.condition_basis_digest,
                        recovery_epoch_id=condition.condition_id)
                    interventions = list(working.active_interventions) + [intervention]
                    working = replace(working, active_interventions=tuple(interventions))
                if preference:
                    owner = intervention
                    owner_expiry = owner.expires_at_action
                    if owner_expiry is None:
                        raise RuntimeError("StateWork owner has no absolute lease expiry")
                    directive = BehaviorDirective(
                        directive_id="dir-" + self.digest({
                            "intervention": intervention.intervention_id,
                            "statework": preference[0]})[:24],
                        operation="prefer_statework", target=preference[0], value="preferred",
                        cause=intervention.cause, expected_effect=intervention.expected_effect,
                        scope=context.scope,
                        expires_after_actions=owner.expires_after_actions,
                        intervention_id=owner.intervention_id,
                        expires_at_action=owner_expiry)
                    directives.append(directive)
                    working = replace(working, active_interventions=tuple(interventions))
            else:
                preference = ()

            if intervention_capacity_exhausted:
                reason = "intervention_capacity_exhausted"
            elif target_capacity_exhausted:
                reason = "intervention_target_capacity_exhausted"

            decision_id = "decision-" + self.digest({
                "scope": context.scope, "revision": original.revision,
                "state": working.to_dict(), "directives": [item.to_dict() for item in directives],
                "selected_candidate_id": selected_candidate.candidate_id,
            })[:24]
            return BehavioralDecision(
                decision_id=decision_id, scope=context.scope,
                expected_revision=original.revision, state=working,
                directives=tuple(directives), preferred_stateworks=tuple(preference),
                intervention_outcomes=tuple(outcomes), reason=reason,
                selected_candidate_id=selected_candidate.candidate_id)
