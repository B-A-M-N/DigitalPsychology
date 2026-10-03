"""Mode leases and reversible target changes for DP interventions."""
from __future__ import annotations

import json
from dataclasses import replace
from typing import Any, Callable, Mapping, Optional

from ..behavior_state import (
    BehaviorIntervention, BehaviorState, GoalSetSnapshot, InterventionTarget,
)
from .fast_loop_input import FastLoopInput
from ..lease_window import lease_window

def _mode_exit_condition(mode: str) -> str:
    return {
        "RECOVER": "progress_or_new_approach",
        "VERIFY": "verification_result",
        "REINSPECT": "fresh_evidence",
        "REPLAN": "plan_committed",
        "DIAGNOSE": "diagnostic_evidence",
    }.get(mode, "minimum_duration")
def _mode_lease_holds(state: BehaviorState, context: FastLoopInput, *,
                      material_progress: bool, hard_interrupt: bool) -> bool:
    lease = state.mode_lease
    if lease is None or lease.mode != state.cognitive_mode or hard_interrupt:
        return False
    completed_action = max(
        0, context.host_action_index - (1 if context.pre_action else 0))
    elapsed = max(0, completed_action - lease.entered_action)
    if lease.max_actions is not None and elapsed >= lease.max_actions:
        return False
    if elapsed >= lease.minimum_actions:
        return False

    signals = tuple(signal for signal in context.cfw_signals if signal.active)
    signal_codes = {signal.code for signal in signals}
    exit_ready = False
    if lease.exit_condition == "progress_or_new_approach":
        exit_ready = material_progress or bool(
            context.approach_epoch and lease.baseline_approach_epoch
            and context.approach_epoch != lease.baseline_approach_epoch)
    elif lease.exit_condition == "verification_result":
        exit_ready = context.action_completed and (
            material_progress or context.artifact_changed
            or bool(signal_codes & {"verification_passed", "verification_failed",
                                    "verification_complete", "criterion_verified"}))
    elif lease.exit_condition == "fresh_evidence":
        exit_ready = context.action_completed and bool(context.evidence_refs)
    elif lease.exit_condition == "plan_committed":
        exit_ready = context.action_completed and (
            context.attention_lease != state.attention_lease)
    elif lease.exit_condition == "diagnostic_evidence":
        exit_ready = context.action_completed and (bool(context.evidence_refs)
                                                   or material_progress)
    return not exit_ready


class InterventionStateBuilder:
    """Build bounded intervention records and materialize target state."""
    def __init__(self, *, expiry_actions: int, digest: Callable[[Any], str]) -> None:
        self.intervention_expiry_actions = expiry_actions
        self._digest = digest

    def new_intervention(self, *, scope: str, revision: int, cause: str,
                          expected_effect: str,
                          targets: tuple[tuple[str, str, str], ...],
                          expiry: Optional[int] = None,
                          expires_at_action: Optional[int] = None,
                          target_criteria: tuple[str, ...] = (),
                          baseline_criterion_status: Optional[Mapping[str, str]] = None,
                          baseline_action_index: int = 0,
                          concurrent_intervention_ids: tuple[str, ...] = (),
                          baseline_goal_set: GoalSetSnapshot | None = None,
                          baseline_attention_last_seen: Mapping[str, int] | None = None,
                          condition_id: str = "legacy-condition",
                          condition_basis_digest: str = "legacy-condition"
                          ) -> BehaviorIntervention:
        intervention_id = "intv-" + self._digest({
            "scope": scope, "revision": revision, "cause": cause,
            "targets": targets, "target_criteria": target_criteria,
        })[:24]
        records = tuple(InterventionTarget(
            target=target, previous_value=previous, applied_value=applied)
            for target, previous, applied in targets)
        duration = expiry or self.intervention_expiry_actions
        absolute_expiry = (expires_at_action if expires_at_action is not None else
                           lease_window("POST_ACTION", baseline_action_index,
                                        duration).expires_at_action)
        return BehaviorIntervention(
            intervention_id=intervention_id, cause=cause,
            expected_effect=expected_effect, start_revision=revision,
            scope=scope, expires_after_actions=duration,
            actions_elapsed=0, targets=records,
            expires_at_action=absolute_expiry,
            target_criteria=target_criteria,
            baseline_criterion_status={
                key: (baseline_criterion_status or {}).get(key, "unknown")
                for key in target_criteria
            },
            baseline_action_index=baseline_action_index,
            evaluation_horizon_actions=expiry or self.intervention_expiry_actions,
            concurrent_intervention_ids=concurrent_intervention_ids,
            baseline_goal_set=baseline_goal_set,
            baseline_attention_last_seen=baseline_attention_last_seen or {},
            condition_id=condition_id,
            condition_basis_digest=condition_basis_digest)
    @staticmethod
    def set_target(state: BehaviorState, target: str, value: str) -> BehaviorState:
        if target == "mode":
            return replace(state, cognitive_mode=value,
                           mode_lease=(state.mode_lease if value == state.cognitive_mode
                                       else None))
        if target.startswith("framework:"):
            key = target.split(":", 1)[1]
            if key == "ward" and value != "mandatory":
                return state
            modes = dict(state.framework_modes)
            modes[key] = value
            return replace(state, framework_modes=modes)
        if target == "focus_criteria":
            return replace(state, focus_criteria=tuple(json.loads(value)),
                           attention_lease=None)
        if target == "statework_preference":
            return state
        if target in {"verification_intensity", "exploration_budget", "retry_policy",
                      "recovery_level", "context_strategy", "response_strategy"}:
            prior = getattr(state, target)
            converted: Any = int(value) if isinstance(prior, int) else value
            return replace(state, **{target: converted})
        return state
    @staticmethod
    def target_value(state: BehaviorState, target: str) -> str:
        if target == "mode":
            return state.cognitive_mode
        if target.startswith("framework:"):
            return state.framework_modes.get(target.split(":", 1)[1], "off")
        if target == "focus_criteria":
            return json.dumps(list(state.focus_criteria), separators=(",", ":"))
        if target == "statework_preference":
            return "none"
        value = getattr(state, target, None)
        if value is None or isinstance(value, (tuple, list)):
            return json.dumps(list(value or ()), separators=(",", ":"))
        return str(value)
