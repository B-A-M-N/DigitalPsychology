"""Neutral host validator binding contracts for the shared runtime."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .actions import (
    _validate_id,
    freeze_json_object,
    freeze_json_schema,
    json_object_value,
    json_schema_value,
)

_MAX_REFERENCES = 128


def _normalize_ids(values: Any, *, label: str) -> tuple[str, ...]:
    if (not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray))
            or len(values) > _MAX_REFERENCES):
        raise ValueError(f"{label} must be a bounded sequence of identifiers")
    result = tuple(values)
    for value in result:
        _validate_id(value, label)
    if len(result) != len(set(result)):
        raise ValueError(f"{label} must be unique")
    return result


@dataclass(frozen=True)
class HostValidatorBinding:
    """Validator arguments and result contract attached to a host action."""

    validator_id: str
    action_binding_id: str
    arguments: Mapping[str, Any]
    criterion_ids: tuple[str, ...]
    result_schema: Mapping[str, Any]
    covered_boundary_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_id(self.validator_id, "validator_id")
        _validate_id(self.action_binding_id, "action_binding_id")
        arguments = freeze_json_object(self.arguments, label="validator arguments")
        result_schema = freeze_json_schema(
            self.result_schema, label="validator result_schema")
        criteria = _normalize_ids(self.criterion_ids, label="criterion_ids")
        boundaries = _normalize_ids(
            self.covered_boundary_ids, label="covered_boundary_ids")
        object.__setattr__(self, "arguments", arguments)
        object.__setattr__(self, "criterion_ids", criteria)
        object.__setattr__(self, "result_schema", result_schema)
        object.__setattr__(self, "covered_boundary_ids", boundaries)

    def arguments_value(self) -> dict[str, Any]:
        """Return a detached JSON-safe copy of validator arguments."""
        return json_object_value(self.arguments)

    def result_schema_value(self) -> dict[str, Any]:
        """Return a detached JSON-safe copy of the expected result schema."""
        return json_schema_value(self.result_schema)
