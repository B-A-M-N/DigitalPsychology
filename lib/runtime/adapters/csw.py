"""DP-side adapter for CSW's authoritative workflow port.

CSW owns workflow identity, activation, eligibility, legal steps, evidence
validation, and state revisions. This adapter only normalizes CSW snapshots
for DP's behavioral policy.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from ...behavior_state import (CSWBehaviorSnapshot, StateWorkOption,
                               StateWorkReleaseResult)
from ..contracts import RuntimeIdentity
from ...compatibility import (
    CSW_ADAPTER_PROTOCOL_VERSION, CSW_WORKFLOW_PORT_VERSION,
    MAX_ELIGIBLE_STATEWORKS,
)

@dataclass(frozen=True)
class CSWWorkflowBinding:
    identity: Mapping[str, str]
    contract_hash: str

    def __post_init__(self) -> None:
        expected = {"namespace_id", "application_id", "application_instance_id",
                    "workflow_id", "subject_ref", "statework_id", "contract_version"}
        if set(self.identity) != expected:
            raise ValueError("CSW workflow binding identity fields are invalid")
        if any(not isinstance(value, str) or not value or len(value) > 256
               for value in self.identity.values()):
            raise ValueError("CSW workflow binding identity values are invalid")
        if (not isinstance(self.contract_hash, str) or len(self.contract_hash) != 64
                or any(char not in "0123456789abcdef" for char in self.contract_hash)):
            raise ValueError("CSW contract hash must be a SHA-256 digest")
        object.__setattr__(self, "identity", MappingProxyType(dict(self.identity)))


class CSWRuntimeAdapter:
    """Call an injected CSW WorkflowAuthority without importing CSW internals."""

    def __init__(self, authority: Any, *, identity: RuntimeIdentity,
                 bindings: Mapping[str, CSWWorkflowBinding]) -> None:
        self.authority = authority
        self.identity = identity
        self.bindings = MappingProxyType(dict(bindings))
        actual_version = getattr(authority, "workflow_port_version", None)
        if actual_version != CSW_WORKFLOW_PORT_VERSION:
            raise ValueError(
                f"CSW authority workflow port {actual_version!r} is incompatible with "
                f"DP's {CSW_WORKFLOW_PORT_VERSION!r}")
        checkpoint_operations = (
            "checkpoint_selection", "restore_selection_checkpoint",
            "finalize_selection_checkpoint",
        )
        missing_checkpoint_operations = tuple(
            name for name in checkpoint_operations
            if not callable(getattr(authority, name, None)))
        if missing_checkpoint_operations:
            raise ValueError(
                "CSW authority lacks durable selection checkpoint operations: "
                + ", ".join(missing_checkpoint_operations))
        if len(self.bindings) > MAX_ELIGIBLE_STATEWORKS:
            raise ValueError("CSW workflow binding set exceeds its bound")
        for statework_id, binding in self.bindings.items():
            if binding.identity.get("statework_id") != statework_id:
                raise ValueError("CSW binding ID does not match its identity")
            for name, value in identity.csw_scope().items():
                if binding.identity.get(name) != value:
                    raise ValueError("CSW workflow binding escapes the runtime scope")

    def behavioral_snapshot(self, *, candidate_statework_ids: Sequence[str] | None = None
                            ) -> CSWBehaviorSnapshot:
        """Read the current CSW-owned snapshot.

        This is a pure read. Expiring temporary leases is a separate,
        explicit mutation so a snapshot can never mutate workflow state.
        """
        candidates = (tuple(sorted(self.bindings)) if candidate_statework_ids is None
                      else tuple(candidate_statework_ids))
        if not set(candidates).issubset(self.bindings):
            raise ValueError("DP requested a StateWork without a CSW binding")
        raw = self.authority.behavioral_snapshot(
            workflow_scope=self.identity.csw_scope(),
            candidate_statework_ids=candidates)
        active_rows = raw.get("active_workflows") or ()
        active_ids = tuple(sorted(str(row["identity"]["statework_id"])
                                   for row in active_rows))
        states = {str(row["identity"]["statework_id"]): str(row["current_state"])
                  for row in active_rows}
        revisions = {str(row["identity"]["statework_id"]): int(row["revision"])
                     for row in active_rows}
        steps = {str(row["identity"]["statework_id"]): tuple(
            str(step["step_id"]) for step in row.get("eligible_steps") or ())
            for row in active_rows}
        options = tuple(StateWorkOption(
            statework_id=str(row["statework_id"]),
            domain=str(row.get("domain") or ""),
            behavior_tags=tuple(row.get("behavior_tags") or ()),
            purpose=str(row.get("purpose") or ""),
        ) for row in raw.get("eligible_stateworks") or ())
        return CSWBehaviorSnapshot(
            revision=int(raw["selection_revision"]),
            active_stateworks=active_ids,
            eligible_stateworks=options,
            workflow_states=states,
            snapshot_id=str(raw["snapshot_id"]),
            workflow_revisions=revisions,
            eligible_steps=steps,
            dependency_retention={
                str(row["identity"]["statework_id"]): tuple(
                    str(item) for item in row.get("dependency_required_by") or ())
                for row in active_rows if row.get("activation_owner") == "dependency"
            },
            registry_hash=str(raw["registry_hash"]),
        )

    def expire_leases(self, *, host_action_index: int) -> CSWBehaviorSnapshot:
        """Expire DP-owned temporary workflow leases, then re-read the snapshot.

        CSW owns the expiry decision; DP only names the host action boundary.
        """
        if type(host_action_index) is not int or host_action_index < 0:
            raise ValueError("host action index must be a non-negative integer")
        expire = getattr(self.authority, "expire_workflow_leases", None)
        if not callable(expire):
            raise AttributeError(
                "CSW authority does not expose explicit workflow lease expiry")
        expire(workflow_scope=self.identity.csw_scope(),
               host_action_index=host_action_index)
        return self.behavioral_snapshot()

    def available_statework_ids(self) -> tuple[str, ...]:
        """Return IDs backed by explicit bindings in this CSW authority."""
        return tuple(sorted(self.bindings))

    def authority_identity(self) -> dict[str, str]:
        """Stable identity for this CSW scope and its bound workflow contracts."""
        scope = json.dumps(self.identity.csw_scope(), sort_keys=True,
                           separators=(",", ":"))
        root = getattr(self.authority, "root", None)
        authority_basis = (str(root.resolve())
                           if callable(getattr(root, "resolve", None))
                           else f"{type(self.authority).__module__}.{type(self.authority).__qualname__}")
        authority_id = str(getattr(self.authority, "authority_instance_id", ""))
        if not authority_id:
            authority_id = "csw-authority:" + hashlib.sha256(
                authority_basis.encode()).hexdigest()
        registry_hash = str(getattr(self.authority, "registry_hash", ""))
        if not registry_hash:
            raise RuntimeError("CSW authority omitted its pinned registry hash")
        capabilities = json.dumps({
            "registry_hash": registry_hash,
            "bindings": {
                statework_id: {"identity": dict(binding.identity),
                               "contract_hash": binding.contract_hash}
                for statework_id, binding in sorted(self.bindings.items())
            },
        }, sort_keys=True, separators=(",", ":"))
        return {
            "adapter_kind": "csw-workflow-authority",
            "authority_id": authority_id,
            "adapter_protocol_version": CSW_ADAPTER_PROTOCOL_VERSION,
            # Named explicitly so a host can compare the workflow port it will
            # drive against the one CSW implements, at startup rather than on
            # the first workflow transition.
            "workflow_port_version": str(self.authority.workflow_port_version),
            "registry_hash": registry_hash,
            "scope": hashlib.sha256(scope.encode()).hexdigest(),
            "scope_hash": hashlib.sha256(scope.encode()).hexdigest(),
            "capability_hash": hashlib.sha256(capabilities.encode()).hexdigest(),
        }

    def workflow_authority_identities(self) -> Mapping[str, Mapping[str, str]]:
        """Return CSW's immutable identity for every binding this adapter can use."""
        provider = getattr(self.authority, "workflow_authority_identity", None)
        if not callable(provider):
            raise RuntimeError(
                "CSW authority does not expose stable workflow authority identities")
        expected_fields = {
            "namespace_id", "application_id", "application_instance_id",
            "workflow_id", "subject_ref", "registry_hash",
            "authority_instance_id", "storage_identity", "contract_version",
        }
        result: dict[str, Mapping[str, str]] = {}
        for statework_id, binding in sorted(self.bindings.items()):
            raw = provider(binding.identity, contract_hash=binding.contract_hash)
            if callable(getattr(raw, "to_dict", None)):
                raw = raw.to_dict()
            if (not isinstance(raw, Mapping) or set(raw) != expected_fields
                    or any(not isinstance(value, str) or not value
                           for value in raw.values())):
                raise RuntimeError("CSW returned a malformed workflow authority identity")
            for field in ("namespace_id", "application_id", "application_instance_id",
                          "workflow_id", "subject_ref", "contract_version"):
                if raw[field] != binding.identity[field]:
                    raise RuntimeError(
                        "CSW workflow authority identity differs from its adapter binding")
            if raw["registry_hash"] != str(getattr(self.authority, "registry_hash", "")):
                raise RuntimeError("CSW workflow authority registry identity is inconsistent")
            result[statework_id] = MappingProxyType(dict(raw))
        return MappingProxyType(result)

    def checkpoint_selection(self, *, tick_id: str,
                             expected_selection_revision: int) -> None:
        checkpoint = getattr(self.authority, "checkpoint_selection", None)
        if not callable(checkpoint):
            raise RuntimeError("CSW authority has no durable selection checkpoint")
        checkpoint(workflow_scope=self.identity.csw_scope(), tick_id=tick_id,
                   expected_selection_revision=expected_selection_revision)

    def restore_selection_checkpoint(self, *, tick_id: str,
                                     expected_selection_revision: int
                                     ) -> CSWBehaviorSnapshot | None:
        restore = getattr(self.authority, "restore_selection_checkpoint", None)
        if not callable(restore):
            raise RuntimeError("CSW authority has no durable selection checkpoint")
        result = restore(workflow_scope=self.identity.csw_scope(), tick_id=tick_id,
                         expected_selection_revision=expected_selection_revision)
        if result is None:
            return None
        return self.behavioral_snapshot()

    def finalize_selection_checkpoint(self, *, tick_id: str) -> None:
        finalize = getattr(self.authority, "finalize_selection_checkpoint", None)
        if not callable(finalize):
            raise RuntimeError("CSW authority has no durable selection checkpoint")
        finalize(workflow_scope=self.identity.csw_scope(), tick_id=tick_id)

    def activate_statework(self, statework_id: str, *,
                           expected_selection_revision: int,
                           candidate_statework_ids: Sequence[str],
                           intervention_id: str,
                           expires_at_action: int) -> CSWBehaviorSnapshot:
        if statework_id not in candidate_statework_ids:
            raise ValueError("StateWork activation is outside the CSW candidate set")
        binding = self._binding(statework_id)
        self.authority.activate_workflow(
            binding.identity, contract_hash=binding.contract_hash,
            expected_selection_revision=expected_selection_revision,
            activation_owner="digitalpsychology",
            control_session_id=self.identity.scope,
            intervention_id=intervention_id,
            expires_at_action=expires_at_action)
        return self.behavioral_snapshot(
            candidate_statework_ids=candidate_statework_ids)

    def deactivate_statework(self, statework_id: str, *,
                             expected_selection_revision: int,
                             candidate_statework_ids: Sequence[str],
                             intervention_id: str) -> StateWorkReleaseResult:
        if statework_id not in candidate_statework_ids:
            raise ValueError("StateWork deactivation is outside the CSW candidate set")
        self._binding(statework_id)
        raw = self.authority.deactivate_workflow(
            workflow_scope=self.identity.csw_scope(), statework_id=statework_id,
            expected_selection_revision=expected_selection_revision,
            expected_activation_owner="digitalpsychology",
            expected_control_session_id=self.identity.scope,
            expected_intervention_id=intervention_id)
        if not isinstance(raw, Mapping):
            raise RuntimeError("CSW returned a malformed StateWork release disposition")
        disposition = raw.get("disposition")
        required_by = raw.get("required_by") or ()
        released_stateworks = raw.get("released_stateworks") or ()
        snapshot = self.behavioral_snapshot(
            candidate_statework_ids=candidate_statework_ids)
        return StateWorkReleaseResult(
            disposition=str(disposition), statework_id=statework_id,
            required_by=tuple(str(item) for item in required_by),
            released_stateworks=tuple(str(item) for item in released_stateworks),
            snapshot=snapshot)

    def activation_matches(self, statework_id: str, *,
                           intervention_id: str) -> bool:
        """Check a pending DP activation against CSW's recorded owner receipt."""
        self._binding(statework_id)
        metadata = self.authority.workflow_activation_metadata(
            workflow_scope=self.identity.csw_scope(), statework_id=statework_id)
        return bool(metadata and metadata.get("activation_owner") == "digitalpsychology"
                    and metadata.get("control_session_id") == self.identity.scope
                    and metadata.get("intervention_id") == intervention_id)

    def apply_step(self, statework_id: str, step_id: str, *,
                   evidence_refs: Sequence[str], expected_workflow_revision: int) -> Any:
        snapshot = self.behavioral_snapshot(candidate_statework_ids=(statework_id,))
        if statework_id not in snapshot.active_stateworks:
            raise ValueError("CSW refused a step request for an inactive StateWork")
        if step_id not in snapshot.eligible_steps.get(statework_id, ()):
            raise ValueError("CSW snapshot does not list the requested legal step")
        binding = self._binding(statework_id)
        return self.authority.apply_step(
            binding.identity, contract_hash=binding.contract_hash,
            step_id=step_id, evidence_refs=tuple(evidence_refs),
            expected_revision=expected_workflow_revision)

    def _binding(self, statework_id: str) -> CSWWorkflowBinding:
        try:
            return self.bindings[statework_id]
        except KeyError as exc:
            raise ValueError(f"StateWork {statework_id!r} has no CSW binding") from exc


__all__ = ["CSWWorkflowBinding", "CSWRuntimeAdapter"]
