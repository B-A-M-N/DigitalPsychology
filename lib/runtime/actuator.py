"""DP adapter for the harness-neutral triad transaction coordinator."""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from cognitive_runtime_core.errors import ActuationFailure
from cognitive_runtime_core.integrations.triad.actuator import (
    ControlActuator as TriadControlActuator,
    ControlApplication,
)

from ..behavior_state import BehaviorState
from ..compatibility import MAX_CONTROL_BATCH_ITEMS
from ..lease_window import intervention_expiry
from .cfw_projection import build_cfw_behavior_projection


def _project_cfw_behavior(state: Mapping[str, Any],
                          basis: Mapping[str, Any]) -> Mapping[str, Any]:
    action_index = basis.get("cfw_projection_action_index")
    if action_index is None:
        action_index = basis["host_action_index"]
    return build_cfw_behavior_projection(
        BehaviorState.from_mapping(state),
        host_action_index=int(action_index),
    ).to_dict()


def _empty_csw_snapshot() -> Any:
    from .adapters.standalone import NullCSWWorkflowPort
    return NullCSWWorkflowPort().behavioral_snapshot()


class ControlActuator(TriadControlActuator):
    """Adapt DP projections and lease semantics to the neutral coordinator."""

    def __init__(self, *, cfw: Any = None, csw: Any = None,
                 production_metrics: Any = None,
                 csw_snapshot_provider: Callable[[], tuple[Any, Sequence[str]]] | None = None
                 ) -> None:
        if cfw is None:
            from .adapters.standalone import NullCFWComponentPort
            cfw = NullCFWComponentPort()
        super().__init__(
            cfw=cfw,
            csw=csw,
            cfw_projection=_project_cfw_behavior,
            empty_csw_snapshot=_empty_csw_snapshot,
            csw_snapshot_provider=csw_snapshot_provider,
            intervention_expiry=intervention_expiry,
            max_directives=MAX_CONTROL_BATCH_ITEMS,
            production_metrics=production_metrics,
        )


__all__ = ["ActuationFailure", "ControlActuator", "ControlApplication"]
