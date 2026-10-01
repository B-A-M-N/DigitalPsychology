"""Typed boundaries for the DigitalPsychology runtime host.

The runtime depends on ports supplied by CFW and CSW. Those repositories do
not import or inherit the DP host, and the host never modifies their objects
at runtime.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from typing import Any, Callable, Mapping, Protocol, Sequence

from ..behavior_state import (BehaviorDirective, BehaviorState,
                              CSWBehaviorSnapshot, FrameworkSignal)
from ..system_one_contracts import SystemOneFrame


@dataclass(frozen=True)
class RuntimeIdentity:
    namespace_id: str
    application_id: str
    application_instance_id: str
    agent_instance_id: str
    session_id: str
    task_id: str
    attempt_id: str
    workflow_id: str
    subject_ref: str
    segment_id: str
    agent_id: str
    behavioral_subject: str
    goal_contract_hash: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "namespace_id", "application_id", "application_instance_id",
            "agent_instance_id", "session_id", "task_id", "attempt_id",
            "workflow_id", "subject_ref", "segment_id", "agent_id",
            "behavioral_subject",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or len(value) > 512:
                raise ValueError(f"runtime identity {name} must be bounded text")
        if self.goal_contract_hash is not None and (
                not isinstance(self.goal_contract_hash, str)
                or len(self.goal_contract_hash) > 128):
            raise ValueError("goal contract hash must be bounded text")

    def to_dict(self) -> dict[str, str | None]:
        return {name: getattr(self, name) for name in (
            "namespace_id", "application_id", "application_instance_id",
            "agent_instance_id", "session_id", "task_id", "attempt_id",
            "workflow_id", "subject_ref", "segment_id", "agent_id",
            "behavioral_subject", "goal_contract_hash")}

    @property
    def scope(self) -> str:
        body = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return "dp-runtime-" + hashlib.sha256(body.encode("utf-8")).hexdigest()

    def csw_scope(self) -> dict[str, str]:
        return {name: str(getattr(self, name)) for name in (
            "namespace_id", "application_id", "application_instance_id",
            "workflow_id", "subject_ref")}


@dataclass(frozen=True)
class RuntimeObservation:
    """One host-produced action boundary and its authoritative evidence refs."""

    action_index: int
    action_completed: bool = True
    progress_made: bool = False
    regression_observed: bool = False
    artifact_changed: bool = False
    actions_since_goal_progress: int = 0
    equivalent_retry_count: int = 0
    context_pressure: float = 0.0
    criterion_status: Mapping[str, str] = field(default_factory=dict)
    evidence_refs: tuple[str, ...] = ()
    active_trigger_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.action_index, int) or self.action_index < 0:
            raise ValueError("host action index must be non-negative")
        if any(not isinstance(value, bool) for value in (
                self.action_completed, self.progress_made,
                self.regression_observed, self.artifact_changed)):
            raise ValueError("runtime observation flags must be boolean")
        if (self.actions_since_goal_progress < 0 or self.equivalent_retry_count < 0
                or not 0.0 <= float(self.context_pressure) <= 1.0):
            raise ValueError("runtime observation counters are out of bounds")
        statuses = dict(self.criterion_status)
        if len(statuses) > 8 or any(
                not isinstance(key, str) or not key or len(key) > 128
                or value not in {"unmet", "in_progress", "verified", "blocked", "unknown"}
                for key, value in statuses.items()):
            raise ValueError("host criterion statuses are malformed")
        object.__setattr__(self, "criterion_status", statuses)
        for name, limit in (("evidence_refs", 16), ("active_trigger_codes", 32)):
            values = getattr(self, name)
            if (not isinstance(values, (tuple, list)) or len(values) > limit
                    or len(values) != len(set(values))
                    or any(not isinstance(item, str) or not item or len(item) > 256
                           for item in values)):
                raise ValueError(f"{name} must be a bounded sequence of unique strings")
            object.__setattr__(self, name, tuple(values))


class CFWComponentPort(Protocol):
    """Explicit CFW capability boundary used by DP session orchestration."""

    def available_component_ids(self) -> Sequence[str]: ...

    def collect_behavior_signals(self, observation: RuntimeObservation
                                 ) -> Sequence[FrameworkSignal]: ...

    def prepare_behavior_directives(self, directives: Sequence[Mapping[str, Any]]) -> Any: ...

    def apply_behavior_directives(self, prepared: Any,
                                  behavior_state: Mapping[str, Any]) -> Sequence[str]: ...

    def synchronize_behavior_state(self, behavior_state: Mapping[str, Any]) -> None: ...

    def rollback_behavior_directives(self, prepared: Any) -> None: ...

    def render_behavior_context(self, context: str, *,
                                active_statework_ids: Sequence[str] = ()) -> str: ...

    def before_model_call(self, context: str, *, tool_capable: bool = False,
                          action_hint: Mapping[str, Any] | None = None,
                          active_statework_ids: Sequence[str] = ()) -> str: ...

    def before_tool_planning(self, context: str,
                             action_hint: Mapping[str, Any] | None = None,
                             active_statework_ids: Sequence[str] = ()) -> str: ...


class CSWWorkflowPort(Protocol):
    """CSW-owned workflow selection and transition operations."""

    def behavioral_snapshot(self, *, candidate_statework_ids: Sequence[str] | None = None
                            ) -> CSWBehaviorSnapshot: ...

    def activate_statework(self, statework_id: str, *,
                           expected_selection_revision: int,
                           candidate_statework_ids: Sequence[str]) -> CSWBehaviorSnapshot: ...

    def deactivate_statework(self, statework_id: str, *,
                             expected_selection_revision: int,
                             candidate_statework_ids: Sequence[str]) -> CSWBehaviorSnapshot: ...

    def apply_step(self, statework_id: str, step_id: str, *,
                   evidence_refs: Sequence[str], expected_workflow_revision: int) -> Any: ...


SystemOneFrameFactory = Callable[[RuntimeIdentity, str, Sequence[str], BehaviorState,
                                  RuntimeObservation, CSWBehaviorSnapshot,
                                  Sequence[FrameworkSignal]], SystemOneFrame]


class BehavioralDecisionService(Protocol):
    def judge(self, frame: SystemOneFrame, *, mode: str | None = None) -> Any: ...

    def decide_behavior(self, context: Any, *, judgment: Any = None) -> Mapping[str, Any]: ...

    def assess_intervention_outcome(self, intervention: Any, frame: Any, *,
                                    evidence_authority: Any) -> Mapping[str, Any]: ...

    def record_intervention_application(self, intervention: Any, directives: tuple,
                                        applied_directive_ids: tuple[str, ...], *,
                                        event_context: Any) -> str: ...

    def record_intervention_outcome(self, outcome: Any, *, event_context: Any) -> str: ...


__all__ = ["RuntimeIdentity", "RuntimeObservation", "CFWComponentPort",
           "CSWWorkflowPort", "BehavioralDecisionService"]
