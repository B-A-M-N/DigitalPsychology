"""Fast behavioral adaptation owned by DigitalPsychology.

The controller is a pure decision function.  A trusted runtime host supplies
CFW signals, a bounded authoritative CSW snapshot, outcomes, and an optional
DP System-1 judgment; the host persists the returned BehaviorState, checks its
revision, intersects directives with hard constraints, and applies them.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import hashlib
import json
from typing import Any, Mapping, Optional, Protocol

from .behavior_state import (
    AttentionLease, BehaviorIntervention, BehaviorState, BehaviorDirective,
    BehavioralDecision, CSWBehaviorSnapshot, FrameworkSignal, GoalSetSnapshot,
    InterventionOutcome,
    InterventionTarget, StateWorkOption,
)
from .condition_identity import identify_condition
from .control.policy_selector import (
    BehaviorCandidate, BehaviorPolicyConstraints, BehaviorPolicyPrior,
    BehaviorPolicySelector,
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
    goal_set: GoalSetSnapshot | None = None
    attention_lease: AttentionLease | None = None
    attention_last_seen: Mapping[str, int] = field(default_factory=dict)
    cfw_signals: tuple[FrameworkSignal, ...] = ()
    active_trigger_codes: tuple[str, ...] = ()
    actions_since_goal_progress: int = 0
    equivalent_retry_count: int = 0
    host_action_index: int = 0
    criterion_status: Mapping[str, str] = field(default_factory=dict)
    action_completed: bool = False
    pre_action: bool = False
    progress_made: bool = False
    regression_observed: bool = False
    artifact_changed: bool = False
    context_pressure: float = 0.0
    evidence_refs: tuple[str, ...] = ()
    intervention_assessments: tuple[InterventionOutcome, ...] = ()
    approach_epoch: str = ""
    policy_context: Mapping[str, Any] = field(default_factory=dict)
    available_frameworks: tuple[str, ...] = (
        "owl", "anchor", "fuse", "ward", "flow", "sispis", "dox")

    def __post_init__(self) -> None:
        if not isinstance(self.scope, str) or not self.scope.strip() or len(self.scope) > 256:
            raise ValueError("fast-loop scope must be non-empty bounded text")
        if len(self.objective_criteria) > 256 or len(set(self.objective_criteria)) != len(self.objective_criteria):
            raise ValueError("objective criteria must be unique and bounded to 256")
        if any(not isinstance(value, str) or not value or len(value) > 128
               for value in self.objective_criteria):
            raise ValueError("objective criterion IDs must be bounded text")
        if len(self.cfw_signals) > 32 or len(self.active_trigger_codes) > 32:
            raise ValueError("fast-loop input exceeds bounded signal counts")
        supported_frameworks = {"owl", "anchor", "fuse", "ward", "flow", "sispis", "dox"}
        frameworks = tuple(self.available_frameworks)
        if (len(frameworks) > 16 or len(frameworks) != len(set(frameworks))
                or any(not isinstance(value, str) or value.lower() not in supported_frameworks
                       for value in frameworks)):
            raise ValueError("available CFW frameworks are malformed")
        object.__setattr__(self, "available_frameworks",
                           tuple(value.lower() for value in frameworks))
        if (self.actions_since_goal_progress < 0 or self.equivalent_retry_count < 0
                or self.host_action_index < 0):
            raise ValueError("outcome counters must be non-negative")
        if not isinstance(self.pre_action, bool):
            raise ValueError("pre_action must be boolean")
        if not 0.0 <= float(self.context_pressure) <= 1.0:
            raise ValueError("context pressure must be between zero and one")
        if len(self.evidence_refs) > 16 or any(
                not isinstance(value, str) or not value or len(value) > 256
                for value in self.evidence_refs):
            raise ValueError("evidence references must be bounded")
        statuses = dict(self.criterion_status or {})
        if len(statuses) > 256 or not set(statuses).issubset(set(self.objective_criteria)):
            raise ValueError("criterion status must be bounded to objective criteria")
        if any(status not in {"unmet", "in_progress", "verified", "blocked", "unknown"}
               for status in statuses.values()):
            raise ValueError("unsupported host criterion status")
        object.__setattr__(self, "criterion_status", statuses)
        if self.goal_set is not None:
            if not isinstance(self.goal_set, GoalSetSnapshot):
                raise ValueError("fast-loop goal_set must be a validated host snapshot")
            if set(item.criterion_id for item in self.goal_set.items) != set(self.objective_criteria):
                raise ValueError("goal set must exactly cover the complete runtime objective")
        if self.attention_lease is not None:
            if not isinstance(self.attention_lease, AttentionLease):
                raise ValueError("attention lease must be validated")
            if not set(self.attention_lease.criterion_ids).issubset(set(self.objective_criteria)):
                raise ValueError("attention lease escapes the full objective set")
        seen = dict(self.attention_last_seen or {})
        if len(seen) > 256 or not set(seen).issubset(set(self.objective_criteria)):
            raise ValueError("attention history must be bounded to objective criteria")
        if any(type(key) is not str or type(value) is not int or value < 0
               for key, value in seen.items()):
            raise ValueError("attention history entries are malformed")
        object.__setattr__(self, "attention_last_seen", seen)
        if len(self.intervention_assessments) > 16:
            raise ValueError("intervention assessment count exceeds its bound")
        assessment_ids = [item.intervention_id for item in self.intervention_assessments]
        if len(assessment_ids) != len(set(assessment_ids)):
            raise ValueError("intervention assessments must be unique per decision")
        if any(item.scope != self.scope for item in self.intervention_assessments):
            raise ValueError("intervention assessments must match the decision scope")
        if not isinstance(self.approach_epoch, str) or len(self.approach_epoch) > 256:
            raise ValueError("approach epoch must be bounded text")
        policy_context = dict(self.policy_context or {})
        if len(policy_context) > 32:
            raise ValueError("policy context exceeds its field bound")
        try:
            encoded_policy_context = json.dumps(
                policy_context, sort_keys=True, separators=(",", ":"),
                ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("policy context must contain JSON values") from exc
        if len(encoded_policy_context.encode("utf-8")) > 8192:
            raise ValueError("policy context exceeds its byte bound")
        object.__setattr__(self, "policy_context", policy_context)


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

    def _new_intervention(self, *, scope: str, revision: int, cause: str,
                          expected_effect: str,
                          targets: tuple[tuple[str, str, str], ...],
                          expiry: Optional[int] = None,
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
        return BehaviorIntervention(
            intervention_id=intervention_id, cause=cause,
            expected_effect=expected_effect, start_revision=revision,
            scope=scope, expires_after_actions=expiry or self.intervention_expiry_actions,
            actions_elapsed=0, targets=records,
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
               judgment: Optional[_Judgment] = None,
               policy_priors: tuple[BehaviorPolicyPrior, ...] = ()) -> BehavioralDecision:
        """Return a DP decision. The caller must CAS and constrain its application."""
        original = context.state
        semantic = self._semantic(judgment)
        next_revision = original.revision + 1
        codes = {signal.code for signal in context.cfw_signals if signal.active}
        codes.update(context.active_trigger_codes)

        attention_ids = (context.attention_lease.criterion_ids
                         if context.attention_lease is not None
                         else original.focus_criteria)
        goal_items = ({item.criterion_id: item for item in context.goal_set.items}
                      if context.goal_set is not None else {})
        focused_items = [goal_items[item] for item in attention_ids if item in goal_items]
        multi_item_task = (context.goal_set is not None
                           and sum(item.required for item in context.goal_set.items) > 1)
        if context.goal_set is not None:
            focused_retry_count = max(
                (item.equivalent_retry_count for item in focused_items), default=0)
            focused_stall_count = max(
                (item.actions_since_progress for item in focused_items), default=0)
            focused_progress = any(
                item.last_progress_action == context.host_action_index
                for item in focused_items)
            runnable_count = len(context.goal_set.runnable_items)
            required_counts = context.goal_set.required_counts
            task_complete = (required_counts["total"] > 0
                             and required_counts["remaining_required"] == 0)
        else:
            focused_retry_count = context.equivalent_retry_count
            focused_stall_count = context.actions_since_goal_progress
            focused_progress = context.progress_made
            runnable_count = len(context.objective_criteria)
            task_complete = False

        scoped_retry_signal = any(
            signal.active and signal.code == "equivalent_retry"
            and (signal.criterion_id in attention_ids or not multi_item_task)
            for signal in context.cfw_signals)
        scoped_support_signal = any(
            signal.active and signal.code == "support_work_budget_exceeded"
            and (signal.criterion_id in attention_ids or not multi_item_task)
            for signal in context.cfw_signals)
        unscoped_retry = ("equivalent_retry" in context.active_trigger_codes
                          and not multi_item_task)
        unscoped_support = ("support_work_budget_exceeded" in context.active_trigger_codes
                            and not multi_item_task)

        contradiction = bool(codes & {"operator_contradiction", "stale_assumption"})
        retry = (focused_retry_count >= self.retry_threshold
                 or (semantic.retry_relation == "equivalent_retry"
                     and semantic.retry_confidence >= self.semantic_threshold)
                 or scoped_retry_signal or unscoped_retry)
        drift = (focused_stall_count >= self.support_action_threshold
                 or scoped_support_signal or unscoped_support
                 or (semantic.goal_drift is True
                     and semantic.goal_drift_confidence >= self.semantic_threshold)) \
                and not focused_progress
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

        # Restore the settings owned by a closed intervention, unless another
        # still-active intervention currently owns that same target.
        active_targets = {target.target for item in interventions for target in item.targets}
        next_focus = (context.attention_lease.criterion_ids
                      if context.attention_lease is not None else ())
        # Attention is host-owned. A closing intervention must not restore a
        # stale focus value while the current lease still owns focus_criteria.
        if context.attention_lease is not None:
            active_targets.add("focus_criteria")
        if next_focus != working.focus_criteria:
            active_targets.add("focus_criteria")
        closed_ids = {item.intervention_id for item in original.active_interventions} - {
            item.intervention_id for item in interventions}
        for closed in original.active_interventions:
            if closed.intervention_id not in closed_ids:
                continue
            assessed = next((item for item in outcomes
                             if item.intervention_id == closed.intervention_id), None)
            for target in reversed(closed.targets):
                if target.target in active_targets:
                    continue
                if target.target.startswith("statework:"):
                    if target.previous_value == "unselected":
                        statework_id = target.target.split(":", 1)[1]
                        if statework_id in working.active_stateworks:
                            directives.append(BehaviorDirective(
                                directive_id="dir-" + self._digest({
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

        failed_intervention = bool(
            blocking_failed_causes
            or any(cause in triggers and applies_to_attention(target_criteria)
                   and current_condition_ids.get(cause) == condition_id
                   for cause, target_criteria, condition_id in expired_causes))
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
        elif task_complete:
            reason = "all required host-owned criteria are verified"
            proposed = [("mode", "READY_TO_COMPLETE", "required_criteria_verified",
                         "The complete required task set is verified by host evidence.")]
        elif failed_intervention:
            if runnable_count > 0:
                reason = "yield this criterion after an ineffective recovery window and continue runnable work"
                proposed = [("mode", "REPLAN", "failed_criterion_yielded",
                             "Rotate to other runnable criteria after the bounded recovery window.")]
            else:
                reason = "stop because no required criterion remains runnable"
                proposed = [("mode", "BLOCKED", "failed_cause_blocked",
                             "Require an authority update or operator input before continuing.")]
        elif context.goal_set is not None and runnable_count == 0:
            reason = "no required criterion is currently runnable"
            proposed = [("mode", "BLOCKED", "no_runnable_required_criteria",
                         "Wait for the host to unblock or reprioritize required work.")]
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
                proposed.append(("focus_criteria", json.dumps(list(attention_ids),
                                                               separators=(",", ":")), cause,
                                 "Keep the next action bound to unfinished objective criteria."))
        elif pressure:
            cause = "context_pressure"
            reason = "narrow context around current objective and verified state"
            proposed = [("context_strategy", "criterion_focused", cause,
                         "Retain active criteria, current authoritative state, and the next action."),
                        ("exploration_budget", "low", cause,
                         "Reduce context growth until pressure returns to normal.")]
        elif focused_progress:
            reason = "objective progress observed; release stale recovery constraints"
            proposed = [("mode", "VERIFY" if context.artifact_changed else "DELIVER",
                         "progress_observed", "Continue from the new objective progress."),
                        ("recovery_level", str(max(0, working.recovery_level - 1)),
                         "progress_observed", "Reduce recovery pressure after objective progress.")]

        if next_focus != working.focus_criteria:
            lease_key = (context.attention_lease.lease_id[-16:]
                         if context.attention_lease is not None else "released")
            attention_cause = (proposed[0][2] if proposed
                               else f"attention_lease_{lease_key}")
            proposed = [(target, value, attention_cause, effect)
                        for target, value, _cause, effect in proposed
                        if target != "focus_criteria"]
            proposed.append((
                "focus_criteria", json.dumps(list(next_focus), separators=(",", ":")),
                attention_cause,
                "Give runnable required criteria a bounded, starvation-aware attention window."))

        # The deterministic controller proposes at most one policy change. The
        # selector compares it with continuation and only lets a validated,
        # independently evaluated receipt change that fallback choice.
        proposed = [item for item in proposed
                    if not item[0].startswith("framework:")
                    or item[0].split(":", 1)[1].lower() in context.available_frameworks]
        proposed = [item for item in proposed
                    if not (item[0] == "framework:ward" and item[1] != "mandatory")]
        current_preference = self._rank_stateworks(
            working.cognitive_mode, context.csw_snapshot.eligible_stateworks,
            context.csw_snapshot.active_stateworks)[:1]
        continuation = BehaviorCandidate.from_state(
            working, focus_criteria=next_focus,
            statework_preferences=current_preference,
            reason_codes=("continue_current",), cause="continue_current",
            expected_effect="Continue the current bounded behavior configuration.")
        candidates_by_id = {continuation.candidate_id: continuation}
        recommended = None
        if proposed:
            candidate_state = working
            for target, value, _cause, _effect in proposed:
                candidate_state = self._set_target(candidate_state, target, value)
            recommended = BehaviorCandidate.from_state(
                candidate_state, focus_criteria=next_focus,
                statework_preferences=self._rank_stateworks(
                    candidate_state.cognitive_mode,
                    context.csw_snapshot.eligible_stateworks,
                    context.csw_snapshot.active_stateworks)[:1],
                reason_codes=("deterministic_rule", *sorted(triggers)),
                cause=proposed[0][2], expected_effect=proposed[0][3])
            candidates_by_id[recommended.candidate_id] = recommended

        required_mode = None
        if contradiction:
            required_mode = "REINSPECT"
        elif task_complete:
            required_mode = "READY_TO_COMPLETE"
        elif failed_intervention:
            required_mode = "REPLAN" if runnable_count > 0 else "BLOCKED"
        elif context.goal_set is not None and runnable_count == 0:
            required_mode = "BLOCKED"
        selection = self.policy_selector.select(
            candidates_by_id.values(),
            constraints=BehaviorPolicyConstraints(
                current_state=working,
                available_frameworks=context.available_frameworks,
                goal_set=context.goal_set,
                required_mode=required_mode),
            csw_snapshot=context.csw_snapshot,
            goal_set=context.goal_set,
            policy_context=context.policy_context,
            priors=policy_priors)
        selected_candidate = selection.candidate
        if (recommended is not None
                and selected_candidate.candidate_id != recommended.candidate_id):
            # Drop the unselected policy changes while keeping the host-owned
            # attention lease update. Directive generation below remains the
            # single path for intervention identity and rollback targets.
            proposed = [item for item in proposed if item[0] == "focus_criteria"]
            reason = ("validated behavior evidence favors continuing the current policy"
                      if selection.score.evidence_source.startswith("validated_receipt:")
                      else "continue current policy; deterministic recommendation was constrained")
        elif recommended is not None:
            reason = reason + f" (selected {selected_candidate.candidate_id})"

        existing_condition_ids = ({item.condition_id for item in interventions
                                   if applies_to_attention(item.target_criteria)}
                                  | {current_condition_ids[cause]
                                     for cause in previously_effective & triggers})
        grouped: dict[str, list[tuple[str, str, str, str]]] = {}
        for target, value, cause, effect in proposed:
            if (target.startswith("framework:")
                    and target.split(":", 1)[1].lower()
                    not in context.available_frameworks):
                continue
            if target.startswith("framework:") and target.split(":", 1)[1] == "ward":
                continue
            previous = self._target_value(working, target)
            if previous == value:
                continue
            grouped.setdefault(cause, []).append((target, previous, value, effect))

        for cause, target_values in grouped.items():
            target_criteria = tuple(next_focus or attention_ids)[:8]
            condition = condition_for(cause, target_criteria)
            if condition.condition_id in existing_condition_ids:
                continue
            expected_effect = target_values[0][3]
            expiry = 1 if cause == "progress_observed" else self.intervention_expiry_actions
            intervention = self._new_intervention(
                scope=context.scope, revision=next_revision, cause=cause,
                expected_effect=expected_effect,
                targets=tuple((target, previous, value)
                              for target, previous, value, _effect in target_values),
                expiry=expiry,
                target_criteria=tuple(next_focus or attention_ids),
                baseline_criterion_status=context.criterion_status,
                baseline_action_index=max(
                    0, context.host_action_index - (1 if context.pre_action else 0)),
                concurrent_intervention_ids=tuple(
                    item.intervention_id for item in interventions),
                baseline_goal_set=context.goal_set,
                baseline_attention_last_seen=context.attention_last_seen,
                condition_id=condition.condition_id,
                condition_basis_digest=condition.condition_basis_digest)
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
            focus_criteria=next_focus,
            attention_lease=context.attention_lease,
            attention_last_seen=context.attention_last_seen,
            active_stateworks=context.csw_snapshot.active_stateworks,
            active_interventions=tuple(interventions[-16:]),
            intervention_history=tuple(
                (list(original.intervention_history) + outcomes)[-32:]),
            # WARD's hard floor is invariant in BehaviorState.
            framework_modes={**dict(working.framework_modes), "ward": "mandatory"},
        )

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
                updated = replace(
                    intervention,
                    targets=intervention.targets + (InterventionTarget(
                        target=f"statework:{preference[0]}",
                        previous_value="unselected", applied_value="preferred"),))
                interventions = [updated if item.intervention_id == updated.intervention_id
                                 else item for item in working.active_interventions]
                working = replace(working, active_interventions=tuple(interventions[-16:]))
            elif working.active_interventions:
                # Do not overlap a new workflow-selection intervention with a
                # still-evaluating behavior change. Reconsider after its
                # evidence window closes.
                preference = ()
            else:
                condition = condition_for(
                    "statework_fit", tuple(next_focus or attention_ids)[:8])
                intervention = self._new_intervention(
                    scope=context.scope, revision=next_revision, cause="statework_fit",
                    expected_effect="Use the eligible StateWork that best fits the current behavioral mode; CSW validates and commits it.",
                    targets=((f"statework:{preference[0]}", "unselected", "preferred"),),
                    expiry=1,
                    target_criteria=tuple(next_focus or attention_ids),
                    baseline_criterion_status=context.criterion_status,
                    baseline_action_index=max(
                        0, context.host_action_index - (1 if context.pre_action else 0)),
                    concurrent_intervention_ids=tuple(
                        item.intervention_id for item in interventions),
                    baseline_goal_set=context.goal_set,
                    baseline_attention_last_seen=context.attention_last_seen,
                    condition_id=condition.condition_id,
                    condition_basis_digest=condition.condition_basis_digest)
                interventions = list(working.active_interventions) + [intervention]
            if preference:
                directive = BehaviorDirective(
                    directive_id="dir-" + self._digest({
                        "intervention": intervention.intervention_id,
                        "statework": preference[0]})[:24],
                    operation="prefer_statework", target=preference[0], value="preferred",
                    cause=intervention.cause, expected_effect=intervention.expected_effect,
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
