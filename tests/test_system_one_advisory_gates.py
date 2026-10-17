from __future__ import annotations

from dataclasses import replace

import pytest

try:
    from tests.test_system_one_runtime import (BACKEND_ID, MODEL_HASH, MODEL_ID,
                                               MODEL_REVISION, PROVIDER_HASH,
                                               FixedBackend, frame, judgment)
except Exception:
    pytest.skip("runtime test fixture unavailable", allow_module_level=True)

from lib.protocol_artifact import (PROTOCOL_HASH, PROTOCOL_VERSION,
                                   QUESTION_REGISTRY,
                                   QUESTION_REGISTRY_HASH,
                                   STATE_PROJECTION_SCHEMA_HASH)
from lib.system_one_contracts import SystemOneCapability
from lib.system_one_runtime import SystemOneRuntime


def _jev_identity(model_id="typesafe/jev-1.14", revision="jev-1.14"):
    return SystemOneCapability(
        backend_id="digitalpsychology-jev-openrouter", model_id=model_id,
        model_revision=revision, model_content_hash=MODEL_HASH,
        provider_protocol_hash=PROVIDER_HASH, protocol_version=PROTOCOL_VERSION,
        protocol_hash=PROTOCOL_HASH,
        question_registry_hash=QUESTION_REGISTRY_HASH,
        state_projection_schema_hash=STATE_PROJECTION_SCHEMA_HASH,
        supported_question_families=tuple(QUESTION_REGISTRY),
        max_state_bytes=32768, stateful_sessions=False, max_concurrent_requests=2)


def _laya_identity():
    return SystemOneCapability(
        backend_id=BACKEND_ID, model_id=MODEL_ID, model_revision=MODEL_REVISION,
        model_content_hash=MODEL_HASH, provider_protocol_hash=PROVIDER_HASH,
        protocol_version=PROTOCOL_VERSION, protocol_hash=PROTOCOL_HASH,
        question_registry_hash=QUESTION_REGISTRY_HASH,
        state_projection_schema_hash=STATE_PROJECTION_SCHEMA_HASH,
        supported_question_families=tuple(QUESTION_REGISTRY),
        max_state_bytes=32768, stateful_sessions=False, max_concurrent_requests=2)


def test_new_model_identity_rejects_cached_laya_result_without_calling_provider():
    request = frame(question_ids=("retry_relation",))
    backend = FixedBackend()
    runtime = SystemOneRuntime(backend, cache_size=2,
                               enabled_question_families=("retry_relation",))
    assert runtime.judge(request, mode="shadow").status == "shadow"
    from lib.system_one_contracts import canonical_hash
    changed_frame = replace(
        request, model_revision="new-revision", sequence=2,
        request_id="request-2",
        request_digest=canonical_hash({
            "identity": request.identity.to_dict(), "sequence": 2,
            "state_digest": request.state_digest,
            "question_bundle_digest": request.question_bundle_digest,
            "policy_hash": request.policy_hash}))
    result = runtime.judge(changed_frame, mode="shadow")
    assert result.status == "rejected"
    assert result.reason == "provider_contract_error"
    assert len(backend.calls) == 2


def test_jev_high_confidence_is_not_advisory_without_task_calibration():
    request = frame(backend="digitalpsychology-jev-openrouter",
                    model="typesafe/jev-1.14", revision="jev-1.14",
                    content_hash=MODEL_HASH, provider_hash=PROVIDER_HASH,
                    question_ids=("retry_relation",))
    answer = judgment(request, selected="materially_new")
    backend = FixedBackend(identity=_jev_identity(),
                           responds=lambda _frame: answer)
    runtime = SystemOneRuntime(backend, enabled_question_families=("retry_relation",))
    result = runtime.judge(request, mode="advisory")
    assert result.status == "shadow"
    assert result.reason == "jev_unrecognized_served_snapshot"


def test_laya_high_confidence_is_not_advisory_without_coverage_diagnostics():
    request = frame(question_ids=("retry_relation",))
    answer = judgment(request, selected="materially_new")
    backend = FixedBackend(identity=_laya_identity(),
                           responds=lambda _frame: answer)
    runtime = SystemOneRuntime(backend, enabled_question_families=("retry_relation",))
    result = runtime.judge(request, mode="advisory")
    assert result.status == "shadow"
    assert result.reason == "uncalibrated_or_shifted_context"
