from __future__ import annotations

from dataclasses import replace

import pytest

try:
    from tests.test_system_one_runtime import (BACKEND_ID, MODEL_ID, MODEL_HASH,
                                               MODEL_REVISION, PROVIDER_HASH,
                                               FixedBackend, frame, judgment)
except Exception:
    pytest.skip("runtime test fixture unavailable", allow_module_level=True)

from lib.system_one_service import SystemOneService
from lib.system_one_runtime import SystemOneRuntime


def test_jev_high_confidence_stays_shadow_and_does_not_influence_behavior():
    backend_id = "digitalpsychology-jev-openrouter"
    request = frame(backend=backend_id, model="typesafe/jev-1.14",
                    revision="jev-1.14",
                    content_hash=MODEL_HASH, provider_hash=PROVIDER_HASH,
                    question_ids=("retry_relation",))
    answer = judgment(request, selected="materially_new")
    runtime = SystemOneRuntime(FixedBackend(identity=answer.capability()
                                            if hasattr(answer, "capability") else None,
                                            responds=lambda _frame: answer),
                               enabled_question_families=("retry_relation",))
    # FixedBackend returns the capability from its injected identity; rebuild it
    # explicitly for this model identity.
    from lib.system_one_contracts import SystemOneCapability
    from lib.protocol_artifact import (PROTOCOL_HASH, PROTOCOL_VERSION,
                                       QUESTION_REGISTRY, QUESTION_REGISTRY_HASH,
                                       STATE_PROJECTION_SCHEMA_HASH)
    identity = SystemOneCapability(
        backend_id=backend_id, model_id="typesafe/jev-1.14", model_revision="jev-1.14",
        model_content_hash=MODEL_HASH, provider_protocol_hash=PROVIDER_HASH,
        protocol_version=PROTOCOL_VERSION, protocol_hash=PROTOCOL_HASH,
        question_registry_hash=QUESTION_REGISTRY_HASH,
        state_projection_schema_hash=STATE_PROJECTION_SCHEMA_HASH,
        supported_question_families=tuple(QUESTION_REGISTRY),
        max_state_bytes=32768, stateful_sessions=False, max_concurrent_requests=2)
    runtime = SystemOneRuntime(
        FixedBackend(identity=identity, responds=lambda _frame: answer),
        enabled_question_families=("retry_relation",))
    result = runtime.judge(request, mode="advisory")
    assert result.status == "shadow"
    assert result.reason == "jev_unrecognized_served_snapshot"
    assert result.judgment.answer("retry_relation").raw_confidence == 0.85
