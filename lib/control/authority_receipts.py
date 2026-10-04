"""Receipt checks proving exact CFW and CSW authority application."""
from __future__ import annotations

from typing import Mapping

from ..behavior_state import BehavioralDecision
from ..control_protocol import ControlCommitRequest, ControlSessionDescriptor


def validate_applied_authorities(decision: BehavioralDecision,
                                  basis: Mapping[str, Any],
                                  request: ControlCommitRequest,
                                  descriptor: ControlSessionDescriptor) -> None:
    original_cfw = int(basis["cfw_lifecycle_revision"])
    expected_cfw = (decision.state.revision if descriptor.available_frameworks
                    else original_cfw)
    if request.cfw_lifecycle_revision != expected_cfw:
        raise RuntimeError("CFW lifecycle revision does not match the committed behavior state")

    original_active = set(basis["csw_active_stateworks"])
    expected_active = set(original_active)
    csw_changes = 0
    eligible = set(basis["csw_eligible_stateworks"])
    for directive in decision.directives:
        if directive.operation == "release_statework":
            if directive.target not in expected_active:
                raise ValueError("CSW release target was not active in the basis")
            expected_active.remove(directive.target)
            csw_changes += 1
        elif directive.operation == "prefer_statework":
            if directive.target not in eligible:
                raise ValueError("CSW activation target was not eligible in the basis")
            if directive.target in expected_active:
                raise ValueError("CSW activation target was already active")
            expected_active.add(directive.target)
            csw_changes += 1
    retained = dict(request.csw_snapshot.dependency_retention)
    for statework_id, required_by in retained.items():
        if statework_id not in original_active:
            raise ValueError("CSW dependency retention names a workflow outside the basis")
        if not set(required_by).issubset(expected_active):
            raise ValueError("CSW retained a prerequisite without its active dependents")
        expected_active.add(statework_id)
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
