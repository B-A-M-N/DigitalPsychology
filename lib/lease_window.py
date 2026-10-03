"""Shared absolute action boundaries for DP-owned temporary leases."""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
from typing import Any, Iterable


@dataclass(frozen=True)
class LeaseWindow:
    """Inclusive action window for one intervention lease."""

    effective_from_action: int
    expires_at_action: int

    def __post_init__(self) -> None:
        if (type(self.effective_from_action) is not int
                or type(self.expires_at_action) is not int
                or self.effective_from_action < 0
                or self.expires_at_action < self.effective_from_action):
            raise ValueError("lease window action bounds are invalid")


def lease_window(boundary_kind: str, host_action_index: int,
                 duration: int) -> LeaseWindow:
    """Resolve PRE_ACTION/POST_ACTION duration to one inclusive absolute range.

    A pre-action lease covers the action about to be authorized. A post-action
    lease begins with the next action. Expiry is inclusive: the lease is valid
    through ``expires_at_action`` and is released at the following boundary.
    """
    if boundary_kind not in {"PRE_ACTION", "POST_ACTION"}:
        raise ValueError("lease boundary kind must be PRE_ACTION or POST_ACTION")
    if type(host_action_index) is not int or host_action_index < 0:
        raise ValueError("host action index must be non-negative")
    if type(duration) is not int or not 1 <= duration <= 64:
        raise ValueError("lease duration must be between one and sixty-four actions")
    start = host_action_index + (1 if boundary_kind == "POST_ACTION" else 0)
    return LeaseWindow(start, start + duration - 1)


def intervention_expiry(interventions: Iterable[Any], intervention_id: str) -> int:
    """Read an already-computed expiry from its owning intervention."""
    for item in interventions:
        if isinstance(item, Mapping):
            current_id = item.get("intervention_id")
            expiry = item.get("expires_at_action")
        else:
            current_id = getattr(item, "intervention_id", None)
            expiry = getattr(item, "expires_at_action", None)
        if current_id == intervention_id:
            if type(expiry) is not int or expiry < 0:
                raise ValueError("owning intervention has no absolute lease expiry")
            return expiry
    raise ValueError(f"owning intervention {intervention_id!r} is missing")


__all__ = ["LeaseWindow", "lease_window", "intervention_expiry"]
