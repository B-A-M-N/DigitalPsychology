from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest

from lib.system_one_jev import (JevOpenRouterProvider, JevSecretError,
                                build_jev_request, jev_state_projection,
                                judgment_from_jev)
from lib.system_one_jev_models import JEV_MODELS, provider_protocol_hash

try:
    from tests.test_system_one_runtime import frame
except Exception:
    frame = None


def _frame(model_id="typesafe/jev-1.14"):
    if frame is None:
        pytest.skip("runtime test fixture unavailable")
    base = frame(backend="digitalpsychology-jev-openrouter",
                 model=model_id,
                 revision=JEV_MODELS[model_id]["model_revision"],
                 content_hash=JevOpenRouterProvider(
                     "unused", model_id=model_id).capability().model_content_hash,
                 provider_hash=provider_protocol_hash(model_id),
                 question_ids=("retry_relation",))
    return base


def test_projection_redacts_secrets_and_paths():
    provider = JevOpenRouterProvider("key")
    base = _frame()
    state = base.state.to_dict()
    state["candidate"]["proposed_purpose"] = (
        "use /home/agent/secret.txt and api_key=abcd1234 token: supersecret")
    from lib.system_one_contracts import SemanticState, canonical_hash
    from dataclasses import replace as _replace
    state["candidate"] = _replace(base.state.candidate,
                                  proposed_purpose=state["candidate"]["proposed_purpose"])
    state["activity"] = (state["candidate"],) + tuple(base.state.activity)[1:]
    semantic_state = SemanticState(
        task=state["task"], situation=state["situation"],
        criteria=base.state.criteria, activity=state["activity"],
        workflows=base.state.workflows, convergence=base.state.convergence,
        candidate=state["candidate"],
        failed_approaches=base.state.failed_approaches,
        legal_routes=base.state.legal_routes)
    state_digest = canonical_hash(semantic_state.to_dict())
    from dataclasses import replace
    request = replace(base, state=semantic_state, state_digest=state_digest,
                      request_digest=canonical_hash({
                          "identity": base.identity.to_dict(),
                          "sequence": base.sequence,
                          "state_digest": state_digest,
                          "question_bundle_digest": base.question_bundle_digest,
                          "policy_hash": base.policy_hash}))
    payload = jev_state_projection(request)
    assert "[REDACTED]" in payload["candidate"]["purpose"]
    assert "/home/" not in payload["candidate"]["purpose"]


def test_request_uses_canonical_registry_definitions_and_pinned_model():
    provider = JevOpenRouterProvider("key")
    payload = build_jev_request(_frame())
    assert payload["model"] == "typesafe/jev-1.14"
    assert set(payload["questions"]) == {"retry_relation"}
    assert payload["questions"]["retry_relation"]["criteria"] == {
        label: description for label, description in
        _frame().questions[0].criteria}


def test_identity_mismatch_is_rejected():
    provider = JevOpenRouterProvider("key")
    with pytest.raises(ValueError):
        provider.judge(_frame(model_id="typesafe/jev-1.13"))


def test_provider_revision_is_required_and_model_substitution_rejected():
    provider = JevOpenRouterProvider("key")
    response = {"model": "typesafe/jev-1.14", "provider_revision": "abc",
                "answers": {"retry_relation": {
                    "choice": "equivalent_retry",
                    "probabilities": {"equivalent_retry": 1.0,
                                      "materially_new": 0.0,
                                      "same_premise_new_action": 0.0,
                                      "unrelated": 0.0}}}}
    judgment = judgment_from_jev(_frame(), response, elapsed_ms=10)
    assert judgment.backend_id == "digitalpsychology-jev-openrouter"
    assert judgment.local_inference_diagnostics["advisory_eligible"] is False
    with pytest.raises(ValueError):
        judgment_from_jev(_frame(), {**response, "model": "typesafe/jev-1.13"}, elapsed_ms=1)
    with pytest.raises(ValueError):
        judgment_from_jev(_frame(), {**response, "provider_revision": " "}, elapsed_ms=1)


def test_probability_contract_is_strict():
    provider = JevOpenRouterProvider("key")
    labels = ["equivalent_retry", "materially_new", "same_premise_new_action", "unrelated"]
    base = {"model": "typesafe/jev-1.14", "provider_revision": "abc",
            "answers": {"retry_relation": {"choice": "materially_new",
                                            "probabilities": dict.fromkeys(labels, 0.25)}}}
    judgment_from_jev(_frame(), base, elapsed_ms=1)
    for bad in ([], None, 0.5, "x"):
        row = dict(base["answers"]["retry_relation"])
        row["probabilities"] = {label: bad for label in labels}
        with pytest.raises(ValueError):
            judgment_from_jev(_frame(), {**base, "answers": {"retry_relation": row}},
                              elapsed_ms=1)


def test_transport_and_close():
    calls = []
    def transport(request, timeout):
        calls.append((request, timeout))
        return json.dumps({
            "model": "typesafe/jev-1.14", "provider_revision": "abc",
            "answers": {"retry_relation": {"choice": "materially_new",
                                            "probabilities": {
                                                "equivalent_retry": 0.1,
                                                "materially_new": 0.7,
                                                "same_premise_new_action": 0.1,
                                                "unrelated": 0.1}}}}).encode()
    provider = JevOpenRouterProvider("key", transport=transport, timeout_ms=10)
    result = provider.judge(_frame())
    assert result.answer("retry_relation").selected == "materially_new"
    assert calls[0][0].get_header("Authorization") == "Bearer key"
    provider.close()
