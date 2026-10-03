"""Build bounded CFW/CSW snapshots and model-facing behavior projections."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from ..behavior_state import (CSWBehaviorSnapshot, FrameworkSignal,
                              GoalSetSnapshot)
from ..control_protocol import ControlSessionDescriptor
from ..runtime.contracts import RuntimeObservation
from ..runtime.projection import (AuditProjection,
                                  cached_replace_model_projection)
from ..runtime.adapters.standalone import NullCSWWorkflowPort
from .system_one_audit import summarize_system_one_observation

class HostProjectionManager:
    """Responsibility-focused host bridge collaborator."""

    def __init__(self, bridge: Any) -> None:
        self.host = bridge

    def _descriptor(self) -> ControlSessionDescriptor:
        return ControlSessionDescriptor(
            identity=self.host.identity,
            objective=self.host.objective,
            objective_criteria=self.host.objective_criteria,
            initial_goal_set=self.host.goal_set,
            available_frameworks=self.host._cfw_components,
            cfw_authority_identity=self.host._cfw_authority_identity(),
            csw_authority_identity=self.host._csw_identity,
            host_capabilities={
                "cfw_connected": bool(self.host._cfw_components),
                "csw_connected": self.host.csw is not None,
                "outcome_evidence_connected": self.host.outcome_evidence_connected,
                "host_initiated": True,
                "policy_context": {
                    **dict(self.host.host_policy_context),
                    "host_has_stable_objective_context": (
                        self.host.host_has_stable_objective_context),
                },
            },
        )

    def _project_model_context(self, context: str, projection: Any) -> str:
        enriched, self.host._last_model_projection_digest = cached_replace_model_projection(
            context, projection, self.host._last_model_projection_digest)
        return enriched

    def _record_system_one_observation(self, prepared: Mapping[str, Any], *,
                                       tick_status: str) -> None:
        """Keep provider evidence in host audit state, outside model context."""
        observation = summarize_system_one_observation(
            prepared, tick_status=tick_status)
        if observation is None:
            return
        self.host._latest_system_one_observation = observation
        metadata = dict(self.host._audit_projection.metadata)
        metadata["system_one"] = deepcopy(observation)
        self.host._audit_projection = AuditProjection(metadata=metadata)

    def _available_frameworks(self) -> tuple[str, ...]:
        if self.host.cfw is None:
            return ()
        values = self.host.cfw.available_component_ids()
        return tuple(sorted(str(item).lower() for item in values))

    def _authority_identity(self) -> dict[str, str]:
        if self.host.csw is None:
            return {}
        provider = getattr(self.host.csw, "authority_identity", None)
        if callable(provider):
            return dict(provider())
        snapshot, _ = self.host._csw_snapshot()
        return {
            "adapter_kind": "csw-workflow-port",
            "scope_hash": self.host.identity.scope,
            "capability_hash": snapshot.snapshot_id,
        }

    def _cfw_authority_identity(self) -> dict[str, str]:
        if self.host.cfw is None or not self.host._cfw_components:
            return {}
        provider = getattr(self.host.cfw, "authority_identity", None)
        value = provider() if callable(provider) else provider
        if (not isinstance(value, Mapping)
                or any(not isinstance(key, str) or not isinstance(item, str)
                       for key, item in value.items())):
            raise RuntimeError("CFW did not provide a valid authority identity")
        return dict(value)

    def _csw_snapshot(self) -> tuple[CSWBehaviorSnapshot, tuple[str, ...]]:
        if self.host.csw is None:
            return NullCSWWorkflowPort().behavioral_snapshot(), ()
        snapshot = self.host.csw.behavioral_snapshot()
        available = getattr(self.host.csw, "available_statework_ids", None)
        if callable(available):
            candidates = tuple(sorted(set(available()) |
                                      set(snapshot.active_stateworks) |
                                      {item.statework_id
                                       for item in snapshot.eligible_stateworks}))
            snapshot = self.host.csw.behavioral_snapshot(
                candidate_statework_ids=candidates)
        else:
            candidates = tuple(sorted(set(snapshot.active_stateworks) |
                                      {item.statework_id
                                       for item in snapshot.eligible_stateworks}))
        return snapshot, candidates

    def _cfw_signals(self, observation: RuntimeObservation) -> tuple[FrameworkSignal, ...]:
        if self.host.cfw is None:
            return ()
        return tuple(self.host.cfw.collect_behavior_signals(observation))

    def _current_cfw_revision(self) -> int:
        if not self.host._cfw_components:
            return 0
        revision = self.host.actuator.lifecycle_revision()
        if revision < -1:
            raise RuntimeError("CFW lifecycle revision is invalid")
        return revision

    @staticmethod
    def _reported_goal_set(response: Mapping[str, Any]) -> GoalSetSnapshot:
        value = response.get("goal_set")
        if not isinstance(value, Mapping):
            raise RuntimeError("DP reconciliation omitted the complete goal set")
        from ..control_protocol import parse_goal_set
        return parse_goal_set(value)

    @staticmethod
    def _same_cfw_projection(actual: Mapping[str, Any] | None,
                             expected: Mapping[str, Any]) -> bool:
        if actual is None:
            return False
        return dict(actual) == dict(expected)

    @staticmethod
    def _directives(raw: Any) -> tuple[dict[str, Any], ...]:
        if not isinstance(raw, (tuple, list)) or len(raw) > 32:
            raise ValueError("pending control directives are malformed")
        return tuple(dict(item) for item in raw if isinstance(item, Mapping))
