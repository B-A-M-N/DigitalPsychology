"""Narrow, versioned CFW actuation projection.

CFW needs enough DP state to actuate its components. It does not need DP's
private bookkeeping, and coupling it to the whole ``BehaviorState`` means every
DP schema change can break the CFW port without either suite noticing. This
module defines the contract CFW actually consumes.

What is deliberately absent: ``mode_lease``, ``attention_lease``,
``attention_last_seen``, ``active_stateworks``, ``intervention_history`` and the
full intervention records. Those are DP's, and they are not actuation inputs.

The projection carries an explicit ``protocol_version`` so an incompatible host
and framework pair fail at startup rather than during the first intervention.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

CFW_BEHAVIOR_PROJECTION_VERSION = "2.0.0"

_MAX_FOCUS = 8
_MAX_LEASES = 16


@dataclass(frozen=True)
class CFWTargetLease:
    """One bounded, expiring control value CFW must hold for DP."""

    intervention_id: str
    target: str
    previous_value: str
    applied_value: str
    expires_at_action: int
    source_revision: int

    def __post_init__(self) -> None:
        for name in ("intervention_id", "target", "previous_value", "applied_value"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value or len(value) > 512:
                raise ValueError(f"CFW target lease {name} must be bounded text")
        for name in ("expires_at_action", "source_revision"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"CFW target lease {name} must be a non-negative integer")

    def to_dict(self) -> dict[str, Any]:
        return {"intervention_id": self.intervention_id, "target": self.target,
                "previous_value": self.previous_value,
                "applied_value": self.applied_value,
                "expires_at_action": self.expires_at_action,
                "source_revision": self.source_revision}


@dataclass(frozen=True)
class CFWBehaviorProjection:
    """Exactly the DP state CFW is allowed to see, and nothing more."""

    behavior_revision: int
    cognitive_mode: str
    framework_modes: Mapping[str, str]
    focus_criteria: tuple[str, ...]
    verification_intensity: str
    exploration_budget: str
    retry_policy: str
    recovery_level: int
    context_strategy: str
    response_strategy: str
    target_leases: tuple[CFWTargetLease, ...] = ()
    protocol_version: str = CFW_BEHAVIOR_PROJECTION_VERSION

    def __post_init__(self) -> None:
        if type(self.behavior_revision) is not int or self.behavior_revision < 0:
            raise ValueError("CFW behavior revision must be a non-negative integer")
        if not isinstance(self.cognitive_mode, str) or not self.cognitive_mode:
            raise ValueError("CFW projection requires a cognitive mode")
        if (not isinstance(self.framework_modes, Mapping)
                or not self.framework_modes
                or len(self.framework_modes) > 16
                or any(not isinstance(key, str) or not key
                       or not isinstance(value, str) or not value
                       for key, value in self.framework_modes.items())):
            raise ValueError("CFW projection framework modes are malformed")
        focus = tuple(self.focus_criteria)
        if (not isinstance(self.focus_criteria, (tuple, list)) or len(focus) > _MAX_FOCUS
                or any(not isinstance(item, str) or not item or len(item) > 128
                       for item in focus)):
            raise ValueError("CFW projection focus criteria are malformed")
        for name in ("verification_intensity", "exploration_budget", "retry_policy",
                     "context_strategy", "response_strategy"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value or len(value) > 64:
                raise ValueError(f"CFW projection {name} must be bounded text")
        if type(self.recovery_level) is not int or not 0 <= self.recovery_level <= 3:
            raise ValueError("CFW projection recovery level must be between zero and three")
        leases = tuple(self.target_leases)
        if not isinstance(self.target_leases, (tuple, list)) or len(leases) > _MAX_LEASES:
            raise ValueError("CFW projection target leases exceed their bound")
        object.__setattr__(self, "framework_modes",
                           MappingProxyType(dict(self.framework_modes)))
        object.__setattr__(self, "focus_criteria", focus)
        object.__setattr__(self, "target_leases", leases)

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "behavior_revision": self.behavior_revision,
            "cognitive_mode": self.cognitive_mode,
            "framework_modes": dict(self.framework_modes),
            "focus_criteria": list(self.focus_criteria),
            "verification_intensity": self.verification_intensity,
            "exploration_budget": self.exploration_budget,
            "retry_policy": self.retry_policy,
            "recovery_level": self.recovery_level,
            "context_strategy": self.context_strategy,
            "response_strategy": self.response_strategy,
            "target_leases": [lease.to_dict() for lease in self.target_leases],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CFWBehaviorProjection":
        if not isinstance(value, Mapping):
            raise ValueError("CFW behavior projection must be a mapping")
        expected = {"protocol_version", "behavior_revision", "cognitive_mode",
                    "framework_modes", "focus_criteria", "verification_intensity",
                    "exploration_budget", "retry_policy", "recovery_level",
                    "context_strategy", "response_strategy", "target_leases"}
        if set(value) != expected:
            raise ValueError("CFW behavior projection does not match the versioned port")
        version = value.get("protocol_version")
        if version != CFW_BEHAVIOR_PROJECTION_VERSION:
            raise ValueError(f"unsupported CFW behavior projection version {version!r}")
        leases = tuple(CFWTargetLease(**dict(lease))
                       for lease in (value.get("target_leases") or ()))
        return cls(
            behavior_revision=value["behavior_revision"],
            cognitive_mode=value["cognitive_mode"],
            framework_modes=value["framework_modes"],
            focus_criteria=tuple(value["focus_criteria"]),
            verification_intensity=value["verification_intensity"],
            exploration_budget=value["exploration_budget"],
            retry_policy=value["retry_policy"],
            recovery_level=value["recovery_level"],
            context_strategy=value["context_strategy"],
            response_strategy=value["response_strategy"],
            target_leases=leases,
            protocol_version=version,
        )


__all__ = ["CFW_BEHAVIOR_PROJECTION_VERSION", "CFWTargetLease", "CFWBehaviorProjection"]


def build_cfw_behavior_projection(
        state: Any, *, directives: tuple[Mapping[str, Any], ...] = (),
        host_action_index: int = 0) -> CFWBehaviorProjection:
    """Project DP's own state into the narrow actuation contract CFW consumes.

    ``directives`` are the CFW-facing control changes DP just decided. Only
    those become target leases; DP's other intervention bookkeeping stays inside
    DP. ``host_action_index`` is the action the lease baseline is measured
    from, so CFW, CSW and DP agree on the expiry boundary.
    """
    if type(host_action_index) is not int or host_action_index < 0:
        raise ValueError("host action index must be a non-negative integer")

    leases: list[CFWTargetLease] = []
    for directive in directives:
        target = directive.get("target")
        value = directive.get("value")
        operation = directive.get("operation")
        if not isinstance(target, str) or not target:
            continue
        if operation not in {"set_mode", "set_framework", "set_control"}:
            continue
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            continue
        duration = directive.get("expires_after_actions")
        if type(duration) is not int or duration < 0:
            duration = 0
        directive_id = directive.get("directive_id")
        if not isinstance(directive_id, str) or not directive_id:
            raise ValueError("a CFW target lease requires its owning directive ID")
        previous = directive.get("previous_value")
        # A lease must record what it replaced; an unknown baseline is not a
        # lease, it is an unowned change, so refuse it rather than guess.
        if not isinstance(previous, (str, int)) or isinstance(previous, bool):
            raise ValueError(
                f"CFW target lease for {target!r} lacks its previous value")
        leases.append(CFWTargetLease(
            intervention_id=directive_id,
            target=target,
            previous_value=str(previous),
            applied_value=str(value),
            expires_at_action=host_action_index + duration,
            source_revision=int(getattr(state, "revision", 0)),
        ))

    return CFWBehaviorProjection(
        behavior_revision=int(getattr(state, "revision", 0)),
        cognitive_mode=str(state.cognitive_mode),
        framework_modes=dict(state.framework_modes),
        focus_criteria=tuple(state.focus_criteria),
        verification_intensity=str(state.verification_intensity),
        exploration_budget=str(state.exploration_budget),
        retry_policy=str(state.retry_policy),
        recovery_level=int(state.recovery_level),
        context_strategy=str(state.context_strategy),
        response_strategy=str(state.response_strategy),
        target_leases=tuple(leases),
    )


__all__.append("build_cfw_behavior_projection")
