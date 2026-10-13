"""Confirmation semantics for StateWork activation (directive §12 scenario 9).

``ControlActuator`` confirmed an activation by re-reading the CSW snapshot. When
that verification read failed *after* the activation had committed, the failure
surfaced with the same shape as a refusal, and the rollback then also ran on the
same degraded read path.

That made three outcomes indistinguishable from the exception alone:

  * rejected before mutation      -> safe to retry
  * committed and confirmed       -> done
  * committed but unreadable      -> indeterminate; retrying risks duplication

The fix distinguishes the third case via ``ActuationFailure.indeterminate`` and
refuses to roll back or implicitly authorise a retry.
"""
from __future__ import annotations

import pytest

from lib.behavior_state import (
    BehaviorIntervention, BehaviorState, InterventionTarget, StateWorkOption,
)
from lib.runtime.actuator import ControlActuator

from cognitive_runtime_core.errors import ActuationFailure


class _Snapshot:
    def __init__(self, revision, active, eligible):
        self.revision = revision
        self.active_stateworks = active
        self.eligible_stateworks = tuple(
            StateWorkOption(item, behavior_tags=()) for item in eligible)
        self.snapshot_id = "s" * 64
        self.registry_hash = "r" * 64
        self.dependency_retention = {}

    def __eq__(self, other):
        return (isinstance(other, _Snapshot)
                and self.revision == other.revision
                and self.active_stateworks == other.active_stateworks)

    def __hash__(self):
        return hash((self.revision, self.active_stateworks))


class _CommitThenFailReadPort:
    """Commits the activation, then fails every subsequent verification read."""

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
        if self.activate_calls >= 1:
            raise TimeoutError("CSW snapshot read timed out")
        return _Snapshot(self.revision, self.active, self.eligible)

    def checkpoint_selection(self, *, tick_id, expected_selection_revision):
        assert expected_selection_revision == self.revision
        self.checkpoints[tick_id] = (self.revision, dict(self.owners),
                                     tuple(self.active))

    def restore_selection_checkpoint(self, *, tick_id, expected_selection_revision):
        row = self.checkpoints.pop(tick_id, None)
        if row is None:
            return None
        self.revision, self.owners, self.active = row[0], row[1], row[2]
        self.revision += 1
        return _Snapshot(self.revision, self.active, self.eligible)

    def finalize_selection_checkpoint(self, *, tick_id):
        self.checkpoints.pop(tick_id, None)

    def activate_statework(self, statework_id, *, expected_selection_revision,
                           candidate_statework_ids, intervention_id,
                           expires_at_action):
        assert expected_selection_revision == self.revision
        self.activate_calls += 1
        self.active = tuple(sorted(set(self.active) | {statework_id}))
        self.owners[statework_id] = {"owner": "digitalpsychology",
                                     "intervention_id": intervention_id}
        self.revision += 1
        return _Snapshot(self.revision, self.active, self.eligible)

    def deactivate_statework(self, statework_id, *, expected_selection_revision,
                             candidate_statework_ids, intervention_id):
        self.deactivate_calls += 1
        self.owners.pop(statework_id, None)
        self.active = tuple(i for i in self.active if i != statework_id)
        self.revision += 1
        return _Snapshot(self.revision, self.active, self.eligible)


def _prepared(csw):
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
    directive = {"directive_id": "activate-getter", "operation": "prefer_statework",
                 "target": "getter", "value": "preferred",
                 "intervention_id": "intervention-getter", "expires_at_action": 6}
    return {
        "status": "prepared", "tick_id": "confirm-tick",
        "decision_id": "decision-confirm", "basis_digest": "f" * 64,
        "candidate_behavior_state": state.to_dict(),
        "directives": [directive], "cfw_directives": [],
        "csw_directives": [directive],
        "basis": {
            "host_action_index": 0, "cfw_projection_action_index": 0,
            "cfw_lifecycle_revision": 0,
            "csw_selection_revision": before.revision,
            "csw_snapshot_id": before.snapshot_id,
            "csw_active_stateworks": list(before.active_stateworks),
            "csw_dependency_retention": {},
        },
    }


def test_committed_but_unverifiable_activation_is_reported_indeterminate():
    """A failed verification read after commit must not look like a refusal.

    The activation is durable, so the caller must be told the outcome is
    indeterminate rather than being handed a message asserting no mutation
    occurred.
    """
    csw = _CommitThenFailReadPort()
    prepared = _prepared(csw)

    with pytest.raises(ActuationFailure) as failure:
        ControlActuator(csw=csw).apply(prepared)

    # The activation really did commit.
    assert csw.activate_calls == 1
    assert "getter" in csw.active

    # And it is now reported as indeterminate, not as a clean refusal.
    assert failure.value.indeterminate is True, (
        "a committed-but-unverifiable activation must be classified "
        "indeterminate, not as a refusal")
    assert failure.value.rollback_completed is False
    assert failure.value.applied_directive_ids == ("activate-getter",)


def test_indeterminate_activation_is_not_rolled_back_or_retried():
    """Rolling back or retrying on a degraded read path risks duplication.

    The activation committed, so a blind rollback attempt would issue a second
    consequential transition against an authority that cannot be read.
    """
    csw = _CommitThenFailReadPort()
    prepared = _prepared(csw)

    with pytest.raises(ActuationFailure):
        ControlActuator(csw=csw).apply(prepared)

    # No deactivation was attempted on the degraded path.
    assert csw.deactivate_calls == 0, (
        "an indeterminate outcome must not trigger a compensating rollback")
    # Exactly one activation, never a duplicate.
    assert csw.activate_calls == 1
    assert csw.active.count("getter") == 1 if isinstance(csw.active, tuple) else True


def test_genuine_refusal_is_not_classified_indeterminate():
    """A real rejection (ineligible target) stays a refusal, safe to retry.

    This guards the other side of the distinction: the new classification must
    not swallow genuine refusals into the indeterminate bucket.
    """
    csw = _CommitThenFailReadPort()
    prepared = _prepared(csw)
    # Point the directive at a StateWork outside the eligible set.
    prepared["csw_directives"][0]["target"] = "unregistered"
    prepared["directives"] = list(prepared["csw_directives"])

    with pytest.raises(ActuationFailure) as failure:
        ControlActuator(csw=csw).apply(prepared)

    assert csw.activate_calls == 0, "an ineligible target must not be activated"
    assert failure.value.indeterminate is False, (
        "a genuine refusal must not be classified indeterminate")
    assert failure.value.rollback_completed is True, (
        "nothing committed, so there is nothing to roll back")
