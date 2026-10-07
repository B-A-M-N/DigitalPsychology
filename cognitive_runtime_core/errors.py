"""Failure contracts shared by harness-neutral runtime integrations."""
from __future__ import annotations

from collections.abc import Sequence


class ActuationFailure(RuntimeError):
    """An authority apply failed, with bounded rollback evidence."""

    def __init__(self, message: str, *, applied_directive_ids: Sequence[str],
                 rollback_completed: bool) -> None:
        super().__init__(message)
        self.applied_directive_ids = tuple(applied_directive_ids)
        self.rollback_completed = rollback_completed


__all__ = ["ActuationFailure"]
