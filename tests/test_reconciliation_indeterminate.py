"""D3: reconciliation must not translate unresolved uncertainty into certainty.

`reconcile_prepared` derives `applied` from an authoritative CSW re-read, so
committed-vs-not-committed IS resolved correctly when that read succeeds. The
defect was the failure mode: when the re-read itself failed, a raw transport
error escaped `reconcile_prepared` and the caller could not distinguish "still
unresolved" from "confirmed not applied".
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from cognitive_runtime_core.errors import ActuationFailure
from lib.behavior_state import (
    BehaviorIntervention,
    BehaviorState,
    CSWBehaviorSnapshot,
    GoalItemSnapshot,
    GoalSetSnapshot,
    InterventionTarget,
    StateWorkOption,
)
from lib.host.reconciler import HostReconciler
from lib.runtime.actuator import ControlActuator


def _snap(revision, active, eligible):
    return CSWBehaviorSnapshot(
        revision=revision, active_stateworks=active,
        eligible_stateworks=tuple(StateWorkOption(i, behavior_tags=())
                                  for i in eligible),
        snapshot_id="1" * 64, registry_hash="2" * 64)


class _Port:
    """activate commits (or not); the re-read fails only after a real commit."""

    def __init__(self, *, commit=True):
        self.revision = 7
        self.active = ()
        self.eligible = ("getter", "gitter")
        self.owners = {}
        self.checkpoints = {}
        self.activate_calls = 0
        self.commit = commit

    def available_statework_ids(self):
        return self.eligible

    def activation_matches(self, statework_id, *, intervention_id):
        owner = self.owners.get(statework_id) or {}
        return bool(owner.get("owner") == "digitalpsychology"
                    and owner.get("intervention_id") == intervention_id)

    def behavioral_snapshot(self, candidate_statework_ids=None):
        # The authoritative re-read fails once an activation has committed.
        if self.activate_calls >= 1 and self.active:
            raise TimeoutError("CSW snapshot read timed out")
        return _snap(self.revision, self.active, self.eligible)

    def checkpoint_selection(self, *, tick_id, expected_selection_revision):
        self.checkpoints[tick_id] = (self.revision, dict(self.owners),
                                     tuple(self.active))

    def restore_selection_checkpoint(self, *, tick_id, expected_selection_revision):
        row = self.checkpoints.pop(tick_id, None)
        if row is None:
            return None
        self.revision, self.owners, self.active = row[0], row[1], row[2]
        self.revision += 1
        return _snap(self.revision, self.active, self.eligible)

    def finalize_selection_checkpoint(self, *, tick_id):
        self.checkpoints.pop(tick_id, None)

    def activate_statework(self, statework_id, *, expected_selection_revision,
                           candidate_statework_ids, intervention_id,
                           expires_at_action):
        self.activate_calls += 1
        if self.commit:
            self.active = tuple(sorted(set(self.active) | {statework_id}))
            self.owners[statework_id] = {"owner": "digitalpsychology",
                                         "intervention_id": intervention_id}
            self.revision += 1
        raise TimeoutError("activation acknowledgment lost")

    def deactivate_statework(self, statework_id, *, expected_selection_revision,
                             candidate_statework_ids, intervention_id):
        self.owners.pop(statework_id, None)
        self.active = tuple(i for i in self.active if i != statework_id)
        self.revision += 1
        return _snap(self.revision, self.active, self.eligible)


def _prepared(csw, tick):
    before = csw.behavioral_snapshot()
    state = BehaviorState(revision=1, active_interventions=(
        BehaviorIntervention(
            intervention_id="intervention-getter", cause="switch_workflow",
            expected_effect="apply repository change controls",
            start_revision=1, scope="session", expires_after_actions=4,
            actions_elapsed=0,
            targets=(InterventionTarget(
                "statework:getter", "unselected", "preferred"),),
            expires_at_action=6, target_criteria=("criterion",)),))
    d = {"directive_id": "activate-getter", "operation": "prefer_statework",
         "target": "getter", "value": "preferred",
         "intervention_id": "intervention-getter", "expires_at_action": 6}
    return {
        "status": "prepared", "tick_id": tick,
        "decision_id": "d3", "basis_digest": "f" * 64,
        "candidate_behavior_state": state.to_dict(),
        "directives": [d], "cfw_directives": [], "csw_directives": [d],
        "basis": {
            "host_action_index": 0, "cfw_projection_action_index": 0,
            "cfw_lifecycle_revision": 0,
            "csw_selection_revision": before.revision,
            "csw_snapshot_id": before.snapshot_id,
            "csw_active_stateworks": list(before.active_stateworks),
            "csw_dependency_retention": {},
        },
    }


def test_failed_authoritative_read_is_reported_indeterminate():
    """An unresolved outcome must not escape as an unclassified transport error.

    Before the fix, `TimeoutError` propagated straight out of
    `reconcile_prepared`, so the caller could not tell "still unresolved" from
    "confirmed not applied".
    """
    csw = _Port(commit=True)
    prepared = _prepared(csw, "d3-unresolved")
    with pytest.raises(ActuationFailure):
        ControlActuator(csw=csw).apply(prepared)

    recovery = ControlActuator(csw=csw).reconcile_prepared(
        prepared, committed_behavior_state={"revision": 1},
        committed_projection_basis={"cfw_projection_action_index": 0})

    assert recovery.indeterminate is True, (
        "a failed authoritative re-read must be reported as unresolved")
    assert recovery.applied is False
    assert recovery.rollback_errors, "the read failure must be recorded"


def test_resolved_not_committed_is_not_flagged_indeterminate():
    """A readable authority that shows nothing committed is determinate."""
    csw = _Port(commit=False)
    prepared = _prepared(csw, "d3-notcommitted")
    with pytest.raises(ActuationFailure):
        ControlActuator(csw=csw).apply(prepared)

    recovery = ControlActuator(csw=csw).reconcile_prepared(
        prepared, committed_behavior_state={"revision": 1},
        committed_projection_basis={"cfw_projection_action_index": 0})

    assert recovery.indeterminate is False, (
        "a successfully resolved not-committed state is determinate")
    assert recovery.applied is False


def test_unresolved_recovery_propagates_to_the_host_abort():
    """The unresolved state must survive the restart boundary to the host."""
    csw = _Port(commit=True)
    prepared = _prepared(csw, "d3-host")
    with pytest.raises(ActuationFailure):
        ControlActuator(csw=csw).apply(prepared)

    class Client:
        def __init__(self):
            self.aborts = []

        async def commit_control_tick(self, receipt):
            return {"status": "committed",
                    "behavior_state": prepared["candidate_behavior_state"],
                    "host_action_index": 0,
                    "cfw_projection_action_index": 0, "boundary_sequence": 1}

        async def abort_control_tick(self, abort):
            self.aborts.append(abort)
            return {"status": "aborted"}

    client = Client()

    host = SimpleNamespace(
        identity=SimpleNamespace(scope="session"),
        _state=BehaviorState(revision=0),
        _cfw_projection_action_index=0,
        goal_set=GoalSetSnapshot(0, (GoalItemSnapshot("criterion"),)),
        _cfw_components=(), actuator=ControlActuator(csw=csw), csw=csw,
        client=client,
        _directives=lambda raw: tuple(dict(item) for item in raw),
        _csw_snapshot=lambda: (csw.behavioral_snapshot(),
                               csw.available_statework_ids()),
        _current_cfw_revision=lambda: 0,
        _accept_committed=lambda _r, _g: None,
        _fault=None, _notify_fault=lambda _f: None,
    )

    asyncio.run(HostReconciler(host)._recover_pending(prepared))

    assert client.aborts, "recovery must emit an abort"
    abort = client.aborts[0]
    assert abort.get("indeterminate") is True, (
        "the host must be told the outcome is unresolved, not not-committed")
    assert "UNRESOLVED" in (host._fault or ""), (
        "the unresolved state must be visible in the audit fault record")


def test_recovery_does_not_reissue_the_activation():
    """Reconciliation must never re-issue a consequential activation."""
    csw = _Port(commit=True)
    prepared = _prepared(csw, "d3-noreissue")
    with pytest.raises(ActuationFailure):
        ControlActuator(csw=csw).apply(prepared)
    calls_after_apply = csw.activate_calls

    ControlActuator(csw=csw).reconcile_prepared(
        prepared, committed_behavior_state={"revision": 1},
        committed_projection_basis={"cfw_projection_action_index": 0})

    assert csw.activate_calls == calls_after_apply, (
        "recovery must not issue a second activation")
