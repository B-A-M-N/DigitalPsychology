"""Neutral host action binding contracts for the shared runtime."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
import math
from types import MappingProxyType
from typing import Any, Callable, TypeAlias


# A host descriptor is represented by its public, JSON-shaped fields. Using the
# Mapping interface keeps this boundary structural: the shared runtime does
# not import application or harness descriptor classes.
HostActionDescriptor: TypeAlias = Mapping[str, Any]
HostActionExecutor: TypeAlias = Callable[[Mapping[str, Any]], Any]

_MAX_DEPTH = 16
_MAX_NODES = 4096
_MAX_CONTAINER_ITEMS = 512
_MAX_KEY_LENGTH = 256
_MAX_STRING_LENGTH = 16_384
_MAX_SAFE_INTEGER = (2 ** 53) - 1
_MAX_SCHEMA_BYTES = 65_536
_MAX_DATA_BYTES = 32_768


def _json_plain(value: Any, *, label: str, max_bytes: int) -> Any:
    """Validate and copy a bounded JSON value into ordinary Python values."""
    budget = [0]
    active: set[int] = set()

    def visit(item: Any, depth: int) -> Any:
        budget[0] += 1
        if budget[0] > _MAX_NODES:
            raise ValueError(f"{label} exceeds the JSON node bound")
        if depth > _MAX_DEPTH:
            raise ValueError(f"{label} exceeds the JSON nesting bound")
        if item is None or type(item) is bool:
            return item
        if type(item) is int:
            if abs(item) > _MAX_SAFE_INTEGER:
                raise ValueError(f"{label} contains an integer outside the safe JSON range")
            return item
        if type(item) is float:
            if not math.isfinite(item):
                raise ValueError(f"{label} contains a non-finite number")
            return item
        if isinstance(item, str):
            if len(item) > _MAX_STRING_LENGTH:
                raise ValueError(f"{label} contains oversized text")
            return item
        if isinstance(item, Mapping):
            marker = id(item)
            if marker in active:
                raise ValueError(f"{label} contains a cyclic mapping")
            if len(item) > _MAX_CONTAINER_ITEMS:
                raise ValueError(f"{label} contains an oversized object")
            active.add(marker)
            try:
                result: dict[str, Any] = {}
                for key, child in item.items():
                    if (not isinstance(key, str) or len(key) > _MAX_KEY_LENGTH
                            or any(ord(char) < 0x20 for char in key)):
                        raise ValueError(f"{label} contains an invalid object key")
                    result[key] = visit(child, depth + 1)
                return result
            finally:
                active.remove(marker)
        if isinstance(item, Sequence) and not isinstance(
                item, (str, bytes, bytearray, memoryview)):
            marker = id(item)
            if marker in active:
                raise ValueError(f"{label} contains a cyclic array")
            if len(item) > _MAX_CONTAINER_ITEMS:
                raise ValueError(f"{label} contains an oversized array")
            active.add(marker)
            try:
                return [visit(child, depth + 1) for child in item]
            finally:
                active.remove(marker)
        raise ValueError(f"{label} contains a non-JSON value")

    result = visit(value, 0)
    try:
        encoded = json.dumps(result, ensure_ascii=True, allow_nan=False,
                             sort_keys=True, separators=(",", ":")).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise ValueError(f"{label} is not JSON encodable") from exc
    if len(encoded) > max_bytes:
        raise ValueError(f"{label} exceeds the {max_bytes}-byte JSON bound")
    return result


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(child) for key, child in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(child) for child in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_thaw(child) for child in value]
    return value


def _validate_schema_node(schema: Any, *, label: str, depth: int = 0) -> None:
    if type(schema) is bool:
        return
    if not isinstance(schema, Mapping):
        raise ValueError(f"{label} schema nodes must be objects or booleans")
    if depth > _MAX_DEPTH:
        raise ValueError(f"{label} schema exceeds the nesting bound")

    types = schema.get("type")
    valid_types = {"null", "boolean", "object", "array", "number", "integer", "string"}
    if types is not None:
        values = (types,) if isinstance(types, str) else types
        if (not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray))
                or not 1 <= len(values) <= 7
                or any(not isinstance(value, str) or value not in valid_types
                       for value in values)
                or len(values) != len(set(values))):
            raise ValueError(f"{label} schema has an invalid type declaration")

    required = schema.get("required")
    if required is not None and (
            not isinstance(required, Sequence)
            or isinstance(required, (str, bytes, bytearray))
            or len(required) > _MAX_CONTAINER_ITEMS
            or any(not isinstance(name, str) or len(name) > _MAX_KEY_LENGTH
                   for name in required)
            or len(required) != len(set(required))):
        raise ValueError(f"{label} schema has an invalid required list")

    enum = schema.get("enum")
    if enum is not None and (
            not isinstance(enum, Sequence) or isinstance(enum, (str, bytes, bytearray))
            or not 1 <= len(enum) <= _MAX_CONTAINER_ITEMS):
        raise ValueError(f"{label} schema has an invalid enum")

    ref = schema.get("$ref")
    if ref is not None and (not isinstance(ref, str) or not ref.startswith("#")):
        raise ValueError(f"{label} schema references must be local")

    object_schema_keywords = (
        "properties", "patternProperties", "definitions", "$defs",
        "dependentSchemas",
    )
    for keyword in object_schema_keywords:
        children = schema.get(keyword)
        if children is None:
            continue
        if not isinstance(children, Mapping):
            raise ValueError(f"{label} schema keyword {keyword} must be an object")
        for child in children.values():
            _validate_schema_node(child, label=label, depth=depth + 1)

    singular_schema_keywords = (
        "items", "additionalItems", "additionalProperties", "unevaluatedItems",
        "unevaluatedProperties", "contains", "propertyNames", "not", "if",
        "then", "else", "contentSchema",
    )
    for keyword in singular_schema_keywords:
        child = schema.get(keyword)
        if child is None:
            continue
        if keyword == "items" and isinstance(child, Sequence) and not isinstance(
                child, (str, bytes, bytearray)):
            if len(child) > _MAX_CONTAINER_ITEMS:
                raise ValueError(f"{label} schema keyword items exceeds its bound")
            for item in child:
                _validate_schema_node(item, label=label, depth=depth + 1)
        else:
            _validate_schema_node(child, label=label, depth=depth + 1)

    combined_schema_keywords = ("allOf", "anyOf", "oneOf")
    for keyword in combined_schema_keywords:
        children = schema.get(keyword)
        if children is None:
            continue
        if (not isinstance(children, Sequence)
                or isinstance(children, (str, bytes, bytearray))
                or not 1 <= len(children) <= _MAX_CONTAINER_ITEMS):
            raise ValueError(f"{label} schema keyword {keyword} must be a non-empty array")
        for child in children:
            _validate_schema_node(child, label=label, depth=depth + 1)


def freeze_json_object(value: Any, *, label: str, max_bytes: int = _MAX_DATA_BYTES
                       ) -> Mapping[str, Any]:
    """Return an immutable, bounded JSON object for sibling runtime contracts."""
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a JSON object")
    plain = _json_plain(value, label=label, max_bytes=max_bytes)
    return _freeze(plain)


def freeze_json_schema(value: Any, *, label: str) -> Mapping[str, Any]:
    """Validate the bounded JSON Schema subset used at host binding edges."""
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a JSON Schema object")
    plain = _json_plain(value, label=label, max_bytes=_MAX_SCHEMA_BYTES)
    _validate_schema_node(plain, label=label)
    return _freeze(plain)


def json_object_value(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return a detached, JSON-serializable copy of an immutable object."""
    return _thaw(value)


def json_schema_value(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return a detached, JSON-serializable copy of an immutable schema."""
    return _thaw(value)


@dataclass(frozen=True)
class HostActionBinding:
    """Host-owned action metadata and an executor, with no harness dependency."""

    binding_id: str
    description: str
    input_schema: Mapping[str, Any]
    descriptor: HostActionDescriptor
    executor: HostActionExecutor

    def __post_init__(self) -> None:
        _validate_id(self.binding_id, "action binding_id")
        if (not isinstance(self.description, str) or not self.description.strip()
                or len(self.description) > 1024):
            raise ValueError("action description must be bounded non-empty text")
        schema = freeze_json_schema(self.input_schema, label="action input_schema")
        descriptor = freeze_json_object(
            self.descriptor, label="action descriptor", max_bytes=_MAX_DATA_BYTES)
        if not callable(self.executor):
            raise TypeError("action executor must be callable")
        object.__setattr__(self, "input_schema", schema)
        object.__setattr__(self, "descriptor", descriptor)

    def schema_value(self) -> dict[str, Any]:
        """Return a detached JSON-safe copy of the action input schema."""
        return json_schema_value(self.input_schema)

    def descriptor_value(self) -> dict[str, Any]:
        """Return a detached JSON-safe copy of the host action descriptor."""
        return json_object_value(self.descriptor)


def _validate_id(value: Any, label: str) -> None:
    if (not isinstance(value, str) or not value.strip() or len(value) > 128
            or any(ord(char) < 0x20 for char in value)):
        raise ValueError(f"{label} must be bounded non-empty text")
