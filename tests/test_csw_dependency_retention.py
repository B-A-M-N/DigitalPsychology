from __future__ import annotations

import asyncio
import hashlib
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from lib.behavior_state import (BehaviorDirective, BehaviorIntervention,
                                BehaviorState, InterventionTarget,
                                BehavioralDecision, CSWBehaviorSnapshot,
                                GoalItemSnapshot, GoalSetSnapshot,
                                StateWorkOption,
                                StateWorkReleaseResult)
from lib.host.actuator import ActuationFailure, ControlActuator
from lib.host.reconciler import HostReconciler
from lib.control.authority_receipts import validate_applied_authorities
from lib.control_protocol import ControlCommitRequest, ControlSessionDescriptor
from lib.runtime.contracts import RuntimeIdentity


class _RetainingCSWPort:
    def __init__(self) -> None:
        self.revision = 4
        self.active = ("prerequisite", "dependent")
        self.retained = False
        self.checkpoints = {}

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

    def checkpoint_selection(self, *, tick_id, expected_selection_revision):
        assert expected_selection_revision == self.revision
        assert tick_id not in self.checkpoints
        self.checkpoints[tick_id] = (self.revision, self.retained)

    def restore_selection_checkpoint(self, *, tick_id, expected_selection_revision):
        assert expected_selection_revision == self.revision
        checkpoint = self.checkpoints.pop(tick_id, None)
        if checkpoint is None:
            return None
        _revision, self.retained = checkpoint
        self.revision += 1
        return self.behavioral_snapshot()

    def finalize_selection_checkpoint(self, *, tick_id):
        self.checkpoints.pop(tick_id, None)

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
        "tick_id": "tick-1",
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
    csw.finalize_selection_checkpoint(tick_id="tick-1")

    assert result.applied_directive_ids == ("release-prerequisite",)
    assert set(result.csw_snapshot.active_stateworks) == {
        "dependent", "prerequisite"}
    assert dict(result.csw_snapshot.dependency_retention) == {
        "prerequisite": ("dependent",)}


class _DependencySwitchCSWPort:
    """Model activating a dependent before releasing its current prerequisite."""

    def __init__(self):
        self.revision = 7
        self.owners = {
            "gitter": {"owner": "digitalpsychology",
                       "intervention_id": "intervention-gitter"}}
        self.events = []
        self.checkpoints = {}

    def available_statework_ids(self):
        return ("getter", "gitter")

    def behavioral_snapshot(self, *, candidate_statework_ids=None):
        active = tuple(sorted(self.owners))
        eligible = (() if "getter" in self.owners or "gitter" not in self.owners
                    else (StateWorkOption("getter", domain="repository_state"),))
        retained = ({"gitter": ("getter",)}
                    if self.owners.get("gitter", {}).get("owner") == "dependency"
                    else {})
        body = {"revision": self.revision, "active": active,
                "eligible": tuple(item.statework_id for item in eligible),
                "owners": self.owners, "retained": retained}
        snapshot_id = hashlib.sha256(json.dumps(
            body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return CSWBehaviorSnapshot(
            revision=self.revision, active_stateworks=active,
            eligible_stateworks=eligible,
            workflow_states={item: "ACTIVE" for item in active},
            snapshot_id=snapshot_id,
            workflow_revisions={item: 1 for item in active},
            dependency_retention=retained, registry_hash="b" * 64)

    def checkpoint_selection(self, *, tick_id, expected_selection_revision):
        assert expected_selection_revision == self.revision
        self.checkpoints[tick_id] = (self.revision, deepcopy(self.owners))

    def restore_selection_checkpoint(self, *, tick_id, expected_selection_revision):
        assert expected_selection_revision == self.revision
        checkpoint = self.checkpoints.pop(tick_id, None)
        if checkpoint is None:
            return None
        self.owners = checkpoint[1]
        self.revision += 1
        return self.behavioral_snapshot()

    def finalize_selection_checkpoint(self, *, tick_id):
        self.checkpoints.pop(tick_id, None)

    def activate_statework(self, statework_id, *, expected_selection_revision,
                           candidate_statework_ids, intervention_id,
                           expires_at_action):
        assert expected_selection_revision == self.revision
        assert set(candidate_statework_ids) == {"getter", "gitter"}
        assert statework_id == "getter"
        assert intervention_id == "intervention-getter"
        assert expires_at_action == 6
        snapshot = self.behavioral_snapshot(
            candidate_statework_ids=candidate_statework_ids)
        assert statework_id in {item.statework_id
                                for item in snapshot.eligible_stateworks}
        self.events.append(("activate", statework_id))
        self.owners[statework_id] = {
            "owner": "digitalpsychology", "intervention_id": intervention_id}
        self.revision += 1
        return self.behavioral_snapshot(
            candidate_statework_ids=candidate_statework_ids)

    def deactivate_statework(self, statework_id, *, expected_selection_revision,
                             candidate_statework_ids, intervention_id):
        assert expected_selection_revision == self.revision
        assert statework_id == "gitter"
        assert intervention_id == "intervention-gitter"
        self.events.append(("release", statework_id))
        if "getter" in self.owners:
            self.owners[statework_id] = {"owner": "dependency",
                                         "intervention_id": None}
            disposition = "retained_required"
            required_by = ("getter",)
        else:
            self.owners.pop(statework_id)
            disposition = "released"
            required_by = ()
        self.revision += 1
        snapshot = self.behavioral_snapshot(
            candidate_statework_ids=candidate_statework_ids)
        return StateWorkReleaseResult(
            disposition=disposition, statework_id=statework_id,
            required_by=required_by,
            released_stateworks=() if required_by else (statework_id,),
            snapshot=snapshot)


def test_actuator_activates_dependent_before_releasing_its_prerequisite():
    csw = _DependencySwitchCSWPort()
    initial = csw.behavioral_snapshot()
    active_interventions = (
        BehaviorIntervention(
            intervention_id="intervention-getter", cause="switch_workflow",
            expected_effect="apply repository change controls",
            start_revision=1, scope="session", expires_after_actions=4,
            actions_elapsed=0,
            targets=(InterventionTarget("statework:getter", "unselected", "preferred"),),
            expires_at_action=6, target_criteria=("criterion",)),
        BehaviorIntervention(
            intervention_id="intervention-gitter", cause="switch_workflow",
            expected_effect="release the old repository workflow",
            start_revision=1, scope="session", expires_after_actions=4,
            actions_elapsed=0,
            targets=(InterventionTarget("statework:gitter", "preferred", "unselected"),),
            expires_at_action=6, target_criteria=("criterion",)),
    )
    state = BehaviorState(revision=1, active_interventions=active_interventions)
    directives = [
        {"directive_id": "release-gitter", "operation": "release_statework",
         "target": "gitter", "value": "unselected",
         "intervention_id": "intervention-gitter", "expires_at_action": 6},
        {"directive_id": "activate-getter", "operation": "prefer_statework",
         "target": "getter", "value": "preferred",
         "intervention_id": "intervention-getter", "expires_at_action": 6},
    ]
    prepared = {
        "status": "prepared", "decision_id": "decision-switch",
        "tick_id": "tick-switch", "candidate_behavior_state": state.to_dict(),
        "directives": directives, "cfw_directives": [],
        "csw_directives": directives,
        "basis": {
            "host_action_index": 5, "cfw_projection_action_index": 5,
            "cfw_lifecycle_revision": 0,
            "csw_selection_revision": initial.revision,
            "csw_snapshot_id": initial.snapshot_id,
            "csw_active_stateworks": list(initial.active_stateworks),
        },
    }

    result = ControlActuator(csw=csw).apply(prepared)

    assert csw.events == [("activate", "getter"), ("release", "gitter")]
    assert set(result.csw_snapshot.active_stateworks) == {"getter", "gitter"}
    assert dict(result.csw_snapshot.dependency_retention) == {
        "gitter": ("getter",)}
    assert csw.owners["getter"]["owner"] == "digitalpsychology"
    assert csw.owners["gitter"]["owner"] == "dependency"
    csw.finalize_selection_checkpoint(tick_id="tick-switch")


def _cascade_rows():
    def row(statework_id, owner, session=None, intervention=None, expiry=None):
        return {"identity": {"statework_id": statework_id},
                "contract_hash": hashlib.sha256(statework_id.encode()).hexdigest(),
                "activation_owner": owner, "control_session_id": session,
                "intervention_id": intervention, "activation_revision": 1,
                "expires_at_action": expiry}
    return {
        "A": row("A", "dependency"),
        "B": row("B", "dependency"),
        "C": row("C", "digitalpsychology", "session", "intervention-C", 9),
    }


class _CascadeCSWPort:
    """Three-level A <- B <- C selection with durable test checkpoints."""
    REQUIRED_BY = {"A": ("B",), "B": ("C",)}

    def __init__(self):
        self.revision = 4
        self.rows = _cascade_rows()
        self.checkpoints = {}
        self.finalized = []

    @property
    def active(self):
        return tuple(sorted(self.rows))

    def available_statework_ids(self):
        return ("A", "B", "C")

    def behavioral_snapshot(self, *, candidate_statework_ids=None):
        active = self.active
        retention = {key: values for key, values in self.REQUIRED_BY.items()
                     if key in active and set(values).intersection(active)}
        body = {"revision": self.revision, "active": active, "retention": retention}
        snapshot_id = hashlib.sha256(json.dumps(
            body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return CSWBehaviorSnapshot(
            revision=self.revision, active_stateworks=active,
            workflow_states={key: "ACTIVE" for key in active},
            snapshot_id=snapshot_id,
            workflow_revisions={key: 1 for key in active},
            dependency_retention=retention, registry_hash="e" * 64)

    def checkpoint_selection(self, *, tick_id, expected_selection_revision):
        assert expected_selection_revision == self.revision
        self.checkpoints[tick_id] = deepcopy(self.rows)

    def restore_selection_checkpoint(self, *, tick_id, expected_selection_revision):
        assert expected_selection_revision == self.revision
        rows = self.checkpoints.pop(tick_id, None)
        if rows is None:
            return None
        self.rows = rows
        self.revision += 1
        return self.behavioral_snapshot()

    def finalize_selection_checkpoint(self, *, tick_id):
        self.checkpoints.pop(tick_id, None)
        self.finalized.append(tick_id)

    def deactivate_statework(self, statework_id, *, expected_selection_revision,
                             candidate_statework_ids, intervention_id):
        assert expected_selection_revision == self.revision
        assert intervention_id == "intervention-C"
        self.rows.pop(statework_id)
        released = {statework_id}
        changed = True
        while changed:
            changed = False
            for retained, required_by in tuple(self.REQUIRED_BY.items()):
                if (retained in self.rows
                        and not set(required_by).intersection(self.rows)):
                    self.rows.pop(retained)
                    released.add(retained)
                    changed = True
        self.revision += 1
        snapshot = self.behavioral_snapshot(
            candidate_statework_ids=candidate_statework_ids)
        return StateWorkReleaseResult(
            disposition="released", statework_id=statework_id,
            required_by=(), released_stateworks=tuple(sorted(released)),
            snapshot=snapshot)


def _release(target="C", directive_id="release-C"):
    return {
        "directive_id": directive_id, "operation": "release_statework",
        "target": target, "value": "unselected", "intervention_id": "intervention-C",
        "expires_at_action": 9,
    }


def _prepared(csw, directives):
    before = csw.behavioral_snapshot()
    return {
        "status": "prepared", "tick_id": "cascade-tick", "decision_id": "decision-1",
        "basis_digest": "f" * 64,
        "candidate_behavior_state": BehaviorState(revision=1).to_dict(),
        "directives": directives, "cfw_directives": [], "csw_directives": directives,
        "basis": {
            "host_action_index": 0, "cfw_projection_action_index": 0,
            "cfw_lifecycle_revision": 0, "csw_selection_revision": before.revision,
            "csw_snapshot_id": before.snapshot_id,
            "csw_active_stateworks": list(before.active_stateworks),
            "csw_dependency_retention": {
                key: list(value) for key, value in before.dependency_retention.items()},
        },
    }


def test_three_level_cascade_is_accepted_by_dp_commit_receipt():
    identity = RuntimeIdentity(
        namespace_id="ns", application_id="app", application_instance_id="app-1",
        agent_instance_id="agent-1", session_id="session", task_id="task",
        attempt_id="attempt", workflow_id="workflow", subject_ref="repo:test",
        segment_id="segment", agent_id="agent", behavioral_subject="repo:test")
    goals = GoalSetSnapshot(0, (GoalItemSnapshot("criterion"),))
    descriptor = ControlSessionDescriptor(
        identity=identity, objective="test", objective_criteria=("criterion",),
        initial_goal_set=goals, available_frameworks=(),
        cfw_authority_identity={}, csw_authority_identity={},
        host_capabilities={"cfw_connected": False, "csw_connected": False})
    directive = BehaviorDirective(
        directive_id="release-C", operation="release_statework", target="C",
        value="unselected", cause="test", expected_effect="release dependent",
        scope=identity.scope, expires_after_actions=1,
        intervention_id="intervention-C", expires_at_action=9)
    decision = BehavioralDecision(
        decision_id="decision-1", scope=identity.scope, expected_revision=0,
        state=BehaviorState(revision=1), directives=(directive,))
    basis = {
        "cfw_lifecycle_revision": 0, "csw_selection_revision": 4,
        "csw_snapshot_id": "a" * 64, "csw_registry_hash": "e" * 64,
        "csw_active_stateworks": ["A", "B", "C"],
        "csw_eligible_stateworks": [],
        "csw_workflow_revisions": {"A": 1, "B": 1, "C": 1},
        "csw_dependency_retention": {"A": ["B"], "B": ["C"]},
    }
    request = ControlCommitRequest(
        control_session_id=identity.scope, tick_id="cascade-tick",
        decision_id="decision-1", basis_digest="f" * 64,
        applied_directive_ids=("release-C",), cfw_lifecycle_revision=0,
        csw_snapshot=CSWBehaviorSnapshot(
            revision=5, active_stateworks=(), snapshot_id="b" * 64,
            registry_hash="e" * 64))

    validate_applied_authorities(decision, basis, request, descriptor)


def test_crash_after_three_level_apply_reconciles_as_committed():
    csw = _CascadeCSWPort()
    prepared = _prepared(csw, [_release()])
    ControlActuator(csw=csw).apply(prepared)
    assert csw.active == ()

    class Client:
        receipts = []

        async def commit_control_tick(self, receipt):
            self.receipts.append(receipt)
            return {"status": "committed", "behavior_state": prepared[
                "candidate_behavior_state"], "host_action_index": 0,
                    "cfw_projection_action_index": 0, "boundary_sequence": 1}

    host = SimpleNamespace(
        identity=SimpleNamespace(scope="session"),
        goal_set=GoalSetSnapshot(0, (GoalItemSnapshot("criterion"),)),
        _cfw_components=(), actuator=ControlActuator(csw=csw), csw=csw,
        client=Client(),
        _directives=lambda raw: tuple(dict(item) for item in raw),
        _csw_snapshot=lambda: (csw.behavioral_snapshot(),
                               csw.available_statework_ids()),
        _current_cfw_revision=lambda: 0,
        _accept_committed=lambda _result, _goals: None,
    )
    assert asyncio.run(HostReconciler(host)._recover_pending(prepared)) is False
    assert len(host.client.receipts) == 1
    assert host.client.receipts[0]["csw_snapshot"]["active_stateworks"] == []
    assert csw.finalized == ["cascade-tick"]
    assert csw.checkpoints == {}


def test_failure_after_cascade_restores_exact_original_ownership_rows():
    csw = _CascadeCSWPort()
    original_rows = deepcopy(csw.rows)
    prepared = _prepared(csw, [_release(), _release("A", "release-A")])

    with pytest.raises(ActuationFailure, match="current selection") as failure:
        ControlActuator(csw=csw).apply(prepared)

    assert failure.value.rollback_completed
    assert csw.rows == original_rows
    assert csw.active == ("A", "B", "C")
    assert csw.checkpoints == {}
