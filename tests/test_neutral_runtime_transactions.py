"""Exercise the shared transaction sequence without product adapters."""
from __future__ import annotations

import asyncio

import pytest

from cognitive_runtime_core.transactions import (
    coordinate_runtime_transaction,
    execute_prepared_transaction,
)


def test_prepared_transaction_applies_then_commits():
    calls: list[str] = []

    def apply(prepared):
        calls.append(f"apply:{prepared}")
        return "application"

    async def commit(prepared, application):
        calls.append(f"commit:{prepared}:{application}")
        return "receipt"

    async def abort(_prepared, _error):
        calls.append("abort")
        return "abort-receipt"

    outcome = asyncio.run(execute_prepared_transaction(
        "decision", apply=apply, commit=commit, abort=abort))

    assert calls == ["apply:decision", "commit:decision:application"]
    assert outcome.stage == "committed"
    assert outcome.application == "application"
    assert outcome.commit_receipt == "receipt"


def test_recoverable_apply_failure_aborts_prepared_transaction():
    failure = ValueError("apply failed")
    calls: list[str] = []

    def apply(_prepared):
        calls.append("apply")
        raise failure

    def abort(prepared, error):
        calls.append(f"abort:{prepared}:{error is failure}")
        return "abort-receipt"

    outcome = asyncio.run(execute_prepared_transaction(
        "decision", apply=apply, commit=lambda *_: None, abort=abort))

    assert calls == ["apply", "abort:decision:True"]
    assert outcome.stage == "apply_failed"
    assert outcome.error is failure
    assert outcome.abort_receipt == "abort-receipt"


def test_abort_failure_is_reported_for_reconciliation():
    apply_error = ValueError("apply failed")
    abort_error = ConnectionError("abort acknowledgement lost")

    def apply(_prepared):
        raise apply_error

    def abort(_prepared, _error):
        raise abort_error

    outcome = asyncio.run(execute_prepared_transaction(
        "decision", apply=apply, commit=lambda *_: None, abort=abort))

    assert outcome.stage == "abort_failed"
    assert outcome.error is apply_error
    assert outcome.recovery_error is abort_error


def test_commit_failure_is_uncertain_until_authority_reconciliation():
    failure = ConnectionError("commit acknowledgement lost")

    def commit(_prepared, _application):
        raise failure

    outcome = asyncio.run(execute_prepared_transaction(
        "decision", apply=lambda _prepared: "application", commit=commit,
        abort=lambda *_: pytest.fail("commit failure must not trigger abort")))

    assert outcome.stage == "commit_failed"
    assert outcome.error is failure
    assert outcome.application == "application"


def test_nonrecoverable_apply_failure_propagates_without_abort():
    failure = RuntimeError("programming error")

    def apply(_prepared):
        raise failure

    with pytest.raises(RuntimeError, match="programming error") as caught:
        asyncio.run(execute_prepared_transaction(
            "decision", apply=apply, commit=lambda *_: None,
            abort=lambda *_: pytest.fail("unexpected abort"),
            abort_on_apply_error=lambda error: isinstance(error, ValueError)))

    assert caught.value is failure


def test_runtime_coordinator_owns_prepare_apply_commit_order():
    calls: list[str] = []

    async def prepare(request):
        calls.append(f"prepare:{request}")
        return {"status": "prepared", "decision": "decision"}

    def apply(preparation):
        calls.append(f"apply:{preparation['decision']}")
        return "application"

    def commit(preparation, application):
        calls.append(f"commit:{preparation['decision']}:{application}")
        return "receipt"

    def abort(_preparation, _error):
        calls.append("abort")
        return "abort-receipt"

    outcome = asyncio.run(coordinate_runtime_transaction(
        {"request": "tick"}, prepare=prepare, apply=apply, commit=commit,
        abort=abort))

    assert calls == ["prepare:{'request': 'tick'}", "apply:decision",
                     "commit:decision:application"]
    assert outcome.stage == "committed"
    assert outcome.preparation == {"status": "prepared", "decision": "decision"}
    assert outcome.application == "application"
    assert outcome.commit_receipt == "receipt"


def test_runtime_coordinator_preserves_prepare_outcomes_without_applying():
    calls: list[str] = []

    async def prepare(_request):
        calls.append("prepare")
        return {"status": "unchanged", "reason": "same input"}

    outcome = asyncio.run(coordinate_runtime_transaction(
        None, prepare=prepare,
        apply=lambda *_: pytest.fail("unchanged transaction must not apply"),
        commit=lambda *_: pytest.fail("unchanged transaction must not commit"),
        abort=lambda *_: pytest.fail("unchanged transaction must not abort")))

    assert calls == ["prepare"]
    assert outcome.stage == "unchanged"
    assert outcome.preparation == {"status": "unchanged", "reason": "same input"}


def test_runtime_coordinator_returns_prepare_failure_for_host_reconciliation():
    failure = ConnectionError("prepare acknowledgement lost")

    async def prepare(_request):
        raise failure

    outcome = asyncio.run(coordinate_runtime_transaction(
        None, prepare=prepare,
        apply=lambda *_: pytest.fail("failed prepare must not apply"),
        commit=lambda *_: pytest.fail("failed prepare must not commit"),
        abort=lambda *_: pytest.fail("failed prepare must not abort")))

    assert outcome.stage == "prepare_failed"
    assert outcome.error is failure


@pytest.mark.parametrize(
    ("status", "expected_stage"),
    [("committed", "already_committed"), ("aborted", "already_aborted")],
)
def test_runtime_coordinator_does_not_reapply_terminal_prepare_status(
        status, expected_stage):
    outcome = asyncio.run(coordinate_runtime_transaction(
        None, prepare=lambda _request: {"status": status},
        apply=lambda *_: pytest.fail("terminal transaction must not apply"),
        commit=lambda *_: pytest.fail("terminal transaction must not commit"),
        abort=lambda *_: pytest.fail("terminal transaction must not abort")))

    assert outcome.stage == expected_stage
    assert outcome.preparation == {"status": status}
