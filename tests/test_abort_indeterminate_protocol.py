"""D1: `indeterminate` must survive from the actuator to the host abort request.

Before the fix, `abort_prepared` forwarded only applied_directive_ids,
rollback_completed, and failure_class. Both "committed but unverifiable" and
"refused after partial apply" therefore produced byte-identical abort requests,
making the actuator's classification unobservable by its only consumer.

`failure_class` is derived from `type(error.__cause__ or error).__name__`, i.e.
free text. It must NOT be used to infer indeterminacy.
"""
from __future__ import annotations

import pytest

from cognitive_runtime_core.errors import ActuationFailure
from lib.behavior_state import CSWBehaviorSnapshot
from lib.control_protocol import PROTOCOL_VERSION, ControlAbortRequest


def _snapshot() -> CSWBehaviorSnapshot:
    return CSWBehaviorSnapshot(revision=8, snapshot_id="1" * 64,
                               registry_hash="2" * 64)


def _indeterminate_failure() -> ActuationFailure:
    """Exactly what the actuator now raises for a committed-but-unreadable activation."""
    return ActuationFailure(
        "CSW verification read failed after committing 1 directive(s): timeout",
        applied_directive_ids=("activate-getter",),
        rollback_completed=False, indeterminate=True)


def test_indeterminate_failure_can_be_expressed_in_the_abort_protocol():
    """The abort request must have a field capable of carrying indeterminacy."""
    fields = set(ControlAbortRequest.__dataclass_fields__)
    assert any("indetermin" in f for f in fields), (
        "ControlAbortRequest cannot represent an indeterminate outcome, so the "
        "host cannot distinguish it from a refusal")


def test_indeterminate_failure_produces_indeterminate_abort_request():
    """Forwarding an indeterminate ActuationFailure must set the flag."""
    err = _indeterminate_failure()
    abort = ControlAbortRequest(
        control_session_id="session", tick_id="t", decision_id="d",
        basis_digest="f" * 64,
        applied_directive_ids=err.applied_directive_ids,
        rollback_completed=err.rollback_completed,
        failure_class=type(err.__cause__ or err).__name__,
        indeterminate=err.indeterminate,
        cfw_lifecycle_revision=0, csw_snapshot=_snapshot())

    assert abort.indeterminate is True
    # The pre-existing semantics are preserved independently.
    assert abort.applied_directive_ids == ("activate-getter",)
    assert abort.rollback_completed is False


def test_genuine_refusal_remains_determinate():
    """A real pre-mutation rejection must not be classified indeterminate."""
    refusal = ActuationFailure(
        "CSW activation target is outside current eligibility",
        applied_directive_ids=(), rollback_completed=True, indeterminate=False)

    abort = ControlAbortRequest(
        control_session_id="session", tick_id="t", decision_id="d",
        basis_digest="f" * 64,
        applied_directive_ids=refusal.applied_directive_ids,
        rollback_completed=refusal.rollback_completed,
        failure_class="ActuationFailure",
        indeterminate=refusal.indeterminate,
        cfw_lifecycle_revision=0, csw_snapshot=_snapshot())

    assert abort.indeterminate is False
    assert abort.rollback_completed is True


def test_rollback_status_is_independent_of_indeterminacy():
    """The two facts must not be conflated in either direction."""
    # Indeterminate does not imply rollback state, and vice versa.
    committed_unreadable = ControlAbortRequest(
        control_session_id="s", tick_id="t", decision_id="d",
        basis_digest="f" * 64, applied_directive_ids=("a",),
        rollback_completed=False, failure_class="TimeoutError",
        indeterminate=True, cfw_lifecycle_revision=0, csw_snapshot=_snapshot())
    assert committed_unreadable.indeterminate is True
    assert committed_unreadable.rollback_completed is False

    determinate_failed_rollback = ControlAbortRequest(
        control_session_id="s", tick_id="t", decision_id="d",
        basis_digest="f" * 64, applied_directive_ids=("a",),
        rollback_completed=False, failure_class="RuntimeError",
        indeterminate=False, cfw_lifecycle_revision=0, csw_snapshot=_snapshot())
    assert determinate_failed_rollback.indeterminate is False
    assert determinate_failed_rollback.rollback_completed is False


def test_serialization_round_trip_preserves_indeterminacy():
    """The flag must survive to_dict/from_mapping across the host boundary."""
    abort = ControlAbortRequest(
        control_session_id="s", tick_id="t", decision_id="d",
        basis_digest="f" * 64, applied_directive_ids=("activate-getter",),
        rollback_completed=False, failure_class="TimeoutError",
        indeterminate=True, cfw_lifecycle_revision=0, csw_snapshot=_snapshot())

    payload = abort.to_dict()
    assert payload["indeterminate"] is True
    assert payload["protocol_version"] == PROTOCOL_VERSION

    restored = ControlAbortRequest.from_mapping(payload)
    assert restored.indeterminate is True
    assert restored.applied_directive_ids == ("activate-getter",)
    assert restored.rollback_completed is False


def test_indeterminate_defaults_to_false_for_explicit_constructors():
    """Existing constructor call sites must keep working unchanged."""
    abort = ControlAbortRequest(
        control_session_id="s", tick_id="t", decision_id="d",
        basis_digest="f" * 64, applied_directive_ids=(),
        rollback_completed=True, failure_class="ActuationFailure",
        cfw_lifecycle_revision=0, csw_snapshot=_snapshot())
    assert abort.indeterminate is False


def test_older_client_payload_without_the_field_is_rejected_not_assumed():
    """An absent field must not be silently read as verified certainty.

    The protocol uses a strict field-set check, so a payload from an older client
    that omits `indeterminate` is rejected rather than defaulted. That is the
    correct behaviour: unknown meaning must not be represented as certainty.
    """
    abort = ControlAbortRequest(
        control_session_id="s", tick_id="t", decision_id="d",
        basis_digest="f" * 64, applied_directive_ids=(),
        rollback_completed=True, failure_class="ActuationFailure",
        indeterminate=False, cfw_lifecycle_revision=0, csw_snapshot=_snapshot())
    legacy = abort.to_dict()
    del legacy["indeterminate"]

    with pytest.raises(ValueError):
        ControlAbortRequest.from_mapping(legacy)
