"""Provider-specific bounded inference diagnostics."""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

LAYA_DIAGNOSTICS_SCHEMA = "laya-inference-diagnostics/v1"
JEV_DIAGNOSTICS_SCHEMA = "jev-inference-diagnostics/v1"
PROJECTION_SCHEMA = "laya-semantic-projection/v3"
SUPPORTED_PROJECTION_SCHEMAS = frozenset({
    "laya-semantic-projection/v2", PROJECTION_SCHEMA,
})
JEV_PROJECTION_SCHEMA = "jev-minimal-semantic-projection/v1"
_QUESTION_IDS = frozenset({
    "action_relation", "progress_relation", "retry_relation", "goal_drift",
    "route_preference",
})
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


def _bounded_int(value: Any, *, maximum: int, nullable: bool = False) -> bool:
    if value is None:
        return nullable
    return type(value) is int and 0 <= value <= maximum


def _projection(value: Mapping[str, Any], *, schema: str) -> bool:
    if value["projection_schema"] != schema:
        return False
    digest = value["projection_digest"]
    return isinstance(digest, str) and bool(_DIGEST.fullmatch(digest))


def _validate_common_numbers(value: Mapping[str, Any], *, maximum_latency: int) -> bool:
    return all((
        _bounded_int(value["projection_bytes"], maximum=32768),
        _bounded_int(value["latency_ms"], maximum=maximum_latency),
    ))


def validate_laya_diagnostics(value: Any) -> dict[str, Any]:
    """Validate and copy the closed, bounded Laya diagnostics wire shape."""
    fields = {
        "schema_version", "projection_schema", "projection_digest",
        "projection_bytes", "latency_ms", "preflight_status",
        "preflight_state_tokens", "preflight_tokens_dropped",
        "input_tokens", "state_tokens", "state_tokens_dropped",
        "truncation_status", "truncated_questions", "coverage_status",
        "option_coverage", "advisory_eligible",
    }
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ValueError("Laya inference diagnostics are malformed")
    if value["schema_version"] != LAYA_DIAGNOSTICS_SCHEMA:
        raise ValueError("unsupported Laya inference diagnostics schema")
    if value["projection_schema"] not in SUPPORTED_PROJECTION_SCHEMAS:
        raise ValueError("unsupported Laya semantic projection schema")
    if not _validate_common_numbers(value, maximum_latency=30000):
        raise ValueError("Laya diagnostic projection or latency is out of bounds")
    for name in ("preflight_state_tokens", "preflight_tokens_dropped", "input_tokens",
                 "state_tokens", "state_tokens_dropped"):
        if not _bounded_int(value[name], maximum=65536, nullable=True):
            raise ValueError(f"Laya diagnostic {name} is out of bounds")
    statuses = {"complete", "truncated", "unknown"}
    if value["preflight_status"] not in statuses:
        raise ValueError("Laya preflight status is unsupported")
    if value["truncation_status"] not in statuses:
        raise ValueError("Laya truncation status is unsupported")
    if value["coverage_status"] not in {"complete", "incomplete", "unknown"}:
        raise ValueError("Laya option coverage status is unsupported")
    truncated = value["truncated_questions"]
    if (not isinstance(truncated, list) or len(truncated) > 8
            or any(item not in _QUESTION_IDS for item in truncated)
            or len(set(truncated)) != len(truncated)):
        raise ValueError("Laya truncated question list is malformed")
    coverage = value["option_coverage"]
    if (not isinstance(coverage, Mapping) or not coverage
            or len(coverage) > 8 or not set(coverage).issubset(_QUESTION_IDS)):
        raise ValueError("Laya option coverage is malformed")
    normalized_coverage: dict[str, dict[str, Any]] = {}
    for question_id, item in coverage.items():
        if not isinstance(item, Mapping) or set(item) != {
                "expected_options", "returned_options", "status"}:
            raise ValueError("Laya per-question option coverage is malformed")
        expected, returned = item["expected_options"], item["returned_options"]
        if (type(expected) is not int or not 1 <= expected <= 24
                or type(returned) is not int or not 0 <= returned <= 24
                or item["status"] not in {"complete", "incomplete", "unknown"}):
            raise ValueError("Laya per-question option coverage is out of bounds")
        normalized_coverage[str(question_id)] = {
            "expected_options": expected,
            "returned_options": returned,
            "status": item["status"],
        }
    if type(value["advisory_eligible"]) is not bool:
        raise ValueError("Laya advisory eligibility must be boolean")
    eligible = (value["preflight_status"] == "complete"
                and value["truncation_status"] == "complete"
                and value["coverage_status"] == "complete"
                and all(item["status"] == "complete"
                        for item in normalized_coverage.values()))
    if value["advisory_eligible"] != eligible:
        raise ValueError("Laya advisory eligibility conflicts with diagnostics")
    return {**dict(value), "truncated_questions": list(truncated),
            "option_coverage": normalized_coverage}


def validate_jev_diagnostics(value: Any) -> dict[str, Any]:
    """Validate and copy the closed, bounded Jev diagnostics wire shape."""
    fields = {
        "schema_version", "projection_schema", "projection_digest",
        "projection_bytes", "latency_ms", "provider_revision", "advisory_eligible",
    }
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ValueError("Jev inference diagnostics are malformed")
    if value["schema_version"] != JEV_DIAGNOSTICS_SCHEMA:
        raise ValueError("unsupported Jev inference diagnostics schema")
    if not _projection(value, schema=JEV_PROJECTION_SCHEMA):
        raise ValueError("Jev projection identity is malformed")
    if not _validate_common_numbers(value, maximum_latency=30000):
        raise ValueError("Jev diagnostic projection or latency is out of bounds")
    revision = value["provider_revision"]
    if (not isinstance(revision, str) or not revision.strip()
            or len(revision) > 256):
        raise ValueError("Jev provider revision is malformed")
    if type(value["advisory_eligible"]) is not bool:
        raise ValueError("Jev advisory eligibility must be boolean")
    return {**dict(value)}


def validate_inference_diagnostics(value: Any, backend_id: str) -> dict[str, Any]:
    """Dispatch diagnostics to the provider that owns their schema."""
    if backend_id == "digitalpsychology-laya-native":
        return validate_laya_diagnostics(value)
    if backend_id == "digitalpsychology-jev-openrouter":
        return validate_jev_diagnostics(value)
    raise ValueError(f"unsupported System-1 diagnostics provider: {backend_id}")


__all__ = ["LAYA_DIAGNOSTICS_SCHEMA", "JEV_DIAGNOSTICS_SCHEMA", "PROJECTION_SCHEMA",
           "JEV_PROJECTION_SCHEMA", "validate_laya_diagnostics",
           "validate_jev_diagnostics", "validate_inference_diagnostics"]
