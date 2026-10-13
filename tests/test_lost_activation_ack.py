"""D2: a lost activation acknowledgment must be classified indeterminate.

`ControlActuator.apply()` treats the return value of `activate_statework()` as the
confirmation. If the authority commits and the acknowledgment is lost, the
exception propagates from the activate call. A failed acknowledgment does NOT
establish a failed mutation, so this must not be reported as a clean refusal.

The attempted operation's identity is carried separately from
`applied_directive_ids`, which retains its meaning of "confirmed applied", so
recovery can identify what to reconcile without asserting it succeeded.
"""
from __future__ import annotations

import pytest

from cognitive_runtime_core.errors import ActuationFailure
from cognitive_runtime_core.integrations.triad.actuator import GuaranteedNoMutation
from lib.behavior_state import (
    BehaviorIntervention,
    BehaviorState,
    CSWBehaviorSnapshot,
    InterventionTarget,
    StateWorkOption,
)
from lib.runtime.actuator import ControlActuator


def _snapshot(revision, active, eligible):
    return CSWBehaviorSnapshot(
        revision=revision, active_stateworks=active,
        eligible_stateworks=tuple(
            StateWorkOption(i, behavior_tags=()) for i in eligible),
        snapshot_id="1" * 64, registry_hash="2" * 64)


class _LostAckPort:
    """Commits the activation, then drops the acknowledgment."""

    def __init__(self):
        self.revision = 7
        self.active = ()
        self.eligible = ("getter", "gitter")
        self.owners = {}
        self.checkpoints = {}
        self.activate_calls = 0
        self.deactivate_calls = 0

    def available_statework_ids(self):
        return self.eligible

    def activation_matches(self, statework_id, *, intervention_id):
        owner = self.owners.get(statework_id) or {}
        return bool(owner.get("owner") == "digitalpsychology"
                    and owner.get("intervention_id") == intervention_id)

    def behavioral_snapshot(self, candidate_statework_ids=None):
        return _snapshot(self.revision, self.active, self.eligible)

    def checkpoint_selection(self, *, tick_id, expected_selection_revision):
        self.checkpoints[tick_id] = (self.revision, dict(self.owners),
                                     tuple(self.active))

    def restore_selection_checkpoint(self, *, tick_id, expected_selection_revision):
        row = self.checkpoints.pop(tick_id, None)
        if row is None:
            return None
        self.revision, self.owners, self.active = row[0], row[1], row[2]
        self.revision += 1
        return _snapshot(self.revision, self.active, self.eligible)

    def finalize_selection_checkpoint(self, *, tick_id):
        self.checkpoints.pop(tick_id, None)

    def activate_statework(self, statework_id, *, expected_selection_revision,
                           candidate_statework_ids, intervention_id,
                           expires_at_action):
        # The authority COMMITS the activation...
        self.activate_calls += 1
        self.active = tuple(sorted(set(self.active) | {statework_id}))
        self.owners[statework_id] = {"owner": "digitalpsychology",
                                     "intervention_id": intervention_id}
        self.revision += 1
        # ...and then the acknowledgment is lost.
        raise TimeoutError("activation acknowledgment lost after commit")

    def deactivate_statework(self, statework_id, *, expected_selection_revision,
                             candidate_statework_ids, intervention_id):
        self.deactivate_calls += 1
        self.owners.pop(statework_id, None)
        self.active = tuple(i for i in self.active if i != statework_id)
        self.revision += 1
        return _snapshot(self.revision, self.active, self.eligible)


class _RefusePort(_LostAckPort):
    """Refuses before any mutation: the target is not CSW-eligible."""

    def activate_statework(self, statework_id, *, expected_selection_revision,
                           candidate_statework_ids, intervention_id,
                           expires_at_action):
        self.activate_calls += 1
        # Guaranteed pre-commit refusal: nothing was mutated.
        raise GuaranteedNoMutation(
            "CSW activation target is outside current eligibility")


def _prepared(csw, tick="lostack-tick"):
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
        "decision_id": "decision-lostack", "basis_digest": "f" * 64,
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


# ------------------------------------------------ 1. authority invoked once
def test_authority_is_invoked_exactly_once():
    csw = _LostAckPort()
    with pytest.raises(ActuationFailure):
        ControlActuator(csw=csw).apply(_prepared(csw))
    assert csw.activate_calls == 1, (
        "a lost acknowledgment must not trigger a second activation attempt")


# ------------------------------------------------ 2. classified indeterminate
def test_lost_acknowledgment_is_classified_indeterminate():
    csw = _LostAckPort()
    with pytest.raises(ActuationFailure) as failure:
        ControlActuator(csw=csw).apply(_prepared(csw))
    assert failure.value.indeterminate is True, (
        "a failed acknowledgment does not establish a failed mutation")


# ------------------------------------------------ 3. no false rollback claim
def test_no_false_rollback_success_is_asserted():
    csw = _LostAckPort()
    with pytest.raises(ActuationFailure) as failure:
        ControlActuator(csw=csw).apply(_prepared(csw))
    assert failure.value.rollback_completed is False, (
        "an indeterminate outcome must not claim a completed rollback")


# ------------------------------------------------ 4. no duplicate activation
def test_no_duplicate_activation_is_performed():
    csw = _LostAckPort()
    with pytest.raises(ActuationFailure):
        ControlActuator(csw=csw).apply(_prepared(csw))
    assert csw.activate_calls == 1
    # The checkpoint restore is the only compensating action, and it must not
    # have re-issued an activation.
    assert csw.active.count("getter") <= 1


# ------------------------------------------------ attempted identity preserved
def test_attempted_operation_identity_is_preserved_for_recovery():
    """Recovery must be able to identify what to reconcile.

    `applied_directive_ids` keeps meaning "confirmed applied", so the attempted
    operation is carried separately. Without it, the system could avoid an
    unsafe retry but lose the information needed for reliable recovery.
    """
    csw = _LostAckPort()
    with pytest.raises(ActuationFailure) as failure:
        ControlActuator(csw=csw).apply(_prepared(csw))

    # The attempted operation is identified...
    assert failure.value.attempted == ("getter", "intervention-getter")
    # ...and is NOT conflated with confirmed application.
    assert failure.value.applied_directive_ids == (), (
        "an unconfirmed activation must not be reported as applied")


# ------------------------------------------------ 6. genuine refusal path
def test_genuine_pre_commit_refusal_stays_determinate():
    """A guaranteed pre-commit rejection keeps its existing determinate path."""
    csw = _RefusePort()
    with pytest.raises(ActuationFailure) as failure:
        ControlActuator(csw=csw).apply(_prepared(csw, tick="refuse-tick"))

    assert csw.activate_calls == 1
    assert failure.value.indeterminate is False, (
        "a guaranteed pre-commit refusal must not be classified indeterminate")
    assert failure.value.rollback_completed is True, (
        "nothing was mutated, so there is nothing to roll back")
    assert failure.value.applied_directive_ids == ()


# ------------------------------------------------ reconciliation
def test_reconciliation_can_establish_authoritative_state_after_lost_ack():
    """A restart must be able to resolve the outcome from authority state."""
    import asyncio
    from types import SimpleNamespace

    from lib.behavior_state import GoalItemSnapshot, GoalSetSnapshot
    from lib.host.reconciler import HostReconciler

    csw = _LostAckPort()
    prepared = _prepared(csw, tick="reconcile-lostack")
    with pytest.raises(ActuationFailure):
        ControlActuator(csw=csw).apply(prepared)

    class Client:
        def __init__(self):
            self.receipts = []
            self.aborts = []

        async def commit_control_tick(self, receipt):
            self.receipts.append(receipt)
            return {"status": "committed",
                    "behavior_state": prepared["candidate_behavior_state"],
                    "host_action_index": 0,
                    "cfw_projection_action_index": 0, "boundary_sequence": 1}

        async def abort_control_tick(self, abort):
            self.aborts.append(abort)
            return {"status": "aborted"}

    host = SimpleNamespace(
        identity=SimpleNamespace(scope="session"),
        _state=BehaviorState(revision=0),
        _cfw_projection_action_index=0,
        goal_set=GoalSetSnapshot(0, (GoalItemSnapshot("criterion"),)),
        _cfw_components=(),
        actuator=ControlActuator(csw=csw), csw=csw, client=Client(),
        _directives=lambda raw: tuple(dict(item) for item in raw),
        _csw_snapshot=lambda: (csw.behavioral_snapshot(),
                               csw.available_statework_ids()),
        _current_cfw_revision=lambda: 0,
        _accept_committed=lambda _r, _g: None,
    )

    # Recovery re-reads authoritative state rather than re-issuing anything.
    asyncio.run(HostReconciler(host)._recover_pending(prepared))

    assert csw.activate_calls == 1, (
        "reconciliation must not re-issue the activation")
