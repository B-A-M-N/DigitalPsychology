"""Canonical identity for model-backed evaluation cohorts."""
from __future__ import annotations

from dataclasses import dataclass, fields
import hashlib
import json
import re
from typing import Any, Mapping


EVALUATION_COHORT_IDENTITY_SCHEMA = "evaluation-cohort-identity/v1"
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_UNAVAILABLE_DIGESTS = frozenset({"unavailable", "not_applicable", "disabled"})
_TEXT_FIELDS = (
    "harness_id",
    "harness_version",
    "adapter_version",
    "adapter_hash",
    "agent_model",
    "model_revision",
    "provider",
    "provider_endpoint_digest",
    "auth_mode",
    "harness_runtime_version",
    "feature_catalog_hash",
    "reasoning_effort",
    "task_fixture_hash",
    "task_contract_hash",
    "tool_binding_hash",
    "validator_binding_hash",
    "cfw_version",
    "cfw_source_hash",
    "csw_version",
    "csw_source_hash",
    "dp_version",
    "dp_source_hash",
    "laya_mode",
    "laya_model_id",
    "laya_calibration_hash",
)
_DIGEST_FIELDS = frozenset({
    "adapter_hash", "provider_endpoint_digest", "feature_catalog_hash",
    "task_fixture_hash", "task_contract_hash", "tool_binding_hash",
    "validator_binding_hash", "cfw_source_hash", "csw_source_hash",
    "dp_source_hash", "laya_calibration_hash",
})


class IncomparableCohortError(ValueError):
    """Raised when a comparison claims a shared cohort for different identities."""


def _canonical_json(value: Mapping[str, Any]) -> str:
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True,
                          separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("cohort identity must contain canonical JSON values") from exc


@dataclass(frozen=True)
class EvaluationCohortIdentity:
    """All runtime and treatment pins needed to identify one evaluation cohort.

    Unknown values must be explicit (for example ``unavailable`` or
    ``not_applicable``), so missing metadata can never disappear from the hash.
    Endpoint URLs and other secret-bearing values must be represented by a
    digest, not copied into this contract.
    """

    harness_id: str
    harness_version: str
    adapter_version: str
    adapter_hash: str
    agent_model: str
    model_revision: str
    provider: str
    provider_endpoint_digest: str
    auth_mode: str
    harness_runtime_version: str
    feature_catalog_hash: str
    reasoning_effort: str
    task_fixture_hash: str
    task_contract_hash: str
    tool_binding_hash: str
    validator_binding_hash: str
    cfw_version: str
    cfw_source_hash: str
    csw_version: str
    csw_source_hash: str
    dp_version: str
    dp_source_hash: str
    laya_mode: str
    laya_model_id: str
    laya_calibration_hash: str

    def __post_init__(self) -> None:
        for name in _TEXT_FIELDS:
            value = getattr(self, name)
            if (not isinstance(value, str) or value != value.strip()
                    or not value or len(value) > 256
                    or any(ord(char) < 32 for char in value)):
                raise ValueError(f"cohort {name} must be bounded non-empty text")
        for name in _DIGEST_FIELDS:
            value = getattr(self, name)
            if value not in _UNAVAILABLE_DIGESTS and not _SHA256_RE.fullmatch(value):
                raise ValueError(
                    f"cohort {name} must be a lowercase SHA-256 digest or explicit status")

    def canonical_fields(self) -> dict[str, str]:
        """Return the immutable identity fields in a detached canonical mapping."""
        return {item.name: getattr(self, item.name) for item in fields(self)}

    @property
    def cohort_hash(self) -> str:
        material = {"schema": EVALUATION_COHORT_IDENTITY_SCHEMA,
                    **self.canonical_fields()}
        return hashlib.sha256(
            ("cognitive-runtime-evaluation-cohort:\n" + _canonical_json(material))
            .encode("ascii")).hexdigest()

    def to_dict(self) -> dict[str, str]:
        return {"schema": EVALUATION_COHORT_IDENTITY_SCHEMA,
                **self.canonical_fields(), "cohort_hash": self.cohort_hash}

    def require_same_cohort(self, other: "EvaluationCohortIdentity") -> str:
        """Return the shared cohort hash or reject a causal same-cohort claim."""
        if not isinstance(other, EvaluationCohortIdentity):
            raise TypeError("cohort comparison requires an EvaluationCohortIdentity")
        if self.cohort_hash != other.cohort_hash:
            raise IncomparableCohortError(
                "causal same-cohort comparison requires identical cohort hashes")
        return self.cohort_hash

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "EvaluationCohortIdentity":
        required = {"schema", "cohort_hash", *_TEXT_FIELDS}
        if not isinstance(value, Mapping) or set(value) != required:
            raise ValueError("evaluation cohort identity fields are invalid")
        if value.get("schema") != EVALUATION_COHORT_IDENTITY_SCHEMA:
            raise ValueError("unsupported evaluation cohort identity schema")
        identity = cls(**{name: value[name] for name in _TEXT_FIELDS})
        if value.get("cohort_hash") != identity.cohort_hash:
            raise ValueError("evaluation cohort hash does not match its identity fields")
        return identity


__all__ = ["EVALUATION_COHORT_IDENTITY_SCHEMA", "EvaluationCohortIdentity",
           "IncomparableCohortError"]
