"""Control-session registration, capability validation, and lookup."""
from __future__ import annotations

from typing import Any, Mapping

from ..control_protocol import (PROTOCOL_VERSION, ControlSessionDescriptor,
                                bounded_control_mapping)


class SessionRegistryMethods:
    def open_control_session(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        descriptor = ControlSessionDescriptor.from_mapping(raw)
        self._validate_session_capabilities(descriptor)
        scope = descriptor.control_session_id
        self.state_store.register_control_session(scope, descriptor.to_dict())
        record = self.state_store.load(scope)
        return {
            "protocol_version": PROTOCOL_VERSION,
            "control_session_id": scope,
            "behavior_revision": record.state.revision,
            "host_action_index": record.action_index,
            "boundary_sequence": self.state_store.latest_boundary_sequence(scope),
            "behavior_state": record.state.to_dict(),
            "capabilities": {
                "prepare_commit": True,
                "abort": True,
                "reconcile": True,
                "max_objective_criteria": 256,
                "max_cfw_signals": 32,
                "max_active_stateworks": 16,
            },
        }

    def _validate_session_capabilities(self,
                                       descriptor: ControlSessionDescriptor) -> None:
        """Fail closed on actuation versions and provider-required task semantics."""
        from ..runtime.cfw_projection import CFW_BEHAVIOR_PROJECTION_VERSION
        from ..runtime.adapters.csw import CSW_WORKFLOW_PORT_VERSION

        capabilities = descriptor.host_capabilities
        if capabilities["cfw_connected"]:
            if (descriptor.cfw_authority_identity.get("adapter_kind")
                    != "cfw-component-runtime"
                    or descriptor.cfw_authority_identity.get(
                        "adapter_protocol_version") != "1.0.0"):
                raise ValueError("CFW adapter identity or protocol version is incompatible")
            actual = descriptor.cfw_authority_identity.get(
                "behavior_projection_version")
            if actual != CFW_BEHAVIOR_PROJECTION_VERSION:
                raise ValueError(
                    f"CFW behavior projection version {actual!r} is incompatible "
                    f"with DP's {CFW_BEHAVIOR_PROJECTION_VERSION!r}")
        if capabilities["csw_connected"]:
            if (descriptor.csw_authority_identity.get("adapter_kind")
                    != "csw-workflow-authority"
                    or descriptor.csw_authority_identity.get(
                        "adapter_protocol_version") != "1.0.0"):
                raise ValueError("CSW adapter identity or protocol version is incompatible")
            actual = descriptor.csw_authority_identity.get("workflow_port_version")
            if actual != CSW_WORKFLOW_PORT_VERSION:
                raise ValueError(
                    f"CSW workflow port version {actual!r} is incompatible "
                    f"with DP's {CSW_WORKFLOW_PORT_VERSION!r}")
        if self.engine.system_one_frame_factory is not None:
            from .system_one import _deliverable_kind
            policy_context = capabilities.get("policy_context") or {}
            if not isinstance(policy_context, Mapping):
                raise ValueError("host semantic policy context must be a mapping")
            _deliverable_kind(policy_context, provider_enabled=True)

    def close_control_session(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        data = bounded_control_mapping(raw, "control close")
        if set(data) != {"protocol_version", "control_session_id"} \
                or data["protocol_version"] != PROTOCOL_VERSION:
            raise ValueError("control close protocol version or fields are invalid")
        scope = str(data["control_session_id"])
        self._descriptor(scope)
        self.state_store.close_control_session(scope)
        return {"protocol_version": PROTOCOL_VERSION,
                "control_session_id": scope, "status": "closed"}

    def _descriptor(self, scope: str) -> ControlSessionDescriptor:
        stored = self.state_store.control_session(scope)
        if stored is None or stored["status"] != "open":
            raise RuntimeError("control session is not open")
        descriptor = ControlSessionDescriptor.from_mapping(stored["descriptor"])
        if descriptor.control_session_id != scope:
            raise RuntimeError("persisted control session identity is inconsistent")
        return descriptor
