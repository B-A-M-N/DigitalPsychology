"""DP-side adapter for CSW's authoritative workflow port.

CSW owns workflow identity, activation, eligibility, legal steps, evidence
validation, and state revisions. This adapter only normalizes CSW snapshots
for DP's behavioral policy.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from ...behavior_state import CSWBehaviorSnapshot, StateWorkOption
from ..contracts import RuntimeIdentity


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
        if len(self.bindings) > 32:
            raise ValueError("CSW workflow binding set exceeds its bound")
        for statework_id, binding in self.bindings.items():
            if binding.identity.get("statework_id") != statework_id:
                raise ValueError("CSW binding ID does not match its identity")
            for name, value in identity.csw_scope().items():
                if binding.identity.get(name) != value:
                    raise ValueError("CSW workflow binding escapes the runtime scope")

    def behavioral_snapshot(self, *, candidate_statework_ids: Sequence[str] | None = None
                            ) -> CSWBehaviorSnapshot:
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
        )

    def activate_statework(self, statework_id: str, *,
                           expected_selection_revision: int,
                           candidate_statework_ids: Sequence[str]) -> CSWBehaviorSnapshot:
        if statework_id not in candidate_statework_ids:
            raise ValueError("StateWork activation is outside the CSW candidate set")
        binding = self._binding(statework_id)
        self.authority.activate_workflow(
            binding.identity, contract_hash=binding.contract_hash,
            expected_selection_revision=expected_selection_revision)
        return self.behavioral_snapshot(
            candidate_statework_ids=candidate_statework_ids)

    def deactivate_statework(self, statework_id: str, *,
                             expected_selection_revision: int,
                             candidate_statework_ids: Sequence[str]) -> CSWBehaviorSnapshot:
        if statework_id not in candidate_statework_ids:
            raise ValueError("StateWork deactivation is outside the CSW candidate set")
        self._binding(statework_id)
        self.authority.deactivate_workflow(
            workflow_scope=self.identity.csw_scope(), statework_id=statework_id,
            expected_selection_revision=expected_selection_revision)
        return self.behavioral_snapshot(
            candidate_statework_ids=candidate_statework_ids)

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
