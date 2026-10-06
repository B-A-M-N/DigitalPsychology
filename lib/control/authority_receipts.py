"""Receipt checks proving exact CFW and CSW authority application."""
from __future__ import annotations

from typing import Mapping

from ..behavior_state import BehavioralDecision
from ..control_protocol import ControlCommitRequest, ControlSessionDescriptor
from ..host.dependency_closure import expected_csw_active_stateworks


def validate_applied_authorities(decision: BehavioralDecision,
                                  basis: Mapping[str, Any],
                                  request: ControlCommitRequest,
                                  descriptor: ControlSessionDescriptor) -> None:
    original_cfw = int(basis["cfw_lifecycle_revision"])
    expected_cfw = (decision.state.revision if descriptor.available_frameworks
                    else original_cfw)
    if request.cfw_lifecycle_revision != expected_cfw:
        raise RuntimeError("CFW lifecycle revision does not match the committed behavior state")
    expected_registry_hash = str(basis.get("csw_registry_hash") or "")
    if expected_registry_hash and request.csw_snapshot.registry_hash != expected_registry_hash:
        raise RuntimeError("CSW registry identity changed during control application")

    original_active = set(basis["csw_active_stateworks"])
    csw_changes = 0
    eligible = set(basis["csw_eligible_stateworks"])
    for directive in decision.directives:
        if directive.operation == "release_statework":
            if directive.target not in original_active:
                raise ValueError("CSW release target was not active in the basis")
            csw_changes += 1
        elif directive.operation == "prefer_statework":
            if directive.target not in eligible:
                raise ValueError("CSW activation target was not eligible in the basis")
            if directive.target in original_active:
                raise ValueError("CSW activation target was already active")
            csw_changes += 1
    retained = dict(request.csw_snapshot.dependency_retention)
    for statework_id, required_by in retained.items():
        if statework_id not in original_active:
            # An activation can introduce a dependency-owned prerequisite.
            if not any(item.operation == "prefer_statework"
                       for item in decision.directives):
                raise ValueError("CSW dependency retention names a workflow outside the basis")
    expected_active = expected_csw_active_stateworks(
        basis, [item.to_dict() for item in decision.directives], retained)
    if set(request.csw_snapshot.active_stateworks) != expected_active:
        raise ValueError("CSW post-application state does not match DP requests")
    expected_revision = int(basis["csw_selection_revision"]) + csw_changes
    if request.csw_snapshot.revision != expected_revision:
        raise RuntimeError("CSW selection revision changed during control application")
    if not csw_changes and request.csw_snapshot.snapshot_id != basis["csw_snapshot_id"]:
        raise RuntimeError("CSW snapshot changed without a prepared selection mutation")
    for statework_id, revision in basis["csw_workflow_revisions"].items():
        if (statework_id in request.csw_snapshot.active_stateworks
                and request.csw_snapshot.workflow_revisions.get(statework_id) != revision):
            raise RuntimeError("CSW workflow revision changed during control application")
