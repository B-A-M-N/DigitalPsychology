"""Fast behavioral adaptation owned by DigitalPsychology.

The controller is a pure decision function.  A trusted runtime host supplies
CFW signals, a bounded authoritative CSW snapshot, outcomes, and an optional
DP System-1 judgment; the host persists the returned BehaviorState, checks its
revision, intersects directives with hard constraints, and applies them.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
from typing import Any, Mapping, Optional, Protocol

from .behavior_state import (
    BehaviorIntervention, BehaviorState, BehaviorDirective, BehavioralDecision,
    CSWBehaviorSnapshot, FrameworkSignal, InterventionOutcome,
    InterventionTarget, StateWorkOption,
)


class _Answer(Protocol):
    selected: str
    raw_confidence: float
    calibrated_confidence: Optional[float]


class _Judgment(Protocol):
    def answer(self, question_id: str) -> Optional[_Answer]: ...


@dataclass(frozen=True)
class SemanticAssessment:
    """Small provider-neutral view of DP System-1 results."""

    action_relation: Optional[str] = None
    progress_relation: Optional[str] = None
    retry_relation: Optional[str] = None
    goal_drift: Optional[bool] = None
    confidence: float = 0.0
    retry_confidence: float = 0.0
    goal_drift_confidence: float = 0.0

    def __post_init__(self) -> None:
        for name, choices in (
            ("action_relation", {"advances_primary_goal", "removes_concrete_blocker",
                                 "required_verification", "diagnostic_support", "unrelated"}),
            ("progress_relation", {"goal_progress", "blocker_removed", "diagnostic_progress",
                                   "verification_progress", "recovery_progress", "no_progress"}),
            ("retry_relation", {"materially_new", "same_premise_new_action",
                                "equivalent_retry", "unrelated"}),
        ):
            value = getattr(self, name)
            if value is not None and value not in choices:
                raise ValueError(f"unsupported semantic {name}")
        if self.goal_drift is not None and not isinstance(self.goal_drift, bool):
            raise ValueError("goal_drift must be a boolean or null")
        for confidence in (self.confidence, self.retry_confidence,
                           self.goal_drift_confidence):
            if not 0.0 <= float(confidence) <= 1.0:
                raise ValueError("semantic confidence must be between zero and one")


@dataclass(frozen=True)
class FastLoopInput:
    scope: str
    state: BehaviorState
    csw_snapshot: CSWBehaviorSnapshot
    objective_criteria: tuple[str, ...] = ()
    cfw_signals: tuple[FrameworkSignal, ...] = ()
    active_trigger_codes: tuple[str, ...] = ()
    actions_since_goal_progress: int = 0
    equivalent_retry_count: int = 0
    action_completed: bool = False
    progress_made: bool = False
    regression_observed: bool = False
    artifact_changed: bool = False
    context_pressure: float = 0.0
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.scope, str) or not self.scope.strip() or len(self.scope) > 256:
            raise ValueError("fast-loop scope must be non-empty bounded text")
        if len(self.objective_criteria) > 8 or len(set(self.objective_criteria)) != len(self.objective_criteria):
            raise ValueError("objective criteria must be unique and bounded to eight")
        if any(not isinstance(value, str) or not value or len(value) > 128
               for value in self.objective_criteria):
            raise ValueError("objective criterion IDs must be bounded text")
        if len(self.cfw_signals) > 32 or len(self.active_trigger_codes) > 32:
            raise ValueError("fast-loop input exceeds bounded signal counts")
        if self.actions_since_goal_progress < 0 or self.equivalent_retry_count < 0:
            raise ValueError("outcome counters must be non-negative")
        if not 0.0 <= float(self.context_pressure) <= 1.0:
            raise ValueError("context pressure must be between zero and one")
        if len(self.evidence_refs) > 16 or any(
                not isinstance(value, str) or not value or len(value) > 256
                for value in self.evidence_refs):
            raise ValueError("evidence references must be bounded")


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

    @staticmethod
    def _semantic(judgment: Optional[_Judgment]) -> SemanticAssessment:
        if judgment is None:
            return SemanticAssessment()

        def read(question_id: str) -> tuple[Optional[str], float]:
            answer = judgment.answer(question_id)
            if answer is None:
                return None, 0.0
            confidence = (answer.calibrated_confidence
                          if answer.calibrated_confidence is not None
                          else answer.raw_confidence)
            return answer.selected, float(confidence)

        action, action_conf = read("action_relation")
        progress, progress_conf = read("progress_relation")
        retry, retry_conf = read("retry_relation")
        drift, drift_conf = read("goal_drift")
        selected_drift = (True if drift == "yes" else False if drift == "no" else None)
        confidence = max(action_conf, progress_conf, retry_conf, drift_conf)
        return SemanticAssessment(
            action, progress, retry, selected_drift, confidence,
            retry_confidence=retry_conf,
            goal_drift_confidence=drift_conf)

    @staticmethod
    def _digest(value: Any) -> str:
        body = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(body.encode("utf-8")).hexdigest()

    @staticmethod
    def _target_value(state: BehaviorState, target: str) -> str:
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

    @staticmethod
    def _set_target(state: BehaviorState, target: str, value: str) -> BehaviorState:
        if target == "mode":
            return replace(state, cognitive_mode=value)
        if target.startswith("framework:"):
            key = target.split(":", 1)[1]
            if key == "ward" and value != "mandatory":
                return state
            modes = dict(state.framework_modes)
            modes[key] = value
            return replace(state, framework_modes=modes)
        if target == "focus_criteria":
            return replace(state, focus_criteria=tuple(json.loads(value)))
        if target == "statework_preference":
            return state
        if target in {"verification_intensity", "exploration_budget", "retry_policy",
                      "recovery_level", "context_strategy", "response_strategy"}:
            prior = getattr(state, target)
            converted: Any = int(value) if isinstance(prior, int) else value
            return replace(state, **{target: converted})
        return state

    def _new_intervention(self, *, scope: str, revision: int, cause: str,
                          expected_effect: str,
                          targets: tuple[tuple[str, str, str], ...],
                          expiry: Optional[int] = None) -> BehaviorIntervention:
        intervention_id = "intv-" + self._digest({
            "scope": scope, "revision": revision, "cause": cause,
            "targets": targets,
        })[:24]
        records = tuple(InterventionTarget(
            target=target, previous_value=previous, applied_value=applied)
            for target, previous, applied in targets)
        return BehaviorIntervention(
            intervention_id=intervention_id, cause=cause,
            expected_effect=expected_effect, start_revision=revision,
            scope=scope, expires_after_actions=expiry or self.intervention_expiry_actions,
            actions_elapsed=0, targets=records)

    @staticmethod
    def _rank_stateworks(mode: str, options: tuple[StateWorkOption, ...],
                         active: tuple[str, ...]) -> tuple[str, ...]:
        terms = {
            "RECOVER": {"recovery", "recover", "repair", "diagnose"},
            "DIAGNOSE": {"diagnose", "diagnostic", "investigate"},
            "REINSPECT": {"reinspect", "reopen", "evidence"},
            "VERIFY": {"verify", "verification", "validation"},
            "READY_TO_COMPLETE": {"complete", "completion"},
            "DELIVER": {"deliver", "implementation", "build"},
            "REPLAN": {"plan", "replan", "decompose"},
        }.get(mode, set())
        scored = []
        for option in options:
            if option.statework_id in active:
                continue
            tags = {tag.lower() for tag in option.behavior_tags}
            score = len(tags & terms)
            if score:
                scored.append((score, option.statework_id))
        scored.sort(key=lambda pair: (-pair[0], pair[1]))
        return tuple(item[1] for item in scored[:4])

    def decide(self, context: FastLoopInput, *,
               judgment: Optional[_Judgment] = None) -> BehavioralDecision:
        """Return a DP decision. The caller must CAS and constrain its application."""
        original = context.state
        semantic = self._semantic(judgment)
        next_revision = original.revision + 1
        codes = {signal.code for signal in context.cfw_signals if signal.active}
        codes.update(context.active_trigger_codes)

        contradiction = bool(codes & {"operator_contradiction", "stale_assumption"})
        retry = (context.equivalent_retry_count >= self.retry_threshold
                 or (semantic.retry_relation == "equivalent_retry"
                     and semantic.retry_confidence >= self.semantic_threshold)
                 or "equivalent_retry" in codes)
        drift = (context.actions_since_goal_progress >= self.support_action_threshold
                 or "support_work_budget_exceeded" in codes
                 or (semantic.goal_drift is True
                     and semantic.goal_drift_confidence >= self.semantic_threshold)) \
                and not context.progress_made
        pressure = (context.context_pressure >= 0.85 or "context_pressure_high" in codes)

        triggers: set[str] = set()
        if contradiction:
            triggers.add("operator_contradiction" if "operator_contradiction" in codes
                         else "stale_assumption")
        if retry:
            triggers.add("equivalent_retry")
        if drift:
            triggers.add("goal_drift")
        if pressure:
            triggers.add("context_pressure")
        previously_failed = {
            item.cause for item in original.intervention_history
            if item.outcome in {"expired", "regressed"}
        }
        blocking_failed_causes = previously_failed & triggers

        interventions: list[BehaviorIntervention] = []
        outcomes: list[InterventionOutcome] = []
        directives: list[BehaviorDirective] = []
        expired_causes: set[str] = set()
        active_workflows = set(context.csw_snapshot.active_stateworks)
        working = original
        for item in original.active_interventions:
            elapsed = item.actions_elapsed + (1 if context.action_completed else 0)
            updated = replace(item, actions_elapsed=min(elapsed, item.expires_after_actions))
            committed_workflow = any(
                target.target.startswith("statework:")
                and target.target.split(":", 1)[1] in active_workflows
                for target in item.targets)
            if committed_workflow:
                outcomes.append(InterventionOutcome(
                    intervention_id=item.intervention_id, cause=item.cause,
                    outcome="improved", observed_revision=next_revision,
                    evidence_refs=context.evidence_refs))
                continue
            if context.regression_observed:
                outcomes.append(InterventionOutcome(
                    intervention_id=item.intervention_id, cause=item.cause,
                    outcome="regressed", observed_revision=next_revision,
                    evidence_refs=context.evidence_refs))
                expired_causes.add(item.cause)
                continue
            if context.progress_made and item.cause not in triggers:
                outcomes.append(InterventionOutcome(
                    intervention_id=item.intervention_id, cause=item.cause,
                    outcome="improved", observed_revision=next_revision,
                    evidence_refs=context.evidence_refs))
                continue
            if item.cause not in triggers:
                outcomes.append(InterventionOutcome(
                    intervention_id=item.intervention_id, cause=item.cause,
                    outcome="no_effect", observed_revision=next_revision,
                    evidence_refs=context.evidence_refs))
                continue
            if elapsed >= item.expires_after_actions:
                outcomes.append(InterventionOutcome(
                    intervention_id=item.intervention_id, cause=item.cause,
                    outcome="expired", observed_revision=next_revision,
                    evidence_refs=context.evidence_refs))
                expired_causes.add(item.cause)
                continue
            interventions.append(updated)

        # Restore the settings owned by a closed intervention, unless another
        # still-active intervention currently owns that same target.
        active_targets = {target.target for item in interventions for target in item.targets}
        closed_ids = {item.intervention_id for item in original.active_interventions} - {
            item.intervention_id for item in interventions}
        for closed in original.active_interventions:
            if closed.intervention_id not in closed_ids:
                continue
            for target in reversed(closed.targets):
                if target.target in active_targets or target.target.startswith("statework:"):
                    continue
                before = self._target_value(working, target.target)
                working = self._set_target(working, target.target, target.previous_value)
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
                    directive_id="dir-" + self._digest({
                        "intervention": closed.intervention_id,
                        "target": target.target, "value": target.previous_value,
                        "revision": next_revision})[:24],
                    operation=operation, target=directive_target,
                    value=target.previous_value, cause=cause,
                    expected_effect="Release a temporary behavior change after its trigger clears or expires.",
                    scope=context.scope, expires_after_actions=1,
                    intervention_id=closed.intervention_id))

        failed_intervention = bool(blocking_failed_causes or expired_causes & triggers)
        if failed_intervention:
            triggers.add("failed_cause_blocked")

        reason = "no behavioral change required"
        proposed: list[tuple[str, str, str, str]] = []
        if contradiction:
            cause = "operator_contradiction" if "operator_contradiction" in triggers else "stale_assumption"
            reason = "reinspect after contradictory or stale evidence"
            proposed = [("mode", "REINSPECT", cause, "Reopen the evidence base before continuing."),
                        ("framework:owl", "elevated", cause, "Recheck assumptions against current evidence."),
                        ("framework:anchor", "reopen", cause, "Reanchor the active task to current evidence.")]
        elif failed_intervention:
            reason = "stop repeating an expired or regressing intervention while its cause remains active"
            proposed = [("mode", "BLOCKED", "failed_cause_blocked",
                         "Require a fresh plan or operator input before retrying this behavioral change.")]
        elif retry or drift:
            cause = "equivalent_retry" if retry else "goal_drift"
            reason = ("require a materially different recovery attempt" if retry
                      else "restore effort to unfinished objective criteria")
            proposed = [("mode", "RECOVER", cause, "Resume criterion-directed progress without repeating the failed approach."),
                        ("framework:owl", "reset", cause, "Rebuild the hypothesis from current evidence."),
                        ("framework:anchor", "recovery", cause, "Reanchor work to an unfinished objective criterion."),
                        ("framework:fuse", "constrained", cause, "Limit support work until criterion-directed progress resumes."),
                        ("verification_intensity", "high", cause, "Check the next changed artifact against an unfinished criterion."),
                        ("exploration_budget", "low", cause, "Reduce exploratory work while the task is off track."),
                        ("retry_policy", "materially_different_only", cause, "Do not repeat an equivalent failed approach."),
                        ("recovery_level", str(min(3, working.recovery_level + 1)), cause,
                         "Make one bounded recovery attempt and reassess.")]
            if drift:
                proposed.append(("focus_criteria", json.dumps(list(context.objective_criteria),
                                                               separators=(",", ":")), cause,
                                 "Keep the next action bound to unfinished objective criteria."))
        elif pressure:
            cause = "context_pressure"
            reason = "narrow context around current objective and verified state"
            proposed = [("context_strategy", "criterion_focused", cause,
                         "Retain active criteria, current authoritative state, and the next action."),
                        ("exploration_budget", "low", cause,
                         "Reduce context growth until pressure returns to normal.")]
        elif context.progress_made:
            reason = "objective progress observed; release stale recovery constraints"
            proposed = [("mode", "VERIFY" if context.artifact_changed else "DELIVER",
                         "progress_observed", "Continue from the new objective progress."),
                        ("recovery_level", str(max(0, working.recovery_level - 1)),
                         "progress_observed", "Reduce recovery pressure after objective progress.")]

        existing_causes = {item.cause for item in interventions}
        grouped: dict[str, list[tuple[str, str, str, str]]] = {}
        for target, value, cause, effect in proposed:
            if target.startswith("framework:") and target.split(":", 1)[1] == "ward":
                continue
            previous = self._target_value(working, target)
            if previous == value:
                continue
            grouped.setdefault(cause, []).append((target, previous, value, effect))

        for cause, target_values in grouped.items():
            if cause in existing_causes:
                continue
            expected_effect = target_values[0][3]
            expiry = 1 if cause == "progress_observed" else self.intervention_expiry_actions
            intervention = self._new_intervention(
                scope=context.scope, revision=next_revision, cause=cause,
                expected_effect=expected_effect,
                targets=tuple((target, previous, value)
                              for target, previous, value, _effect in target_values),
                expiry=expiry)
            interventions.append(intervention)
            for target, _previous, value, effect in target_values:
                operation = ("set_framework" if target.startswith("framework:")
                             else "set_mode" if target == "mode" else "set_control")
                directive_target = target.split(":", 1)[1] if target.startswith("framework:") else target
                directives.append(BehaviorDirective(
                    directive_id="dir-" + self._digest({
                        "intervention": intervention.intervention_id,
                        "target": target, "value": value})[:24],
                    operation=operation, target=directive_target, value=value,
                    cause=cause, expected_effect=effect, scope=context.scope,
                    expires_after_actions=expiry,
                    intervention_id=intervention.intervention_id))
                working = self._set_target(working, target, value)

        working = replace(
            working,
            revision=next_revision,
            active_stateworks=context.csw_snapshot.active_stateworks,
            active_interventions=tuple(interventions[-16:]),
            intervention_history=tuple(
                (list(original.intervention_history) + outcomes)[-32:]),
            # WARD's hard floor is invariant in BehaviorState.
            framework_modes={**dict(working.framework_modes), "ward": "mandatory"},
        )

        ranked = self._rank_stateworks(
            working.cognitive_mode, context.csw_snapshot.eligible_stateworks,
            context.csw_snapshot.active_stateworks)
        preference = ranked[:1]
        if preference and not any(
                item.cause == "statework_fit" and any(
                    target.target == f"statework:{preference[0]}" for target in item.targets)
                for item in working.active_interventions):
            intervention = self._new_intervention(
                scope=context.scope, revision=next_revision, cause="statework_fit",
                expected_effect="Use the eligible StateWork that best fits the current behavioral mode; CSW validates and commits it.",
                targets=((f"statework:{preference[0]}", "unselected", "preferred"),),
                expiry=1)
            interventions = list(working.active_interventions) + [intervention]
            directive = BehaviorDirective(
                directive_id="dir-" + self._digest({
                    "intervention": intervention.intervention_id,
                    "statework": preference[0]})[:24],
                operation="prefer_statework", target=preference[0], value="preferred",
                cause="statework_fit", expected_effect=intervention.expected_effect,
                scope=context.scope, expires_after_actions=1,
                intervention_id=intervention.intervention_id)
            directives.append(directive)
            working = replace(working, active_interventions=tuple(interventions[-16:]))
        else:
            preference = ()

        decision_id = "decision-" + self._digest({
            "scope": context.scope, "revision": original.revision,
            "state": working.to_dict(), "directives": [item.to_dict() for item in directives],
        })[:24]
        return BehavioralDecision(
            decision_id=decision_id, scope=context.scope,
            expected_revision=original.revision, state=working,
            directives=tuple(directives), preferred_stateworks=tuple(preference),
            intervention_outcomes=tuple(outcomes), reason=reason)


__all__ = ["BehavioralController", "FastLoopInput", "SemanticAssessment"]
