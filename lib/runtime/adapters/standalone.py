"""Explicit ports used when CFW or CSW is not part of a DP runtime session."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from ...behavior_state import CSWBehaviorSnapshot


@dataclass(frozen=True)
class _PreparedStandaloneDirectives:
    directives: tuple[dict[str, Any], ...]
    prior_controls: Mapping[str, Mapping[str, Any]]
    prior_projection: Mapping[str, Any] | None


class NullCFWComponentPort:
    """Keep DP's own behavior projection usable without a CFW installation.

    This port acknowledges only DP mode/control projections, which the DP host
    renders into its own prompt context. It never claims that a CFW component
    was activated and rejects framework-specific directives.
    """

    def __init__(self) -> None:
        self._controls: dict[str, dict[str, Any]] = {}
        self._behavior_projection: dict[str, Any] | None = None

    def authority_identity(self) -> Mapping[str, str]:
        return {"adapter_kind": "null-cfw", "authority_id": "none",
                "adapter_protocol_version": "1.0.0",
                "behavior_projection_version": "2.0.0", "scope": "none",
                "capability_hash": sha256(b"digitalpsychology:null-cfw").hexdigest()}

    def available_component_ids(self) -> tuple[str, ...]:
        return ()

    def lifecycle_revision(self) -> int:
        return 0

    def applied_behavior_projection(self) -> Mapping[str, Any] | None:
        return self._behavior_projection

    def restore_behavior_projection(self, projection: Mapping[str, Any], *,
                                    expected_current_revision: int) -> None:
        del expected_current_revision
        self._behavior_projection = dict(projection)

    def collect_behavior_signals(self, _observation: Any) -> tuple[()]:
        return ()

    def prepare_behavior_directives(
            self, directives: Sequence[Mapping[str, Any]]) -> _PreparedStandaloneDirectives:
        if len(directives) > 32:
            raise ValueError("standalone DP directive batch exceeds its bound")
        normalized = tuple(dict(item) for item in directives)
        if any(item.get("operation") == "set_framework" for item in normalized):
            raise ValueError("a standalone DP runtime has no CFW component actuator")
        return _PreparedStandaloneDirectives(
            directives=normalized,
            prior_controls=MappingProxyType({key: MappingProxyType(dict(value))
                                             for key, value in self._controls.items()}),
            prior_projection=(MappingProxyType(dict(self._behavior_projection))
                              if self._behavior_projection is not None else None),
        )

    def apply_behavior_projection(self, prepared: Any,
                                  projection: Mapping[str, Any]) -> tuple[str, ...]:
        if not isinstance(prepared, _PreparedStandaloneDirectives):
            raise TypeError("standalone DP directives were not prepared by this port")
        if not isinstance(projection, Mapping):
            raise TypeError("DP CFW projection must be a mapping")
        controls = dict(self._controls)
        directive_ids = []
        for item in prepared.directives:
            if item.get("operation") == "set_framework":
                raise ValueError("a standalone DP runtime has no CFW component actuator")
            directive_id = item.get("directive_id")
            if not isinstance(directive_id, str) or not directive_id:
                raise ValueError("standalone DP directive ID is malformed")
            controls[str(item.get("target") or "")] = dict(item)
            directive_ids.append(directive_id)
        if len(directive_ids) != len(set(directive_ids)):
            raise ValueError("standalone DP directive IDs must be unique")
        self._controls = controls
        self._behavior_projection = dict(projection)
        return tuple(directive_ids)

    def synchronize_behavior_projection(self, projection: Mapping[str, Any]) -> None:
        if not isinstance(projection, Mapping):
            raise TypeError("DP CFW projection must be a mapping")
        self._behavior_projection = dict(projection)

    def rollback_behavior_directives(self, prepared: Any) -> None:
        if not isinstance(prepared, _PreparedStandaloneDirectives):
            raise TypeError("standalone DP rollback requires its prepared directive batch")
        self._controls = {key: dict(value)
                          for key, value in prepared.prior_controls.items()}
        self._behavior_projection = (dict(prepared.prior_projection)
                                     if prepared.prior_projection is not None else None)

    def render_behavior_context(self, context: str, *,
                                active_statework_ids: Sequence[str] = ()) -> str:
        del active_statework_ids
        return context

    def before_model_call(self, context: str, *, tool_capable: bool = False,
                          action_hint: Mapping[str, Any] | None = None,
                          active_statework_ids: Sequence[str] = ()) -> str:
        del tool_capable, action_hint, active_statework_ids
        return context

    def before_tool_planning(self, context: str,
                             action_hint: Mapping[str, Any] | None = None,
                             active_statework_ids: Sequence[str] = ()) -> str:
        del action_hint, active_statework_ids
        return context


class NullCSWWorkflowPort:
    """Report an empty workflow set when no CSW authority is connected."""

    _SNAPSHOT_ID = sha256(b"digitalpsychology:no-csw-workflows").hexdigest()

    def authority_identity(self) -> Mapping[str, str]:
        return {"adapter_kind": "null-csw", "authority_id": "none",
                "protocol_version": "1.0.0", "scope": "none",
                "capability_hash": self._SNAPSHOT_ID}

    def behavioral_snapshot(self, *, candidate_statework_ids: Sequence[str] | None = None
                            ) -> CSWBehaviorSnapshot:
        if candidate_statework_ids:
            raise ValueError("no CSW authority is connected to resolve StateWorks")
        return CSWBehaviorSnapshot(revision=0, snapshot_id=self._SNAPSHOT_ID)

    def activate_statework(self, statework_id: str, *,
                           expected_selection_revision: int,
                           candidate_statework_ids: Sequence[str],
                           intervention_id: str,
                           expires_at_action: int) -> CSWBehaviorSnapshot:
        del (statework_id, expected_selection_revision, candidate_statework_ids,
             intervention_id, expires_at_action)
        raise RuntimeError("no CSW authority is connected to activate a StateWork")

    def deactivate_statework(self, statework_id: str, *,
                             expected_selection_revision: int,
                             candidate_statework_ids: Sequence[str],
                             intervention_id: str) -> CSWBehaviorSnapshot:
        del (statework_id, expected_selection_revision, candidate_statework_ids,
             intervention_id)
        raise RuntimeError("no CSW authority is connected to deactivate a StateWork")

    def expire_leases(self, *, host_action_index: int) -> CSWBehaviorSnapshot:
        if host_action_index < 0:
            raise ValueError("host action index must be non-negative")
        return self.behavioral_snapshot()

    def apply_step(self, statework_id: str, step_id: str, *,
                   evidence_refs: Sequence[str], expected_workflow_revision: int) -> Any:
        del statework_id, step_id, evidence_refs, expected_workflow_revision
        raise RuntimeError("no CSW authority is connected to apply a workflow step")


__all__ = ["NullCFWComponentPort", "NullCSWWorkflowPort"]
