"""Restart/replay idempotency for StateWork activation (directive §12 scenario 13).

A prepared decision that was already applied before a host restart must not
re-apply on recovery. Re-applying an activation would either duplicate a
consequential transition or, worse, re-lease a StateWork that another owner has
since taken.

These tests drive the real ``ControlActuator.apply`` and the real
``HostReconciler`` recovery path against a CSW port that records every mutation,
so a duplicate transition is visible as a duplicated event.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from lib.behavior_state import (
    BehaviorIntervention,
    BehaviorState,
    GoalItemSnapshot,
    GoalSetSnapshot,
    InterventionTarget,
)
from tests.test_csw_dependency_retention import _DependencySwitchCSWPort

ACTIVATION_DIRECTIVE = {
    "directive_id": "activate-getter", "operation": "prefer_statework",
    "target": "getter", "value": "preferred",
    "intervention_id": "intervention-getter", "expires_at_action": 6,
}


def _prepared(csw):
    """A prepared activation decision whose owning intervention is present."""
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
    return {
        "status": "prepared", "tick_id": "replay-tick",
        "decision_id": "decision-replay", "basis_digest": "f" * 64,
        "candidate_behavior_state": state.to_dict(),
        "directives": [ACTIVATION_DIRECTIVE], "cfw_directives": [],
        "csw_directives": [ACTIVATION_DIRECTIVE],
        "basis": {
            "host_action_index": 0, "cfw_projection_action_index": 0,
            "cfw_lifecycle_revision": 0,
            "csw_selection_revision": before.revision,
            "csw_snapshot_id": before.snapshot_id,
            "csw_active_stateworks": list(before.active_stateworks),
            "csw_dependency_retention": {
                key: list(value)
                for key, value in before.dependency_retention.items()},
        },
    }


def _actuator(csw):
    from lib.runtime.actuator import ControlActuator
    return ControlActuator(csw=csw)


def _conformant_port(csw):
    """Attach the contract-required ``activation_matches`` to the shared fake.

    ``activation_matches`` is a REQUIRED method of the CSW port contract
    (``lib/runtime/contracts.py``). The actuator deliberately fails closed when
    it is absent, so a conformant port must expose it before recovery can
    distinguish "already applied" from "never applied".
    """

    def activation_matches(statework_id, *, intervention_id):
        owner = csw.owners.get(statework_id) or {}
        return bool(owner.get("owner") == "digitalpsychology"
                    and owner.get("intervention_id") == intervention_id)

    csw.activation_matches = activation_matches
    return csw


def _apply(csw, prepared):
    return _actuator(_conformant_port(csw)).apply(prepared)


def _host(csw, prepared):
    """A minimal trusted host for the reconciler's pending-decision recovery."""
    from lib.host.reconciler import HostReconciler

    class Client:
        def __init__(self):
            self.receipts = []

        async def commit_control_tick(self, receipt):
            self.receipts.append(receipt)
            return {"status": "committed",
                    "behavior_state": prepared["candidate_behavior_state"],
                    "host_action_index": 0,
                    "cfw_projection_action_index": 0, "boundary_sequence": 1}

        async def abort_control_tick(self, abort):
            self.aborts = getattr(self, "aborts", [])
            self.aborts.append(abort)
            return {"status": "aborted"}

    host = SimpleNamespace(
        identity=SimpleNamespace(scope="session"),
        _state=BehaviorState(revision=0),
        _cfw_projection_action_index=0,
        goal_set=GoalSetSnapshot(0, (GoalItemSnapshot("criterion"),)),
        _cfw_components=(),
        actuator=_actuator(_conformant_port(csw)), csw=csw, client=Client(),
        _directives=lambda raw: tuple(dict(item) for item in raw),
        _csw_snapshot=lambda: (csw.behavioral_snapshot(),
                               csw.available_statework_ids()),
        _current_cfw_revision=lambda: 0,
        _accept_committed=lambda _result, _goals: None,
    )
    return HostReconciler(host), Client


def test_reapplying_against_a_stale_basis_is_rejected_not_duplicated():
    """Re-applying a prepared decision against a moved CSW basis is refused.

    A naive replay would re-issue the activation against the old basis. The
    actuator must instead detect that the live CSW snapshot no longer matches
    the decision basis and refuse, so no duplicate consequential transition can
    be issued from a stale replay.
    """
    csw = _DependencySwitchCSWPort()
    prepared = _prepared(csw)

    _apply(csw, prepared)
    events_after_first = list(csw.events)
    assert events_after_first, "precondition: the first apply mutates state"

    with pytest.raises(RuntimeError, match="no longer matches"):
        _apply(csw, prepared)

    assert csw.events == events_after_first, (
        f"replay duplicated transitions: {csw.events[len(events_after_first):]}")


def test_restart_recovery_of_a_committed_decision_is_not_reapplied():
    """A decision CSW reports as already applied reconciles as committed.

    This is the real restart path: the host committed, crashed before recording
    it, and on restart asks the reconciler what actually happened. The answer
    must be "already applied" rather than a second application.
    """
    csw = _DependencySwitchCSWPort()
    prepared = _prepared(csw)
    _apply(csw, prepared)
    events_after_apply = list(csw.events)
    revision_after_apply = csw.revision

    reconciler, _client = _host(csw, prepared)
    recovered = asyncio.run(reconciler._recover_pending(prepared))

    assert csw.events == events_after_apply, (
        "restart recovery re-applied transitions that had already committed")
    assert csw.revision == revision_after_apply, (
        "restart recovery mutated CSW state for an already-committed decision")
    assert recovered is False, (
        "an already-applied decision must not be reported as pending")


def test_replay_cannot_steal_a_statework_owned_by_another_owner():
    """A replayed activation must not overwrite another owner's lease.

    If a different owner took the StateWork after the original activation,
    replaying the stale directive must not reclaim it.
    """
    csw = _DependencySwitchCSWPort()
    prepared = _prepared(csw)
    _apply(csw, prepared)
    events_after_apply = list(csw.events)

    csw.owners["getter"] = {"owner": "operator", "intervention_id": "other"}

    with pytest.raises(RuntimeError):
        _apply(csw, prepared)

    assert csw.events == events_after_apply, (
        "a replayed activation re-applied a transition it did not own")
    assert csw.owners["getter"]["owner"] == "operator", (
        "replay overwrote another owner's StateWork lease")
