from __future__ import annotations

from dataclasses import replace

import pytest

try:
    from tests.test_system_one_runtime import (BACKEND_ID, MODEL_HASH, MODEL_ID,
                                               MODEL_REVISION, PROVIDER_HASH,
                                               FixedBackend, calibration_record,
                                               frame, judgment)
except Exception:
    pytest.skip("runtime test fixture unavailable", allow_module_level=True)

from lib.protocol_artifact import (PROTOCOL_HASH, PROTOCOL_VERSION,
                                   QUESTION_REGISTRY,
                                   QUESTION_REGISTRY_HASH,
                                   STATE_PROJECTION_SCHEMA_HASH)
from lib.system_one_calibration import CalibrationBundle
from lib.system_one_contracts import SystemOneCapability
from lib.system_one_runtime import SystemOneRuntime


def _jev_identity(model_revision="jev-1.14", model_hash=MODEL_HASH,
                  provider_hash=PROVIDER_HASH):
    return SystemOneCapability(
        backend_id="digitalpsychology-jev-openrouter",
        model_id="typesafe/jev-1.14", model_revision=model_revision,
        model_content_hash=model_hash, provider_protocol_hash=provider_hash,
        protocol_version=PROTOCOL_VERSION, protocol_hash=PROTOCOL_HASH,
        question_registry_hash=QUESTION_REGISTRY_HASH,
        state_projection_schema_hash=STATE_PROJECTION_SCHEMA_HASH,
        supported_question_families=tuple(QUESTION_REGISTRY),
        max_state_bytes=32768, stateful_sessions=False, max_concurrent_requests=2)


def _qualified_jev_bundle(*, model_hash=MODEL_HASH, provider_hash=PROVIDER_HASH,
                          question_id="retry_relation", drift="clear"):
    record = replace(
        calibration_record(question_id),
        backend_id="digitalpsychology-jev-openrouter",
        model_id="typesafe/jev-1.14", model_revision="jev-1.14",
        model_content_hash=model_hash, provider_protocol_hash=provider_hash,
        work_modes=("delivery",), deliverable_kinds=("code_behavior",),
        drift_status=drift)
    return CalibrationBundle({question_id: record})


def _jev_request(bundle):
    return frame(
        backend="digitalpsychology-jev-openrouter", model="typesafe/jev-1.14",
        revision="jev-1.14", content_hash=MODEL_HASH, provider_hash=PROVIDER_HASH,
        question_ids=("retry_relation",), calibration_bundle=bundle)


def test_qualified_jev_can_reach_advisory_and_influence_once_permitted():
    bundle = _qualified_jev_bundle()
    request = _jev_request(bundle)
    answer = judgment(request, selected="materially_new")
    runtime = SystemOneRuntime(
        FixedBackend(identity=_jev_identity(), responds=lambda _frame: answer),
        calibrations=bundle, enabled_question_families=("retry_relation",))
    result = runtime.judge(request, mode="advisory")
    assert result.status == "ok"
    assert result.calibrated
    assert result.judgment.answer("retry_relation").calibrated_confidence >= 0.9


def test_unqualified_family_cannot_become_advisory():
    bundle = _qualified_jev_bundle(question_id="progress_relation")
    request = _jev_request(bundle)
    answer = judgment(request, selected="materially_new")
    runtime = SystemOneRuntime(
        FixedBackend(identity=_jev_identity(), responds=lambda _frame: answer),
        calibrations=bundle, enabled_question_families=("progress_relation",
                                                          "retry_relation"))
    result = runtime.judge(request, mode="advisory")
    assert result.status == "shadow"
    assert result.reason == "jev_unrecognized_served_snapshot"


def test_drifted_calibration_cannot_become_advisory():
    bundle = _qualified_jev_bundle(drift="drifted")
    request = _jev_request(bundle)
    answer = judgment(request, selected="materially_new")
    runtime = SystemOneRuntime(
        FixedBackend(identity=_jev_identity(), responds=lambda _frame: answer),
        calibrations=bundle, enabled_question_families=("retry_relation",))
    result = runtime.judge(request, mode="advisory")
    assert result.status == "shadow"


def test_served_snapshot_change_blocks_stale_calibration():
    bundle = _qualified_jev_bundle()
    request = _jev_request(bundle)
    answer = judgment(request, selected="materially_new")
    new_hash = "e" * 64
    runtime = SystemOneRuntime(
        FixedBackend(identity=_jev_identity(model_hash=new_hash),
                     responds=lambda _frame: answer),
        calibrations=bundle, enabled_question_families=("retry_relation",))
    result = runtime.judge(request, mode="advisory")
    assert result.status == "rejected"
    assert result.reason == "provider_contract_error"
