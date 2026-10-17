from __future__ import annotations

import json
import re
from dataclasses import replace

import pytest

from lib.system_one_jev import (JevOpenRouterProvider, JevSecretError,
                                build_jev_request, jev_state_projection,
                                judgment_from_jev)
from lib.system_one_jev_models import JEV_MODELS, provider_protocol_hash

try:
    from tests.test_system_one_runtime import frame
except Exception:
    pytest.skip("runtime test fixture unavailable", allow_module_level=True)


def _frame(model_id="typesafe/jev-1.14"):
    provider = JevOpenRouterProvider("unused", model_id=model_id)
    capability = provider.capability()
    return frame(
        backend=capability.backend_id, model=model_id,
        revision=capability.model_revision,
        content_hash=capability.model_content_hash,
        provider_hash=capability.provider_protocol_hash,
        question_ids=("retry_relation",))


def _documented_response(model_id="typesafe/jev-1.14", *, confidence=0.7):
    served = next(iter(JEV_MODELS[model_id]["served_model_ids"]))
    labels = list(JEV_MODELS and frame(question_ids=("retry_relation",))
                  .questions[0].criteria and [label for label, _ in
                                               frame(question_ids=("retry_relation",))
                                               .questions[0].criteria])
    probabilities = {label: 0.0 for label in labels}
    probabilities["materially_new"] = confidence
    remainder = 1.0 - confidence
    probabilities["same_premise_new_action"] = remainder
    return {
        "model": served,
        "answers": {
            "retry_relation": {
                "type": "choice",
                "choice": "materially_new",
                "probabilities": probabilities,
                "confidence": confidence,
            }
        },
        "provider": "TypeSafe",
    }


def test_documented_1_14_response_fixture_parses():
    judgment = judgment_from_jev(_frame(), _documented_response(), elapsed_ms=10)
    assert judgment.answer("retry_relation").selected == "materially_new"
    diagnostics = judgment.local_inference_diagnostics
    assert diagnostics["served_model"] == "typesafe/jev-1.14-20261014"
    assert diagnostics["requested_model"] == "typesafe/jev-1.14"
    assert diagnostics["provider_revision"] == "20261014"
    assert diagnostics["advisory_eligible"] is False


def test_documented_1_13_response_fixture_parses():
    request = _frame(model_id="typesafe/jev-1.13")
    response = _documented_response(model_id="typesafe/jev-1.13")
    judgment = judgment_from_jev(request, response, elapsed_ms=1)
    assert judgment.local_inference_diagnostics["served_model"] == (
        "typesafe/jev-1.13-20260917")


def test_projection_redacts_private_facts_and_scans_entire_payload():
    base = _frame()
    private = ("/etc/passwd https://user:token123456789@example.com "
               "internal.service.local Bearer abcdefghijklmnopqrstuvwxyz123456")
    from lib.system_one_contracts import SemanticState, canonical_hash
    candidate = replace(base.state.candidate, proposed_purpose=private)
    activity = (candidate,) + tuple(base.state.activity)[1:]
    semantic_state = replace(base.state, candidate=candidate, activity=activity)
    state_digest = canonical_hash(semantic_state.to_dict())
    request = replace(base, state=semantic_state, state_digest=state_digest,
                      request_digest=canonical_hash({
                          "identity": base.identity.to_dict(),
                          "sequence": base.sequence,
                          "state_digest": state_digest,
                          "question_bundle_digest": base.question_bundle_digest,
                          "policy_hash": base.policy_hash}))
    payload = jev_state_projection(request)
    assert payload["candidate"]["purpose_class"].count("[REDACTED]") >= 4
    assert "/etc/" not in payload["candidate"]["purpose_class"]
    assert "internal.service.local" not in payload["candidate"]["purpose_class"]
    assert "token123456789" not in payload["candidate"]["purpose_class"]
    request_payload = json.dumps(build_jev_request(request))
    assert "/etc/" not in request_payload
    assert "token123456789" not in request_payload


def test_request_uses_canonical_registry_definitions_and_pinned_model():
    request = _frame()
    payload = build_jev_request(request)
    assert payload["model"] == "typesafe/jev-1.14"
    assert set(payload["questions"]) == {"retry_relation"}
    assert payload["questions"]["retry_relation"]["criteria"] == {
        label: description for label, description in request.questions[0].criteria}
    assert payload["state"]["question_id"] == "retry_relation"
    assert payload["state"]["schema"] == "jev-family-semantic-projection/v2"


def test_identity_mismatch_is_rejected():
    provider = JevOpenRouterProvider("key")
    with pytest.raises(ValueError):
        provider.judge(_frame(model_id="typesafe/jev-1.13"))


def test_wrong_served_model_is_rejected():
    response = _documented_response()
    response["model"] = "typesafe/jev-1.14-99999999"
    with pytest.raises(ValueError, match="unrecognized model snapshot"):
        judgment_from_jev(_frame(), response, elapsed_ms=1)


def test_answer_type_confidence_and_probabilities_are_validated():
    response = _documented_response()
    with pytest.raises(ValueError, match="answer type"):
        judgment_from_jev(_frame(), {**response, "answers": {
            "retry_relation": {**response["answers"]["retry_relation"], "type": "score"}}},
                          elapsed_ms=1)
    with pytest.raises(ValueError, match="confidence conflicts"):
        bad = json.loads(json.dumps(response))
        bad["answers"]["retry_relation"]["confidence"] = 0.9
        judgment_from_jev(_frame(), bad, elapsed_ms=1)
    with pytest.raises(ValueError):
        bad = json.loads(json.dumps(response))
        bad["answers"]["retry_relation"]["probabilities"]["materially_new"] = 1.5
        judgment_from_jev(_frame(), bad, elapsed_ms=1)


def test_provider_revision_and_requested_model_are_recorded_separately():
    judgment = judgment_from_jev(_frame(), _documented_response(), elapsed_ms=10)
    diagnostics = judgment.local_inference_diagnostics
    assert diagnostics["requested_model"] == "typesafe/jev-1.14"
    assert diagnostics["served_model"] == "typesafe/jev-1.14-20261014"
    assert diagnostics["provider_revision"] == "20261014"


def test_transport_uses_documented_response_and_close_blocks_dispatch():
    calls = []
    def transport(request, timeout):
        calls.append((request, timeout))
        return json.dumps(_documented_response()).encode()
    provider = JevOpenRouterProvider("key", transport=transport, timeout_ms=10)
    result = provider.judge(_frame())
    assert result.answer("retry_relation").selected == "materially_new"
    assert calls[0][0].get_header("Authorization") == "Bearer key"
    provider.close()
    with pytest.raises(RuntimeError, match="closed"):
        provider.judge(_frame())


def test_class_factory_is_available():
    assert callable(JevOpenRouterProvider.from_environment)
