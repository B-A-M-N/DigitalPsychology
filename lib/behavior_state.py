"""DigitalPsychology-owned behavioral state and decision wire contracts.

The runtime host persists and applies these values.  This module defines what
they mean; it has no dependency on CFW, CSW, or an MCP implementation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping


BEHAVIOR_MODES = frozenset({
    "DELIVER", "DIAGNOSE", "VERIFY", "RECOVER", "REPLAN", "BLOCKED",
    "REINSPECT", "READY_TO_COMPLETE",
})
FRAMEWORK_MODES = frozenset({
    "off", "passive", "normal", "elevated", "mandatory", "constrained",
    "reopen", "recovery", "reset",
})
INTERVENTION_OUTCOMES = frozenset({
    "improved", "no_effect", "regressed", "expired", "withdrawn",
})


def _text(value: Any, name: str, *, limit: int = 256) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{name} must be non-empty bounded text")
    return value


def _ids(values: Any, name: str, *, limit: int, item_limit: int = 128) -> tuple[str, ...]:
    if not isinstance(values, (tuple, list)) or len(values) > limit:
        raise ValueError(f"{name} must be a bounded sequence")
    result = tuple(_text(item, name, limit=item_limit) for item in values)
    if len(result) != len(set(result)):
        raise ValueError(f"{name} must not contain duplicates")
    return result


@dataclass(frozen=True)
class InterventionTarget:
    """One reversible setting change owned by an intervention."""

    target: str
    previous_value: str
    applied_value: str

    def __post_init__(self) -> None:
        _text(self.target, "intervention target", limit=128)
        _text(self.previous_value, "previous value", limit=2048)
        _text(self.applied_value, "applied value", limit=2048)

    def to_dict(self) -> dict[str, str]:
        return {"target": self.target, "previous_value": self.previous_value,
                "applied_value": self.applied_value}


@dataclass(frozen=True)
class BehaviorIntervention:
    """An active, bounded behavior change whose result must be observed."""

    intervention_id: str
    cause: str
    expected_effect: str
    start_revision: int
    scope: str
    expires_after_actions: int
    actions_elapsed: int
    targets: tuple[InterventionTarget, ...]

    def __post_init__(self) -> None:
        _text(self.intervention_id, "intervention_id", limit=128)
        _text(self.cause, "intervention cause", limit=128)
        _text(self.expected_effect, "expected effect", limit=512)
        _text(self.scope, "intervention scope", limit=256)
        if self.start_revision < 0 or self.expires_after_actions < 1:
            raise ValueError("intervention revision and expiry must be positive bounds")
        if not 0 <= self.actions_elapsed <= self.expires_after_actions:
            raise ValueError("intervention elapsed actions exceed its expiry")
        if not 1 <= len(self.targets) <= 12:
            raise ValueError("intervention must have one to twelve targets")
        if len({target.target for target in self.targets}) != len(self.targets):
            raise ValueError("intervention targets must be unique")

    def to_dict(self) -> dict[str, Any]:
        return {"intervention_id": self.intervention_id, "cause": self.cause,
                "expected_effect": self.expected_effect,
                "start_revision": self.start_revision, "scope": self.scope,
                "expires_after_actions": self.expires_after_actions,
                "actions_elapsed": self.actions_elapsed,
                "targets": [target.to_dict() for target in self.targets]}

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "BehaviorIntervention":
        data = dict(value)
        data["targets"] = tuple(InterventionTarget(**item) for item in data["targets"])
        return cls(**data)


@dataclass(frozen=True)
class InterventionOutcome:
    intervention_id: str
    cause: str
    outcome: str
    observed_revision: int
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _text(self.intervention_id, "intervention_id", limit=128)
        _text(self.cause, "intervention cause", limit=128)
        if self.outcome not in INTERVENTION_OUTCOMES:
            raise ValueError("unsupported intervention outcome")
        if self.observed_revision < 0:
            raise ValueError("observed revision must be non-negative")
        object.__setattr__(self, "evidence_refs",
                           _ids(self.evidence_refs, "evidence_refs", limit=16))

    def to_dict(self) -> dict[str, Any]:
        return {"intervention_id": self.intervention_id, "cause": self.cause,
                "outcome": self.outcome,
                "observed_revision": self.observed_revision,
                "evidence_refs": list(self.evidence_refs)}


@dataclass(frozen=True)
class BehaviorState:
    """DP's meaning of the current cognitive/behavioral configuration.

    ``active_stateworks`` is copied from an authoritative CSW snapshot.  A DP
    decision can rank eligible StateWorks, but cannot directly change this
    field or commit a CSW transition.
    """

    revision: int = 0
    cognitive_mode: str = "DELIVER"
    framework_modes: Mapping[str, str] = field(default_factory=lambda: {
        "owl": "normal", "anchor": "passive", "fuse": "normal",
        "ward": "mandatory", "flow": "off", "sispis": "normal",
        "dox": "off",
    })
    active_stateworks: tuple[str, ...] = ()
    focus_criteria: tuple[str, ...] = ()
    verification_intensity: str = "normal"
    exploration_budget: str = "normal"
    retry_policy: str = "normal"
    recovery_level: int = 0
    context_strategy: str = "standard"
    response_strategy: str = "direct"
    active_interventions: tuple[BehaviorIntervention, ...] = ()
    intervention_history: tuple[InterventionOutcome, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.revision, int) or self.revision < 0:
            raise ValueError("behavior revision must be a non-negative integer")
        if self.cognitive_mode not in BEHAVIOR_MODES:
            raise ValueError("unsupported cognitive mode")
        if not isinstance(self.framework_modes, Mapping) or len(self.framework_modes) > 32:
            raise ValueError("framework_modes must be a bounded mapping")
        modes: dict[str, str] = {}
        for name, mode in self.framework_modes.items():
            key = _text(name, "framework name", limit=64).lower()
            if not isinstance(mode, str) or mode not in FRAMEWORK_MODES:
                raise ValueError(f"unsupported mode for framework {key!r}")
            modes[key] = mode
        # WARD remains at its hard floor even when a caller supplies stale state.
        modes["ward"] = "mandatory"
        object.__setattr__(self, "framework_modes", MappingProxyType(modes))
        object.__setattr__(self, "active_stateworks",
                           _ids(self.active_stateworks, "active_stateworks", limit=16))
        object.__setattr__(self, "focus_criteria",
                           _ids(self.focus_criteria, "focus_criteria", limit=8))
        if self.verification_intensity not in {"passive", "normal", "high", "strict"}:
            raise ValueError("unsupported verification intensity")
        if self.exploration_budget not in {"disabled", "low", "normal", "bounded"}:
            raise ValueError("unsupported exploration budget")
        if self.retry_policy not in {"normal", "materially_different_only", "no_retry"}:
            raise ValueError("unsupported retry policy")
        if not isinstance(self.recovery_level, int) or not 0 <= self.recovery_level <= 3:
            raise ValueError("recovery level must be between zero and three")
        if self.context_strategy not in {"standard", "compact", "criterion_focused"}:
            raise ValueError("unsupported context strategy")
        if self.response_strategy not in {"direct", "detailed", "structured"}:
            raise ValueError("unsupported response strategy")
        if len(self.active_interventions) > 16:
            raise ValueError("too many active interventions")
        ids = [item.intervention_id for item in self.active_interventions]
        if len(ids) != len(set(ids)):
            raise ValueError("active intervention IDs must be unique")
        if len(self.intervention_history) > 32:
            raise ValueError("intervention history is bounded to 32 outcomes")

    def to_dict(self) -> dict[str, Any]:
        return {"revision": self.revision, "cognitive_mode": self.cognitive_mode,
                "framework_modes": dict(self.framework_modes),
                "active_stateworks": list(self.active_stateworks),
                "focus_criteria": list(self.focus_criteria),
                "verification_intensity": self.verification_intensity,
                "exploration_budget": self.exploration_budget,
                "retry_policy": self.retry_policy, "recovery_level": self.recovery_level,
                "context_strategy": self.context_strategy,
                "response_strategy": self.response_strategy,
                "active_interventions": [item.to_dict() for item in self.active_interventions],
                "intervention_history": [item.to_dict() for item in self.intervention_history]}

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "BehaviorState":
        data = dict(value)
        data["active_interventions"] = tuple(
            item if isinstance(item, BehaviorIntervention)
            else BehaviorIntervention.from_mapping(item)
            for item in data.get("active_interventions", ()))
        data["intervention_history"] = tuple(
            item if isinstance(item, InterventionOutcome)
            else InterventionOutcome(**item)
            for item in data.get("intervention_history", ()))
        for name in ("active_stateworks", "focus_criteria"):
            data[name] = tuple(data.get(name, ()))
        return cls(**data)


@dataclass(frozen=True)
class BehaviorDirective:
    directive_id: str
    operation: str
    target: str
    value: str
    cause: str
    expected_effect: str
    scope: str
    expires_after_actions: int
    intervention_id: str

    def __post_init__(self) -> None:
        for name in ("directive_id", "target", "cause", "scope", "intervention_id"):
            _text(getattr(self, name), name, limit=256 if name == "scope" else 128)
        _text(self.value, "directive value", limit=2048)
        _text(self.expected_effect, "expected effect", limit=512)
        if self.operation not in {"set_mode", "set_framework", "set_control",
                                  "prefer_statework"}:
            raise ValueError("unsupported behavior directive operation")
        if not 1 <= self.expires_after_actions <= 64:
            raise ValueError("directive expiry must be between one and 64 actions")
        if self.operation == "set_framework" and self.target.lower() == "ward" \
                and self.value != "mandatory":
            raise ValueError("behavior directives cannot lower the WARD hard floor")

    def to_dict(self) -> dict[str, Any]:
        return {"directive_id": self.directive_id, "operation": self.operation,
                "target": self.target, "value": self.value, "cause": self.cause,
                "expected_effect": self.expected_effect, "scope": self.scope,
                "expires_after_actions": self.expires_after_actions,
                "intervention_id": self.intervention_id}


@dataclass(frozen=True)
class StateWorkOption:
    """A StateWork CSW has already declared eligible for this identity/revision."""

    statework_id: str
    domain: str = ""
    behavior_tags: tuple[str, ...] = ()
    purpose: str = ""

    def __post_init__(self) -> None:
        _text(self.statework_id, "statework_id", limit=128)
        for name in ("domain", "purpose"):
            value = getattr(self, name)
            if not isinstance(value, str) or len(value) > (64 if name == "domain" else 256):
                raise ValueError(f"{name} must be bounded text")
        object.__setattr__(self, "behavior_tags",
                           _ids(self.behavior_tags, "behavior_tags", limit=16, item_limit=64))

    def to_dict(self) -> dict[str, Any]:
        return {"statework_id": self.statework_id, "domain": self.domain,
                "behavior_tags": list(self.behavior_tags), "purpose": self.purpose}


@dataclass(frozen=True)
class CSWBehaviorSnapshot:
    revision: int
    active_stateworks: tuple[str, ...] = ()
    eligible_stateworks: tuple[StateWorkOption, ...] = ()
    workflow_states: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.revision, int) or self.revision < 0:
            raise ValueError("CSW revision must be non-negative")
        object.__setattr__(self, "active_stateworks",
                           _ids(self.active_stateworks, "active_stateworks", limit=16))
        if len(self.eligible_stateworks) > 32:
            raise ValueError("CSW snapshot contains too many eligible StateWorks")
        ids = [item.statework_id for item in self.eligible_stateworks]
        if len(ids) != len(set(ids)):
            raise ValueError("eligible StateWork IDs must be unique")
        if not isinstance(self.workflow_states, Mapping) or len(self.workflow_states) > 16:
            raise ValueError("workflow_states must be bounded")
        states = {}
        for key, value in self.workflow_states.items():
            states[_text(key, "workflow ID", limit=128)] = _text(value, "workflow state", limit=64)
        object.__setattr__(self, "workflow_states", MappingProxyType(states))


@dataclass(frozen=True)
class FrameworkSignal:
    source: str
    code: str
    active: bool = True
    severity: str = "warning"
    criterion_id: str | None = None
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _text(self.source, "signal source", limit=64)
        _text(self.code, "signal code", limit=128)
        if self.severity not in {"info", "warning", "critical"}:
            raise ValueError("unsupported signal severity")
        if self.criterion_id is not None:
            _text(self.criterion_id, "criterion_id", limit=128)
        object.__setattr__(self, "evidence_refs",
                           _ids(self.evidence_refs, "evidence_refs", limit=8))


@dataclass(frozen=True)
class BehavioralDecision:
    decision_id: str
    scope: str
    expected_revision: int
    state: BehaviorState
    directives: tuple[BehaviorDirective, ...] = ()
    preferred_stateworks: tuple[str, ...] = ()
    intervention_outcomes: tuple[InterventionOutcome, ...] = ()
    reason: str = "no behavioral change required"

    def __post_init__(self) -> None:
        _text(self.decision_id, "decision_id", limit=128)
        _text(self.scope, "decision scope", limit=256)
        _text(self.reason, "decision reason", limit=512)
        if self.expected_revision < 0 or self.state.revision != self.expected_revision + 1:
            raise ValueError("decision must advance exactly one behavior revision")
        if len(self.directives) > 32 or len(self.preferred_stateworks) > 16:
            raise ValueError("behavior decision exceeds bounded output limits")
        object.__setattr__(self, "preferred_stateworks",
                           _ids(self.preferred_stateworks, "preferred_stateworks", limit=16))
        if len(self.intervention_outcomes) > 16:
            raise ValueError("too many intervention outcomes")

    def to_dict(self) -> dict[str, Any]:
        return {"decision_id": self.decision_id, "scope": self.scope,
                "expected_revision": self.expected_revision, "state": self.state.to_dict(),
                "directives": [item.to_dict() for item in self.directives],
                "preferred_stateworks": list(self.preferred_stateworks),
                "intervention_outcomes": [item.to_dict() for item in self.intervention_outcomes],
                "reason": self.reason}


__all__ = [
    "BEHAVIOR_MODES", "BehaviorIntervention", "BehaviorDirective", "BehaviorState",
    "BehavioralDecision", "CSWBehaviorSnapshot", "FrameworkSignal",
    "InterventionOutcome", "InterventionTarget", "StateWorkOption",
]
