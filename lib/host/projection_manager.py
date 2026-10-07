"""Build bounded CFW/CSW snapshots and model-facing behavior projections."""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import replace
from typing import Any, Mapping

from ..behavior_state import (BehaviorSignalBatch, CSWBehaviorSnapshot,
                              FrameworkSignal, GoalSetSnapshot)
from ..compatibility import MAX_CFW_SIGNALS, MAX_CONTROL_BATCH_ITEMS
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
        return self.host._session_descriptor

    def _create_descriptor(self) -> ControlSessionDescriptor:
        capabilities = {
            "cfw_connected": bool(self.host._cfw_components),
            "csw_connected": self.host.csw is not None,
            "outcome_evidence_connected": self.host.outcome_evidence_connected,
            "host_initiated": True,
            "harness_adapter_identity": (
                self.host.harness_adapter_identity.to_dict()),
            "policy_context": {
                **dict(self.host.host_policy_context),
                "host_has_stable_objective_context": (
                    self.host.host_has_stable_objective_context),
            },
        }
        contract_provider = getattr(self.host.cfw, "task_behavior_contract", None)
        if callable(contract_provider):
            contract = contract_provider()
            if contract is not None:
                capabilities["cfw_behavior_contract"] = dict(contract)
        composite_provider = getattr(self.host.cfw, "composite_runtime_snapshot", None)
        if callable(composite_provider):
            composite = composite_provider()
            if composite is not None:
                if not isinstance(composite, Mapping):
                    raise RuntimeError("CFW returned a malformed composite runtime snapshot")
                composite = dict(composite)
                if self.host.csw is not None:
                    authority_registry = str(
                        self.host._csw_identity.get("registry_hash") or "")
                    cfw_registry = str(composite.get("csw_registry_hash") or "")
                    if cfw_registry and cfw_registry != authority_registry:
                        raise RuntimeError(
                            "CFW plan and DP CSW authority use different registries")
                    composite["csw_registry_hash"] = authority_registry
                host_goal_hash = str(
                    getattr(self.host.identity, "goal_contract_hash", None) or "")
                if host_goal_hash and len(host_goal_hash) == 64:
                    cfw_goal_hash = str(composite.get("host_goal_contract_hash") or "")
                    if cfw_goal_hash and cfw_goal_hash != host_goal_hash:
                        raise RuntimeError(
                            "CFW composite snapshot and host goal contract differ")
                    composite["host_goal_contract_hash"] = host_goal_hash
                composite["snapshot_hash"] = hashlib.sha256(json.dumps(
                    {key: value for key, value in composite.items()
                     if key != "snapshot_hash"}, sort_keys=True,
                    separators=(",", ":")).encode("utf-8")).hexdigest()
                capabilities["composite_runtime_snapshot"] = composite
        return ControlSessionDescriptor(
            identity=self.host.identity,
            objective=self.host.objective,
            objective_criteria=self.host.objective_criteria,
            initial_goal_set=self.host.initial_goal_set,
            available_frameworks=self.host._cfw_components,
            cfw_authority_identity=self.host._cfw_authority_identity(),
            csw_authority_identity=self.host._csw_identity,
            host_capabilities=capabilities,
            non_goals=self.host.non_goals,
        )

    def _project_model_context(self, context: str, projection: Any) -> str:
        enriched, self.host._last_model_projection_digest = cached_replace_model_projection(
            context, projection, self.host._last_model_projection_digest)
        return enriched

    def _offline_behavior_state(self) -> tuple[Any, Mapping[str, Any] | None]:
        """Project only controls still effective in CFW after DP lease expiry."""
        if not self.host._offline_controls_expired:
            return self.host._state, None
        provider = getattr(self.host.cfw, "effective_behavior_projection", None)
        effective = provider() if callable(provider) else provider
        if not isinstance(effective, Mapping):
            return self.host._state, None
        # CFW reports effective target values after its local expiry clock.
        # Overlay those controls on DP's last committed state so unrelated
        # leases, intervention history, and attention metadata survive an
        # outage projection.
        values: dict[str, Any] = {}
        for name in (
                "cognitive_mode", "framework_modes", "focus_criteria",
                "verification_intensity", "exploration_budget", "retry_policy",
                "recovery_level", "context_strategy", "response_strategy"):
            if name in effective:
                values[name] = effective[name]
        if "framework_modes" in values:
            values["framework_modes"] = dict(values["framework_modes"])
        if "focus_criteria" in values:
            values["focus_criteria"] = tuple(values["focus_criteria"])
        local = replace(self.host._state, **values)
        controls = {
            key: effective[key] for key in (
                "cognitive_mode", "framework_modes", "focus_criteria",
                "verification_intensity", "exploration_budget", "retry_policy",
                "recovery_level", "context_strategy", "response_strategy",
                "target_leases") if key in effective}
        return local, controls

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

    def _validate_shared_workflow_authorities(self) -> Mapping[str, Mapping[str, str]]:
        """Fail closed unless CFW and DP agree on every shared CSW workflow."""
        if self.host.cfw is None or self.host.csw is None:
            return {}
        cfw_provider = getattr(self.host.cfw, "workflow_authority_identities", None)
        cfw_raw = cfw_provider() if callable(cfw_provider) else {}
        if not isinstance(cfw_raw, Mapping) or len(cfw_raw) > 32:
            raise RuntimeError(
                "CFW and DP are bound to different CSW workflow authorities")
        if not cfw_raw:
            return {}
        csw_provider = getattr(self.host.csw, "workflow_authority_identities", None)
        if not callable(csw_provider):
            raise RuntimeError(
                "CFW and DP are bound to different CSW workflow authorities")
        csw_raw = csw_provider()
        if not isinstance(csw_raw, Mapping) or len(csw_raw) > 32:
            raise RuntimeError(
                "CFW and DP are bound to different CSW workflow authorities")
        fields = {
            "namespace_id", "application_id", "application_instance_id",
            "workflow_id", "subject_ref", "registry_hash",
            "authority_instance_id", "storage_identity", "contract_version",
        }

        def normalized(raw: Mapping[str, Any]) -> dict[str, dict[str, str]]:
            result: dict[str, dict[str, str]] = {}
            for statework_id, identity in raw.items():
                if callable(getattr(identity, "to_dict", None)):
                    identity = identity.to_dict()
                if (not isinstance(statework_id, str) or not statework_id
                        or not isinstance(identity, Mapping) or set(identity) != fields
                        or any(not isinstance(value, str) or not value
                               for value in identity.values())):
                    raise RuntimeError(
                        "CFW and DP are bound to different CSW workflow authorities")
                result[statework_id] = dict(identity)
            return result

        cfw_identities = normalized(cfw_raw)
        csw_identities = normalized(csw_raw)
        cfw_ids = set(cfw_identities)
        csw_ids = set(csw_identities)
        if not cfw_ids.issubset(csw_ids):
            missing = sorted(cfw_ids - csw_ids)
            raise RuntimeError(
                f"DP CSW authority does not cover CFW workflows: {missing}")
        shared_identities: dict[str, dict[str, str]] = {}
        for statework_id in sorted(cfw_ids):
            if cfw_identities[statework_id] != csw_identities[statework_id]:
                raise RuntimeError(
                    "CFW and DP are bound to different CSW workflow authorities")
            shared_identities[statework_id] = cfw_identities[statework_id]
        pinned = getattr(self.host, "_workflow_authority_identity_pin", None)
        if pinned is not None and shared_identities != pinned:
            raise RuntimeError(
                "CFW and DP are bound to different CSW workflow authorities")
        self.host._workflow_authority_identity_pin = shared_identities
        return shared_identities

    def _csw_snapshot(self) -> tuple[CSWBehaviorSnapshot, tuple[str, ...]]:
        self._validate_shared_workflow_authorities()
        descriptor = self.host._session_descriptor
        pinned_registry_hash = descriptor.csw_authority_identity.get("registry_hash")
        if self.host._cfw_components:
            current_cfw = self._cfw_authority_identity()
            if current_cfw != dict(descriptor.cfw_authority_identity):
                raise RuntimeError("CFW authority identity changed during the control session")
        if self.host.csw is None:
            return NullCSWWorkflowPort().behavioral_snapshot(), ()
        identity_provider = getattr(self.host.csw, "authority_identity", None)
        if callable(identity_provider):
            current_csw = dict(identity_provider())
            if current_csw != dict(descriptor.csw_authority_identity):
                raise RuntimeError("CSW authority identity changed during the control session")
        snapshot = self.host.csw.behavioral_snapshot()
        if pinned_registry_hash and snapshot.registry_hash != pinned_registry_hash:
            raise RuntimeError("CSW snapshot registry identity changed during the control session")
        available = getattr(self.host.csw, "available_statework_ids", None)
        if callable(available):
            candidates = tuple(sorted(set(available()) |
                                      set(snapshot.active_stateworks) |
                                      {item.statework_id
                                       for item in snapshot.eligible_stateworks}))
            snapshot = self.host.csw.behavioral_snapshot(
                candidate_statework_ids=candidates)
            if pinned_registry_hash and snapshot.registry_hash != pinned_registry_hash:
                raise RuntimeError(
                    "CSW snapshot registry identity changed during the control session")
        else:
            candidates = tuple(sorted(set(snapshot.active_stateworks) |
                                      {item.statework_id
                                       for item in snapshot.eligible_stateworks}))
        return snapshot, candidates

    def _cfw_signals(self, observation: RuntimeObservation) -> tuple[FrameworkSignal, ...]:
        return self._cfw_signal_batch(observation).signals

    def _cfw_signal_batch(self, observation: RuntimeObservation) -> BehaviorSignalBatch:
        if self.host.cfw is None:
            return BehaviorSignalBatch(after_cursor=0, next_cursor=0)
        return self.host.cfw.peek_behavior_signals(
            after_cursor=None, maximum=MAX_CFW_SIGNALS,
            observation=observation)

    def _acknowledge_cfw_signals(self, through_cursor: int) -> None:
        if self.host.cfw is None or through_cursor <= 0:
            return
        self.host.cfw.acknowledge_behavior_signals(through_cursor=through_cursor)

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
        if not isinstance(raw, (tuple, list)) or len(raw) > MAX_CONTROL_BATCH_ITEMS:
            raise ValueError("pending control directives are malformed")
        return tuple(dict(item) for item in raw if isinstance(item, Mapping))
