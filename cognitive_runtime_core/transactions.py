"""Harness-neutral two-phase transaction sequencing for host runtimes."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import inspect
from typing import Any, Callable, Literal


TransactionStage = Literal["committed", "apply_failed", "abort_failed",
                           "commit_failed"]
RuntimeTransactionStage = Literal[
    "unchanged", "already_committed", "already_aborted", "prepare_failed",
    "committed", "apply_failed", "abort_failed", "commit_failed",
]


@dataclass(frozen=True)
class PreparedTransactionOutcome:
    """Outcome of applying and finalizing one prepared host decision.

    A failed commit is reported as uncertain because an authority may have
    committed before its acknowledgement was lost. Callers must reconcile
    against their authority receipts before retrying.
    """

    stage: TransactionStage
    application: Any = None
    commit_receipt: Any = None
    abort_receipt: Any = None
    error: Exception | None = None
    recovery_error: Exception | None = None


@dataclass(frozen=True)
class RuntimeTransactionOutcome:
    """Outcome of the full prepare → apply → finalize control boundary."""

    stage: RuntimeTransactionStage
    preparation: Mapping[str, Any] | None = None
    application: Any = None
    commit_receipt: Any = None
    abort_receipt: Any = None
    error: Exception | None = None
    recovery_error: Exception | None = None


async def _invoke(callback: Callable[..., Any], *args: Any) -> Any:
    result = callback(*args)
    if inspect.isawaitable(result):
        return await result
    return result


async def execute_prepared_transaction(
        prepared: Any, *, apply: Callable[[Any], Any],
        commit: Callable[[Any, Any], Any], abort: Callable[[Any, Exception], Any],
        abort_on_apply_error: Callable[[Exception], bool] | None = None
        ) -> PreparedTransactionOutcome:
    """Run apply → commit, aborting recoverable apply failures.

    The callbacks own product-specific requests and authority receipts. This
    function owns only the shared transaction ordering and failure stages.
    """
    try:
        application = await _invoke(apply, prepared)
    except Exception as error:
        if abort_on_apply_error is not None and not abort_on_apply_error(error):
            raise
        try:
            abort_receipt = await _invoke(abort, prepared, error)
        except Exception as recovery_error:
            return PreparedTransactionOutcome(
                "abort_failed", error=error, recovery_error=recovery_error)
        return PreparedTransactionOutcome(
            "apply_failed", abort_receipt=abort_receipt, error=error)

    try:
        receipt = await _invoke(commit, prepared, application)
    except Exception as error:
        return PreparedTransactionOutcome(
            "commit_failed", application=application, error=error)
    return PreparedTransactionOutcome(
        "committed", application=application, commit_receipt=receipt)


async def coordinate_runtime_transaction(
        request: Any, *, prepare: Callable[[Any], Any],
        apply: Callable[[Any], Any], commit: Callable[[Any, Any], Any],
        abort: Callable[[Any, Exception], Any],
        abort_on_apply_error: Callable[[Exception], bool] | None = None
        ) -> RuntimeTransactionOutcome:
    """Own shared prepare/status/apply/commit/abort transaction flow.

    A control port returns a mapping with a common ``status`` field. The
    application adapter supplies request and receipt construction through the
    callbacks; this coordinator owns only lifecycle ordering and status flow.
    """
    try:
        preparation = await _invoke(prepare, request)
    except Exception as error:
        return RuntimeTransactionOutcome("prepare_failed", error=error)
    if not isinstance(preparation, Mapping):
        raise TypeError("runtime prepare callback must return a status mapping")

    status = preparation.get("status")
    if status == "unchanged":
        return RuntimeTransactionOutcome("unchanged", preparation=preparation)
    if status == "committed":
        return RuntimeTransactionOutcome("already_committed", preparation=preparation)
    if status == "aborted":
        return RuntimeTransactionOutcome("already_aborted", preparation=preparation)
    if status != "prepared":
        raise RuntimeError("runtime prepare callback returned an unknown transaction status")

    outcome = await execute_prepared_transaction(
        preparation, apply=apply, commit=commit, abort=abort,
        abort_on_apply_error=abort_on_apply_error)
    return RuntimeTransactionOutcome(
        outcome.stage, preparation=preparation, application=outcome.application,
        commit_receipt=outcome.commit_receipt, abort_receipt=outcome.abort_receipt,
        error=outcome.error, recovery_error=outcome.recovery_error)


__all__ = ["PreparedTransactionOutcome", "RuntimeTransactionOutcome",
           "RuntimeTransactionStage", "TransactionStage",
           "coordinate_runtime_transaction", "execute_prepared_transaction"]
