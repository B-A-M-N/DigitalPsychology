"""Per-harness binding registry for the shared runtime contracts."""
from __future__ import annotations

from collections.abc import Sequence
from types import MappingProxyType
from typing import Mapping

from .actions import HostActionBinding
from .completion import aggregate_completion
from .validator_reuse import ValidatorReuseCache
from .validators import HostValidatorBinding


class HostRuntimeBindings:
    """Validate and index one harness's neutral action and validator bindings.

    Harness adapters project their local contracts into these runtime types.
    The registry then gives lifecycle code one immutable lookup surface and a
    session-scoped reuse cache; it contains no transport or product behavior.
    """

    def __init__(self, *, actions: Sequence[HostActionBinding],
                 validators: Sequence[HostValidatorBinding] = ()) -> None:
        if (not isinstance(actions, (tuple, list)) or not 1 <= len(actions) <= 64
                or any(not isinstance(item, HostActionBinding) for item in actions)):
            raise ValueError("runtime actions must contain one to 64 host bindings")
        if (not isinstance(validators, (tuple, list)) or len(validators) > 64
                or any(not isinstance(item, HostValidatorBinding) for item in validators)):
            raise ValueError("runtime validators must contain at most 64 host bindings")
        action_map = {item.binding_id: item for item in actions}
        validator_map = {item.validator_id: item for item in validators}
        if len(action_map) != len(actions):
            raise ValueError("runtime action binding IDs must be unique")
        if len(validator_map) != len(validators):
            raise ValueError("runtime validator IDs must be unique")
        unknown_actions = {
            item.action_binding_id for item in validators
            if item.action_binding_id not in action_map
        }
        if unknown_actions:
            raise ValueError(
                f"runtime validators reference unknown actions: {sorted(unknown_actions)}")

        self.action_bindings_by_id: Mapping[str, HostActionBinding] = MappingProxyType(
            action_map)
        self.validator_bindings_by_id: Mapping[str, HostValidatorBinding] = MappingProxyType(
            validator_map)
        self.validator_reuse_cache = ValidatorReuseCache()

    @staticmethod
    def aggregate_completion(*, required_criterion_ids: Sequence[str],
                             expected_validator_ids: Sequence[str],
                             validator_outcomes: Sequence[object]):
        """Aggregate evidence without granting the harness final authority."""
        return aggregate_completion(
            required_criterion_ids=required_criterion_ids,
            expected_validator_ids=expected_validator_ids,
            validator_outcomes=validator_outcomes,
        )


__all__ = ["HostRuntimeBindings"]
