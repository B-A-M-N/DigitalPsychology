"""Validated input snapshot consumed by the DP behavioral fast loop."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping

from ..behavior_state import (
    AttentionLease, BehavioralCause, BehaviorState, CSWBehaviorSnapshot,
    FrameworkSignal, GoalSetSnapshot, InterventionOutcome,
    group_behavioral_causes,
)
from ..compatibility import (
    MAX_AVAILABLE_FRAMEWORKS, MAX_CFW_SIGNALS,
    MAX_GOAL_CRITERIA,
)

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
    # Host-computed semantic query opportunity. This controls which soft
    # alternatives enter the candidate pool; the provider answer never does.
    semantic_opportunity_question_id: str | None = None
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
    # A host must declare connected CFW components. Absence means DP has no
    # framework actuators available; never assume the full CFW installation.
    available_frameworks: tuple[str, ...] = ()
    cfw_suppression_modes: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.scope, str) or not self.scope.strip() or len(self.scope) > 256:
            raise ValueError("fast-loop scope must be non-empty bounded text")
        if (len(self.objective_criteria) > MAX_GOAL_CRITERIA
                or len(set(self.objective_criteria)) != len(self.objective_criteria)):
            raise ValueError(
                f"objective criteria must be unique and bounded to {MAX_GOAL_CRITERIA}")
        if (self.semantic_opportunity_question_id is not None
                and self.semantic_opportunity_question_id not in {
                    "action_relation", "progress_relation", "retry_relation",
                    "goal_drift", "route_preference"}):
            raise ValueError("unsupported host semantic query opportunity")
        if any(not isinstance(value, str) or not value or len(value) > 128
               for value in self.objective_criteria):
            raise ValueError("objective criterion IDs must be bounded text")
        if (len(self.cfw_signals) > MAX_CFW_SIGNALS
                or len(self.active_trigger_codes) > MAX_CFW_SIGNALS):
            raise ValueError("fast-loop input exceeds bounded signal counts")
        supported_frameworks = {"owl", "anchor", "fuse", "ward", "flow", "sispis", "dox"}
        frameworks = tuple(self.available_frameworks)
        if (len(frameworks) > MAX_AVAILABLE_FRAMEWORKS
                or len(frameworks) != len(set(frameworks))
                or any(not isinstance(value, str) or value.lower() not in supported_frameworks
                       for value in frameworks)):
            raise ValueError("available CFW frameworks are malformed")
        object.__setattr__(self, "available_frameworks",
                           tuple(value.lower() for value in frameworks))
        floors = dict(self.cfw_suppression_modes or {})
        if (len(floors) > MAX_AVAILABLE_FRAMEWORKS or not set(floors).issubset(
                set(self.available_frameworks))):
            raise ValueError("CFW suppression floors must be bounded to selected components")
        normalized_floors = {}
        for component, modes in floors.items():
            if not isinstance(modes, (tuple, list)):
                raise ValueError("CFW suppression floor modes are malformed")
            values = tuple(modes)
            if (not isinstance(component, str) or component.lower() != component
                    or len(values) > 2
                    or any(mode not in {"off", "passive"} for mode in values)
                    or len(values) != len(set(values))):
                raise ValueError("CFW suppression floor modes are malformed")
            normalized_floors[component] = values
        object.__setattr__(self, "cfw_suppression_modes", normalized_floors)
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
        if (len(statuses) > MAX_GOAL_CRITERIA
                or not set(statuses).issubset(set(self.objective_criteria))):
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
        if (len(seen) > MAX_GOAL_CRITERIA
                or not set(seen).issubset(set(self.objective_criteria))):
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
        experimental_route = policy_context.get("experimental_route_preference")
        if experimental_route is not None and (
                not isinstance(experimental_route, str)
                or not experimental_route
                or len(experimental_route) > 128):
            raise ValueError(
                "experimental_route_preference must be bounded text or absent")
        try:
            encoded_policy_context = json.dumps(
                policy_context, sort_keys=True, separators=(",", ":"),
                ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("policy context must contain JSON values") from exc
        if len(encoded_policy_context.encode("utf-8")) > 8192:
            raise ValueError("policy context exceeds its byte bound")
        object.__setattr__(self, "policy_context", policy_context)

    @property
    def behavioral_causes(self) -> tuple[BehavioralCause, ...]:
        return group_behavioral_causes(self.cfw_signals)
