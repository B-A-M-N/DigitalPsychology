"""Bounded diagnostics for local Laya inference coverage."""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

LAYA_DIAGNOSTICS_SCHEMA = "laya-inference-diagnostics/v1"
PROJECTION_SCHEMA = "laya-semantic-projection/v3"
SUPPORTED_PROJECTION_SCHEMAS = frozenset({
    "laya-semantic-projection/v2", PROJECTION_SCHEMA,
})
_QUESTION_IDS = frozenset({
    "action_relation", "progress_relation", "retry_relation", "goal_drift",
    "route_preference",
})
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


def validate_laya_diagnostics(value: Any) -> dict[str, Any]:
    """Validate and copy the closed, bounded diagnostics wire shape."""
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
    if (not isinstance(value["projection_digest"], str)
            or not _DIGEST.fullmatch(value["projection_digest"])):
        raise ValueError("Laya projection digest is malformed")
    for name, maximum in (("projection_bytes", 32768), ("latency_ms", 30000)):
        number = value[name]
        if type(number) is not int or not 0 <= number <= maximum:
            raise ValueError(f"Laya diagnostic {name} is out of bounds")
    for name in ("preflight_state_tokens", "preflight_tokens_dropped", "input_tokens",
                 "state_tokens", "state_tokens_dropped"):
        number = value[name]
        if number is not None and (type(number) is not int or not 0 <= number <= 65536):
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


__all__ = ["LAYA_DIAGNOSTICS_SCHEMA", "PROJECTION_SCHEMA",
           "validate_laya_diagnostics"]
