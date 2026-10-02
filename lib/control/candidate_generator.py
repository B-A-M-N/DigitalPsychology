"""Generate a small set of plausible, bounded behavior configurations."""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
from types import MappingProxyType
from typing import Mapping

from ..behavior_state import BehaviorState, CSWBehaviorSnapshot
from .policy_selector import (BehaviorCandidate, SemanticCandidateEvidence)
from .policy_ambiguity import PolicyAmbiguity
from .statework_selector import rank_stateworks


MAX_CANDIDATES = 8
_CONTROL_NAMES = (
    "verification_intensity", "exploration_budget", "retry_policy",
    "recovery_level", "context_strategy", "response_strategy",
)


@dataclass(frozen=True)
class CandidateConditions:
    contradiction: bool = False
    task_complete: bool = False
    failed_intervention: bool = False
    no_runnable_work: bool = False
    retry: bool = False
    retry_ambiguity: bool = False
    drift: bool = False
    action_relation_uncertain: bool = False
    progress_relation_uncertain: bool = False
    pressure: bool = False
    focused_progress: bool = False
    artifact_changed: bool = False
    trigger_codes: tuple[str, ...] = ()
    contradiction_cause: str = "operator_contradiction"


@dataclass(frozen=True)
class CandidateOption:
    candidate: BehaviorCandidate
    role: str
    changes: tuple[tuple[str, str, str, str], ...]
    deterministic_support: float


@dataclass(frozen=True)
class CandidatePool:
    options: tuple[CandidateOption, ...]
    fallback_candidate_id: str | None
    semantic_evidence: Mapping[str, SemanticCandidateEvidence]

    def __post_init__(self) -> None:
        if not 1 <= len(self.options) <= MAX_CANDIDATES:
            raise ValueError("candidate pool must contain one to eight options")
        ids = tuple(item.candidate.candidate_id for item in self.options)
        if len(ids) != len(set(ids)):
            raise ValueError("candidate pool contains duplicate policy configurations")
        if (self.fallback_candidate_id is not None
                and self.fallback_candidate_id not in ids):
            raise ValueError("candidate fallback must refer to a generated option")
        object.__setattr__(self, "semantic_evidence",
                           MappingProxyType(dict(self.semantic_evidence)))

    def option_for(self, candidate_id: str) -> CandidateOption | None:
        return next((item for item in self.options
                     if item.candidate.candidate_id == candidate_id), None)

    def fallback(self) -> CandidateOption | None:
        return self.option_for(self.fallback_candidate_id) if self.fallback_candidate_id else None

    def ambiguity(self, unresolved_dimensions: tuple[str, ...]) -> PolicyAmbiguity | None:
        if len(self.options) < 2 or not unresolved_dimensions:
            return None
        ranked = sorted(self.options, key=lambda item: (
            item.deterministic_support, item.candidate.candidate_id), reverse=True)
        return PolicyAmbiguity(
            top_candidate_id=ranked[0].candidate.candidate_id,
            second_candidate_id=ranked[1].candidate.candidate_id,
            score_gap=abs(ranked[0].deterministic_support
                          - ranked[1].deterministic_support),
            unresolved_dimensions=unresolved_dimensions)


class BehaviorCandidateGenerator:
    """Generate only policies made plausible by current host conditions."""

    def generate(self, state: BehaviorState, *,
                 next_focus: tuple[str, ...],
                 conditions: CandidateConditions,
                 csw_snapshot: CSWBehaviorSnapshot,
                 available_frameworks: tuple[str, ...],
                 semantic_action_relation: str | None = None,
                 semantic_progress_relation: str | None = None,
                 semantic_retry_relation: str | None = None,
                 semantic_goal_drift: bool | None = None,
                 semantic_statework_preference: str | None = None,
                 semantic_statework_confidence: float = 0.0,
                 semantic_action_confidence: float = 0.0,
                 semantic_progress_confidence: float = 0.0,
                 semantic_retry_confidence: float = 0.0,
                 semantic_drift_confidence: float = 0.0) -> CandidatePool:
        options: list[CandidateOption] = []
        framework_allow = set(available_frameworks)

        def add(role: str, *, mode: str | None = None,
                framework_changes: Mapping[str, str] | None = None,
                control_changes: Mapping[str, object] | None = None,
                cause: str, effect: str, deterministic: bool = False,
                fallback: bool = False) -> CandidateOption:
            frameworks = dict(state.framework_modes)
            for name, value in (framework_changes or {}).items():
                if name.lower() in framework_allow and name.lower() != "ward":
                    frameworks[name.lower()] = value
            frameworks["ward"] = "mandatory"
            controls = {name: getattr(state, name) for name in _CONTROL_NAMES}
            controls.update(control_changes or {})
            next_state = replace(
                state,
                cognitive_mode=mode or state.cognitive_mode,
                mode_lease=(state.mode_lease
                            if mode is None or mode == state.cognitive_mode else None),
                framework_modes=frameworks,
                focus_criteria=next_focus,
                attention_lease=None,
                **controls,
            )
            preferences = rank_stateworks(
                next_state.cognitive_mode, csw_snapshot.eligible_stateworks,
                csw_snapshot.active_stateworks,
                semantic_preference=semantic_statework_preference,
                semantic_confidence=semantic_statework_confidence,
            )[:1]
            reason_codes = (
                (("deterministic_rule",) + conditions.trigger_codes) if deterministic
                else ("abstention_fallback",) if fallback
                else ("continue_current",) if role == "CONTINUE"
                else ("semantic_candidate",) if role in {
                    "DIAGNOSE", "REINSPECT", "REPLAN", "VERIFY"} else ("plausible_alternative",)
            )
            candidate = BehaviorCandidate.from_state(
                next_state, focus_criteria=next_focus,
                statework_preferences=preferences,
                reason_codes=reason_codes,
                cause=cause, expected_effect=effect)
            changes = _changes(state, next_state, cause=cause, effect=effect)
            option = CandidateOption(
                candidate=candidate, role=role, changes=changes,
                deterministic_support=(0.86 if deterministic else
                                       0.40 if role == "CONTINUE" else 0.30))
            if all(item.candidate.candidate_id != candidate.candidate_id for item in options):
                options.append(option)
            elif deterministic:
                prior = next(item for item in options
                             if item.candidate.candidate_id == candidate.candidate_id)
                index = options.index(prior)
                options[index] = replace(
                    prior, candidate=replace_candidate_reasons(
                        prior.candidate, reason_codes=reason_codes),
                    deterministic_support=0.86)
            return next(item for item in options
                        if item.candidate.candidate_id == candidate.candidate_id)

        continuation = add(
            "CONTINUE", cause="continue_current",
            effect="Continue the current bounded behavior configuration.")
        fallback_id: str | None = None

        if conditions.contradiction:
            fallback = add(
                "REINSPECT", mode="REINSPECT",
                framework_changes={"owl": "elevated", "anchor": "reopen"},
                cause=conditions.contradiction_cause,
                effect="Reopen the evidence base before continuing.", deterministic=True)
            fallback_id = fallback.candidate.candidate_id
        elif conditions.task_complete:
            add("READY_TO_COMPLETE", mode="READY_TO_COMPLETE",
                cause="required_criteria_verified",
                effect="Complete only after all required host criteria are verified.",
                deterministic=True)
        elif conditions.failed_intervention:
            mode = "REPLAN" if not conditions.no_runnable_work else "BLOCKED"
            add(mode, mode=mode,
                framework_changes={"owl": "reset", "anchor": "recovery"},
                cause="failed_criterion_yielded",
                effect="Yield an ineffective approach and preserve remaining runnable work.",
                deterministic=True)
        elif conditions.no_runnable_work:
            add("BLOCKED", mode="BLOCKED", cause="no_runnable_required_criteria",
                effect="Wait for the host to unblock or reprioritize required work.",
                deterministic=True)
        elif (conditions.retry or conditions.retry_ambiguity or conditions.drift
              or conditions.action_relation_uncertain
              or conditions.progress_relation_uncertain
              or semantic_action_relation == "unrelated"):
            cause = ("equivalent_retry" if conditions.retry else
                     "goal_drift" if conditions.drift else "policy_ambiguity")
            recovery = add(
                "RECOVER", mode="RECOVER",
                framework_changes={"owl": "reset", "anchor": "recovery",
                                   "fuse": "constrained"},
                control_changes={
                    "verification_intensity": "high",
                    "exploration_budget": "low",
                    "retry_policy": "materially_different_only",
                    "recovery_level": min(3, state.recovery_level + 1),
                }, cause=cause,
                effect="Make one bounded, criterion-directed recovery attempt and reassess.",
                deterministic=bool(conditions.retry or conditions.drift))
            diagnostic = add(
                "DIAGNOSE", mode="DIAGNOSE",
                framework_changes={"owl": "elevated", "anchor": "recovery",
                                   "fuse": "constrained"},
                control_changes={"verification_intensity": "high",
                                 "exploration_budget": "low",
                                 "retry_policy": "materially_different_only"},
                cause="policy_ambiguity",
                effect="Gather discriminating evidence before choosing a recovery path.",
                fallback=True)
            fallback_id = diagnostic.candidate.candidate_id
            if conditions.drift:
                add("REPLAN", mode="REPLAN",
                    framework_changes={"owl": "reset", "anchor": "recovery",
                                       "fuse": "constrained"},
                    cause="goal_drift",
                    effect="Replan remaining runnable criteria after evidence of drift.")
        elif conditions.pressure:
            add("CONTEXT_FOCUSED", control_changes={
                "context_strategy": "criterion_focused",
                "exploration_budget": "low",
            }, cause="context_pressure",
                effect="Narrow context around active criteria and verified state.",
                deterministic=True)
        elif conditions.focused_progress:
            if conditions.artifact_changed:
                add("VERIFY", mode="VERIFY",
                    control_changes={"verification_intensity": "high"},
                    cause="progress_observed",
                    effect="Verify the changed artifact against the active criterion.",
                    deterministic=True)
            else:
                add("DELIVER", mode="DELIVER",
                    control_changes={"recovery_level": max(0, state.recovery_level - 1)},
                    cause="progress_observed",
                    effect="Continue criterion-directed work after observed progress.",
                    deterministic=True)
        elif state.cognitive_mode == "BLOCKED" and not conditions.no_runnable_work:
            add("REPLAN", mode="REPLAN", cause="runnable_work_resumed",
                effect="Resume planning because required work is runnable.",
                deterministic=True)

        if not conditions.contradiction and not conditions.task_complete \
                and not conditions.no_runnable_work:
            roles = {item.role for item in options}
            if semantic_action_relation == "required_verification" and "VERIFY" not in roles:
                add("VERIFY", mode="VERIFY",
                    control_changes={"verification_intensity": "high"},
                    cause="semantic_verification_support",
                    effect="Verify the proposed result before relying on it.")
            if (semantic_action_relation == "diagnostic_support"
                    or semantic_progress_relation in {
                        "diagnostic_progress", "no_progress"}
                    or semantic_goal_drift is True) and "DIAGNOSE" not in roles:
                diagnostic = add(
                    "DIAGNOSE", mode="DIAGNOSE",
                    framework_changes={"owl": "elevated", "anchor": "recovery",
                                       "fuse": "constrained"},
                    control_changes={"verification_intensity": "high",
                                     "exploration_budget": "low"},
                    cause="semantic_policy_ambiguity",
                    effect="Gather evidence that distinguishes the remaining legal policies.",
                    fallback=True)
                if fallback_id is None:
                    fallback_id = diagnostic.candidate.candidate_id
            if (semantic_retry_relation == "equivalent_retry"
                    and "RECOVER" not in roles):
                add("RECOVER", mode="RECOVER",
                    framework_changes={"owl": "reset", "anchor": "recovery",
                                       "fuse": "constrained"},
                    control_changes={"verification_intensity": "high",
                                     "exploration_budget": "low",
                                     "retry_policy": "materially_different_only",
                                     "recovery_level": min(3, state.recovery_level + 1)},
                    cause="semantic_equivalent_retry",
                    effect="Avoid repeating an approach judged equivalent to a failed attempt.")
            if semantic_goal_drift is True and "REPLAN" not in roles:
                add("REPLAN", mode="REPLAN",
                    framework_changes={"owl": "reset", "anchor": "recovery",
                                       "fuse": "constrained"},
                    cause="semantic_goal_drift",
                    effect="Replan toward the remaining required criteria.")

        # A pre-action semantic result is advisory: it changes ranking among
        # these legal configurations while CFW retains final action authority.
        semantic_evidence = _semantic_support(
            options, action_relation=semantic_action_relation,
            progress_relation=semantic_progress_relation,
            retry_relation=semantic_retry_relation,
            goal_drift=semantic_goal_drift,
            action_confidence=semantic_action_confidence,
            progress_confidence=semantic_progress_confidence,
            retry_confidence=semantic_retry_confidence,
            drift_confidence=semantic_drift_confidence)
        if len(options) > MAX_CANDIDATES:
            options = options[:MAX_CANDIDATES]
        if fallback_id is not None and all(
                item.candidate.candidate_id != fallback_id for item in options):
            fallback_id = None
        return CandidatePool(tuple(options), fallback_id, semantic_evidence)


def _changes(before: BehaviorState, after: BehaviorState, *, cause: str,
             effect: str) -> tuple[tuple[str, str, str, str], ...]:
    changes: list[tuple[str, str, str, str]] = []
    if before.cognitive_mode != after.cognitive_mode:
        changes.append(("mode", after.cognitive_mode, cause, effect))
    for name, value in sorted(after.framework_modes.items()):
        if name == "ward" or name not in after.framework_modes:
            continue
        if before.framework_modes.get(name) != value:
            changes.append((f"framework:{name}", value, cause, effect))
    for name in _CONTROL_NAMES:
        value = getattr(after, name)
        if getattr(before, name) != value:
            changes.append((name, str(value), cause, effect))
    if before.focus_criteria != after.focus_criteria:
        changes.append(("focus_criteria", json.dumps(
            list(after.focus_criteria), separators=(",", ":")), cause, effect))
    return tuple(changes)


def _semantic_support(options: list[CandidateOption], *,
                      action_relation: str | None,
                      progress_relation: str | None,
                      retry_relation: str | None,
                      goal_drift: bool | None,
                      action_confidence: float,
                      progress_confidence: float,
                      retry_confidence: float,
                      drift_confidence: float) -> Mapping[str, SemanticCandidateEvidence]:
    result: dict[str, SemanticCandidateEvidence] = {}
    for option in options:
        role = option.role
        alignment: float | None = None
        retry_risk: float | None = None
        drift_risk: float | None = None
        if action_relation is not None and action_confidence >= 0.5:
            if action_relation in {"advances_primary_goal", "removes_concrete_blocker"}:
                alignment = action_confidence if role in {"DELIVER", "CONTINUE", "RECOVER"} else 1.0 - action_confidence
            elif action_relation == "required_verification":
                alignment = action_confidence if role == "VERIFY" else 1.0 - action_confidence
            elif action_relation == "diagnostic_support":
                alignment = action_confidence if role in {"DIAGNOSE", "REINSPECT"} else 1.0 - action_confidence
            elif action_relation == "unrelated":
                alignment = action_confidence if role in {"DIAGNOSE", "REINSPECT", "REPLAN"} else 1.0 - action_confidence
        if progress_relation is not None and progress_confidence >= 0.5:
            if progress_relation in {"goal_progress", "blocker_removed"}:
                progress_alignment = progress_confidence if role in {"DELIVER", "VERIFY"} else 1.0 - progress_confidence
            elif progress_relation == "verification_progress":
                progress_alignment = progress_confidence if role == "VERIFY" else 1.0 - progress_confidence
            elif progress_relation in {"diagnostic_progress", "recovery_progress"}:
                progress_alignment = progress_confidence if role in {"DIAGNOSE", "RECOVER"} else 1.0 - progress_confidence
            else:
                progress_alignment = progress_confidence if role in {"DIAGNOSE", "REPLAN", "REINSPECT"} else 1.0 - progress_confidence
            alignment = progress_alignment if alignment is None else (alignment + progress_alignment) / 2
        if retry_relation is not None and retry_confidence >= 0.5:
            retry_risk = (1.0 - retry_confidence
                          if role in {"RECOVER", "DIAGNOSE", "REPLAN", "REINSPECT"}
                          else retry_confidence)
        if goal_drift is not None and drift_confidence >= 0.5:
            drift_risk = (1.0 - drift_confidence
                          if goal_drift and role in {"DIAGNOSE", "REPLAN", "REINSPECT"}
                          else drift_confidence if goal_drift
                          else 1.0 - drift_confidence)
        evidence = SemanticCandidateEvidence(
            candidate_id=option.candidate.candidate_id,
            goal_alignment_support=alignment,
            retry_risk=retry_risk,
            drift_risk=drift_risk)
        if evidence.support_score is not None:
            result[evidence.candidate_id] = evidence
    return result


def replace_candidate_reasons(candidate: BehaviorCandidate, *,
                              reason_codes: tuple[str, ...]) -> BehaviorCandidate:
    return BehaviorCandidate(
        candidate_id=candidate.candidate_id,
        cognitive_mode=candidate.cognitive_mode,
        framework_modes=candidate.framework_modes,
        controls=candidate.controls,
        focus_criteria=candidate.focus_criteria,
        statework_preferences=candidate.statework_preferences,
        reason_codes=reason_codes,
        focus_scope=candidate.focus_scope,
        cause=candidate.cause,
        expected_effect=candidate.expected_effect)


__all__ = [
    "MAX_CANDIDATES", "BehaviorCandidateGenerator", "CandidateConditions",
    "CandidateOption", "CandidatePool", "rank_stateworks",
]
