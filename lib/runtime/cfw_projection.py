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
import hashlib
from types import MappingProxyType
from typing import Any, Mapping
from ..behavior_state import MAX_ACTIVE_INTERVENTIONS, MAX_TARGETS_PER_INTERVENTION

CFW_BEHAVIOR_PROJECTION_VERSION = "2.0.0"

_MAX_FOCUS = 8
_MAX_LEASES = MAX_ACTIVE_INTERVENTIONS * MAX_TARGETS_PER_INTERVENTION
_CONTROL_TARGETS = frozenset({
    "verification_intensity", "exploration_budget", "retry_policy",
    "recovery_level", "context_strategy", "response_strategy", "focus_criteria",
})


@dataclass(frozen=True)
class CFWTargetLease:
    """One bounded, expiring control value CFW must hold for DP."""

    lease_id: str
    intervention_id: str
    target: str
    previous_value: str
    applied_value: str
    expires_at_action: int
    source_revision: int

    def __post_init__(self) -> None:
        for name in ("lease_id", "intervention_id", "target", "previous_value",
                     "applied_value"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value or len(value) > 512:
                raise ValueError(f"CFW target lease {name} must be bounded text")
        for name in ("expires_at_action", "source_revision"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"CFW target lease {name} must be a non-negative integer")

    def to_dict(self) -> dict[str, Any]:
        return {"lease_id": self.lease_id,
                "intervention_id": self.intervention_id, "target": self.target,
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
    host_action_index: int = 0

    def __post_init__(self) -> None:
        if type(self.behavior_revision) is not int or self.behavior_revision < 0:
            raise ValueError("CFW behavior revision must be a non-negative integer")
        if type(self.host_action_index) is not int or self.host_action_index < 0:
            raise ValueError("CFW host action index must be non-negative")
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
        if (any(not isinstance(item, CFWTargetLease) for item in leases)
                or len({item.lease_id for item in leases}) != len(leases)):
            raise ValueError("CFW projection target lease identities are malformed")
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
            "host_action_index": self.host_action_index,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CFWBehaviorProjection":
        if not isinstance(value, Mapping):
            raise ValueError("CFW behavior projection must be a mapping")
        expected = {"protocol_version", "behavior_revision", "cognitive_mode",
                    "framework_modes", "focus_criteria", "verification_intensity",
                    "exploration_budget", "retry_policy", "recovery_level",
                    "context_strategy", "response_strategy", "target_leases",
                    "host_action_index"}
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
            host_action_index=value["host_action_index"],
        )


__all__ = ["CFW_BEHAVIOR_PROJECTION_VERSION", "CFWTargetLease", "CFWBehaviorProjection"]


def build_cfw_behavior_projection(
        state: Any, *, host_action_index: int) -> CFWBehaviorProjection:
    """Project DP's own state into the narrow actuation contract CFW consumes.

    Active target leases come from persisted intervention truth so recovery can
    reconstruct the same projection without replaying the original directives.
    ``host_action_index`` is the absolute action boundary shared by all ports.
    """
    if type(host_action_index) is not int or host_action_index < 0:
        raise ValueError("host action index must be a non-negative integer")

    if isinstance(state, Mapping):
        from ..behavior_state import BehaviorState
        state = BehaviorState.from_mapping(state)

    def field(value: Any, name: str) -> Any:
        return value.get(name) if isinstance(value, Mapping) else getattr(value, name)

    def canonical_target(raw: Any) -> str | None:
        if not isinstance(raw, str) or not raw:
            return None
        if raw == "mode":
            return "mode"
        if raw.startswith("framework:"):
            component = raw.split(":", 1)[1].lower()
            return f"framework:{component}" if component else None
        if raw.startswith("control:"):
            name = raw.split(":", 1)[1]
            return f"control:{name}" if name in _CONTROL_TARGETS else None
        if raw in _CONTROL_TARGETS:
            return f"control:{raw}"
        return None

    leases: list[CFWTargetLease] = []
    interventions = field(state, "active_interventions")
    for intervention in interventions:
        intervention_id = field(intervention, "intervention_id")
        source_revision = field(intervention, "start_revision")
        expires_at_action = field(intervention, "expires_at_action")
        if (not isinstance(intervention_id, str) or not intervention_id
                or type(source_revision) is not int
                or type(expires_at_action) is not int):
            raise ValueError("active DP intervention lease metadata is malformed")
        for target_record in field(intervention, "targets"):
            target = canonical_target(field(target_record, "target"))
            if target is None:
                continue
            previous = field(target_record, "previous_value")
            applied = field(target_record, "applied_value")
            if not isinstance(previous, str) or not isinstance(applied, str):
                raise ValueError(f"CFW target lease for {target!r} is malformed")
            lease_id = "lease-" + hashlib.sha256(
                f"{intervention_id}\0{target}".encode()).hexdigest()[:32]
            leases.append(CFWTargetLease(
                lease_id=lease_id,
                intervention_id=intervention_id,
                target=target,
                previous_value=previous,
                applied_value=applied,
                expires_at_action=expires_at_action,
                source_revision=source_revision,
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
        host_action_index=host_action_index,
    )


__all__.append("build_cfw_behavior_projection")
