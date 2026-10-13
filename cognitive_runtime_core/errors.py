"""Failure contracts shared by harness-neutral runtime integrations."""
from __future__ import annotations

from collections.abc import Sequence


class ActuationFailure(RuntimeError):
    """An authority apply failed, with bounded rollback evidence.

    ``indeterminate`` distinguishes a *refusal* (nothing was committed, safe to
    retry) from a *committed-but-unverifiable* mutation (durable but unconfirmed,
    which the caller must reconcile from authoritative state rather than retry).
    """

    def __init__(self, message: str, *, applied_directive_ids: Sequence[str],
                 rollback_completed: bool, indeterminate: bool = False,
                 attempted: tuple[str, str] | None = None) -> None:
        super().__init__(message)
        self.applied_directive_ids = tuple(applied_directive_ids)
        self.rollback_completed = rollback_completed
        self.indeterminate = bool(indeterminate)
        # (statework_id, intervention_id) of an operation that was ATTEMPTED
        # but whose outcome is unknown. Separate from applied_directive_ids,
        # which retains its meaning of "confirmed applied", so recovery can
        # identify what to reconcile without asserting it succeeded.
        self.attempted = attempted


__all__ = ["ActuationFailure"]
