from __future__ import annotations

import hashlib
import json

from lib.behavior_state import (BehaviorState, CSWBehaviorSnapshot,
                                StateWorkReleaseResult)
from lib.host.actuator import ControlActuator


class _RetainingCSWPort:
    def __init__(self) -> None:
        self.revision = 4
        self.active = ("prerequisite", "dependent")
        self.retained = False

    def available_statework_ids(self):
        return self.active

    def behavioral_snapshot(self, *, candidate_statework_ids=None):
        retention = ({"prerequisite": ("dependent",)} if self.retained else {})
        payload = json.dumps({"revision": self.revision, "active": self.active,
                              "retention": retention}, sort_keys=True,
                             separators=(",", ":"))
        return CSWBehaviorSnapshot(
            revision=self.revision,
            active_stateworks=self.active,
            workflow_states={item: "ACTIVE" for item in self.active},
            snapshot_id=hashlib.sha256(payload.encode()).hexdigest(),
            workflow_revisions={item: 1 for item in self.active},
            dependency_retention=retention,
        )

    def deactivate_statework(self, statework_id, *, expected_selection_revision,
                             candidate_statework_ids, intervention_id):
        assert statework_id == "prerequisite"
        assert expected_selection_revision == self.revision
        assert "prerequisite" in candidate_statework_ids
        assert intervention_id == "intervention-1"
        self.retained = True
        self.revision += 1
        snapshot = self.behavioral_snapshot(
            candidate_statework_ids=candidate_statework_ids)
        return StateWorkReleaseResult(
            disposition="retained_required",
            statework_id="prerequisite",
            required_by=("dependent",),
            released_stateworks=(),
            snapshot=snapshot,
        )


def test_actuator_preserves_csw_required_dependency_release():
    csw = _RetainingCSWPort()
    before = csw.behavioral_snapshot()
    directive = {
        "directive_id": "release-prerequisite",
        "operation": "release_statework",
        "target": "prerequisite",
        "value": "unselected",
        "intervention_id": "intervention-1",
        "expires_at_action": 6,
    }
    prepared = {
        "status": "prepared",
        "decision_id": "decision-1",
        "candidate_behavior_state": BehaviorState(revision=1).to_dict(),
        "directives": [directive],
        "cfw_directives": [],
        "csw_directives": [directive],
        "basis": {
            "host_action_index": 5,
            "cfw_projection_action_index": 5,
            "cfw_lifecycle_revision": 0,
            "csw_selection_revision": before.revision,
            "csw_snapshot_id": before.snapshot_id,
            "csw_active_stateworks": list(before.active_stateworks),
        },
    }

    result = ControlActuator(csw=csw).apply(prepared)

    assert result.applied_directive_ids == ("release-prerequisite",)
    assert set(result.csw_snapshot.active_stateworks) == {
        "dependent", "prerequisite"}
    assert dict(result.csw_snapshot.dependency_retention) == {
        "prerequisite": ("dependent",)}
