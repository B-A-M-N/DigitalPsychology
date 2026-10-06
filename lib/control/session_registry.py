"""Control-session registration, capability validation, and lookup."""
from __future__ import annotations

import hashlib
from functools import lru_cache
import json
from pathlib import Path
from typing import Any, Mapping

from ..control_protocol import (PROTOCOL_VERSION, ControlSessionDescriptor,
                                bounded_control_mapping)
from ..runtime.cfw_behavior_contract import (
    cfw_behavior_contract, initial_behavior_state,
    validate_state_against_cfw_contract,
)
from ..compatibility import (
    CFW_ADAPTER_PROTOCOL_VERSION, CSW_ADAPTER_PROTOCOL_VERSION,
    advertised_control_capabilities,
)


@lru_cache(maxsize=4)
def _cached_execution_identity(root_name: str,
                              protocol_version: str) -> tuple[tuple[str, str], ...]:
    """Hash immutable package sources once per installed package root."""
    root = Path(root_name)
    implementation_files = tuple(sorted(root.rglob("*.py")))
    policy_files = tuple(root / relative for relative in (
        "behavior_state.py",
        "control/policy_selector.py",
        "control/candidate_generator.py",
        "schemas/behavior-state-v1.schema.json",
        "schemas/behavioral-routing-profile.schema.json",
    ))
    if not implementation_files or any(
            not path.is_file() or path.is_symlink()
            for path in (*implementation_files, *policy_files)):
        raise RuntimeError("DP execution identity source set is incomplete or unsafe")
    candidate_generator_files = tuple(root / relative for relative in (
        "control/candidate_generator.py",
        "control/statework_selector.py",
    ))
    if any(not path.is_file() or path.is_symlink()
           for path in candidate_generator_files):
        raise RuntimeError("DP candidate generator identity source set is incomplete or unsafe")

    def hash_sources(files: tuple[Path, ...]) -> str:
        digest = hashlib.sha256()
        for path in files:
            relative = path.relative_to(root).as_posix()
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
        return digest.hexdigest()

    return tuple(sorted({
        "dp_control_implementation_version": protocol_version,
        "dp_control_implementation_hash": hash_sources(implementation_files),
        "behavior_policy_contract_hash": hash_sources(policy_files),
        "candidate_generator_contract_hash": hash_sources(candidate_generator_files),
    }.items()))


def invalidate_execution_identity_cache() -> None:
    """Recompute source pins only when the process deliberately reloads code."""
    _cached_execution_identity.cache_clear()


def current_behavior_policy_compatibility(
        policy_mapping: Mapping[str, Any]) -> dict[str, str]:
    """Pin a promoted policy to the exact DP implementation that evaluated it."""
    if not isinstance(policy_mapping, Mapping):
        raise TypeError("behavior policy compatibility requires a policy mapping")
    identity = SessionRegistryMethods()._execution_identity()
    encoded = json.dumps(dict(policy_mapping), sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False)
    return {
        "behavior_policy_contract_hash": identity["behavior_policy_contract_hash"],
        "candidate_generator_contract_hash": identity["candidate_generator_contract_hash"],
        "dp_control_implementation_version": identity["dp_control_implementation_version"],
        "dp_control_implementation_hash": identity["dp_control_implementation_hash"],
        "candidate_policy_mapping_hash": hashlib.sha256(
            encoded.encode("utf-8")).hexdigest(),
    }


class SessionRegistryMethods:
    def _execution_identity(self) -> dict[str, str]:
        """Pin DP implementation and behavior contract for this session epoch."""
        # Hash the importable package tree so the same implementation works
        # from both a checkout (lib/...) and its installed wheel
        # (digital_psychology/...). Package-relative paths also keep the digest
        # stable across those layouts. The package is immutable within a
        # process, so reuse its cached identity for later sessions.
        root = Path(__file__).resolve().parents[1]
        return dict(_cached_execution_identity(str(root), PROTOCOL_VERSION))

    def open_control_session(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        descriptor = ControlSessionDescriptor.from_mapping(raw)
        self._validate_session_capabilities(descriptor)
        scope = descriptor.control_session_id
        from ..telemetry.behavioral_trace import provider_attestation
        factory = self.engine.system_one_frame_factory
        provider_identity = {
            "factory_configured": factory is not None,
            "attestation": provider_attestation(factory),
            "execution_identity": self._execution_identity(),
        }
        self.state_store.register_control_session(
            scope, descriptor.to_dict(), provider_identity=provider_identity,
            initial_state=initial_behavior_state(descriptor))
        record = self.state_store.load(scope)
        validate_state_against_cfw_contract(record.state, descriptor)
        return {
            "protocol_version": PROTOCOL_VERSION,
            "control_session_id": scope,
            "behavior_revision": record.state.revision,
            "host_action_index": record.action_index,
            "boundary_sequence": self.state_store.latest_boundary_sequence(scope),
            "behavior_state": record.state.to_dict(),
            "capabilities": advertised_control_capabilities(),
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
                        "adapter_protocol_version") != CFW_ADAPTER_PROTOCOL_VERSION):
                raise ValueError("CFW adapter identity or protocol version is incompatible")
            actual = descriptor.cfw_authority_identity.get(
                "behavior_projection_version")
            if actual != CFW_BEHAVIOR_PROJECTION_VERSION:
                raise ValueError(
                    f"CFW behavior projection version {actual!r} is incompatible "
                    f"with DP's {CFW_BEHAVIOR_PROJECTION_VERSION!r}")
            raw_contract = capabilities.get("cfw_behavior_contract")
            if descriptor.cfw_authority_identity.get("adapter_kind") == (
                    "cfw-component-runtime") and raw_contract is None:
                raise ValueError("connected production CFW must supply its task behavior contract")
            contract = cfw_behavior_contract(descriptor)
            if contract is not None and descriptor.cfw_authority_identity.get(
                    "task_behavior_contract_hash") != contract["contract_hash"]:
                raise ValueError("CFW task behavior contract differs from its authority identity")
        if capabilities["csw_connected"]:
            if (descriptor.csw_authority_identity.get("adapter_kind")
                    != "csw-workflow-authority"
                    or descriptor.csw_authority_identity.get(
                        "adapter_protocol_version") != CSW_ADAPTER_PROTOCOL_VERSION):
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
