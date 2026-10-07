from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cognitive_runtime_core.evaluation import (
    EvaluationCohortIdentity,
    IncomparableCohortError,
)


def _identity() -> EvaluationCohortIdentity:
    return EvaluationCohortIdentity(
        harness_id="codex-app-server",
        harness_version="codex-cli 0.160.0",
        adapter_version="1.0.0",
        adapter_hash="a" * 64,
        agent_model="gpt-6-luna",
        model_revision="unavailable",
        provider="chatgpt",
        provider_endpoint_digest="not_applicable",
        auth_mode="chatgpt",
        harness_runtime_version="codex-cli 0.160.0",
        feature_catalog_hash="b" * 64,
        reasoning_effort="medium",
        task_fixture_hash="c" * 64,
        task_contract_hash="d" * 64,
        tool_binding_hash="e" * 64,
        validator_binding_hash="f" * 64,
        cfw_version="1.4.0",
        cfw_source_hash="1" * 64,
        csw_version="2.1.0",
        csw_source_hash="2" * 64,
        dp_version="1.5.0",
        dp_source_hash="3" * 64,
        laya_mode="disabled",
        laya_model_id="not_applicable",
        laya_calibration_hash="not_applicable",
    )


def test_cohort_identity_hash_is_canonical_and_round_trips():
    identity = _identity()
    restored = EvaluationCohortIdentity.from_mapping(identity.to_dict())

    assert restored == identity
    assert restored.cohort_hash == identity.cohort_hash
    assert len(identity.cohort_hash) == 64
    assert identity.require_same_cohort(restored) == identity.cohort_hash


def test_same_cohort_comparison_rejects_any_runtime_or_treatment_drift():
    identity = _identity()
    changed = replace(identity, laya_mode="advisory")

    with pytest.raises(IncomparableCohortError, match="identical cohort hashes"):
        identity.require_same_cohort(changed)


def test_cohort_identity_requires_explicit_metadata_and_digest_values():
    values = _identity().canonical_fields()
    values["feature_catalog_hash"] = ""
    with pytest.raises(ValueError, match="feature_catalog_hash"):
        EvaluationCohortIdentity(**values)

    serialized = _identity().to_dict()
    serialized["agent_model"] = "different-model"
    with pytest.raises(ValueError, match="does not match"):
        EvaluationCohortIdentity.from_mapping(serialized)
