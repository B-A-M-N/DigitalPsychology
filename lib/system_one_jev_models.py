"""Pinned, allowlisted Jev model identities and provider-protocol bindings."""
from __future__ import annotations

import hashlib
from typing import Mapping

JEV_MODELS: Mapping[str, Mapping[str, str]] = {
    "typesafe/jev-1.13": {
        "model_revision": "jev-1.13",
        "context_limit": "32768",
    },
    "typesafe/jev-1.14": {
        "model_revision": "jev-1.14",
        "context_limit": "65536",
    },
}
DEFAULT_JEV_MODEL = "typesafe/jev-1.14"


def provider_protocol_hash(model_id: str) -> str:
    if model_id not in JEV_MODELS:
        raise ValueError("unsupported Jev model")
    material = {
        "adapter": "digitalpsychology-jev-openrouter",
        "adapter_version": "1",
        "api": "openrouter-decisions-alpha-v1",
        "projection": "jev-minimal-semantic-projection/v1",
        "response": "choice-label-and-distribution-v1",
        "security": "redaction-and-bounded-state-v1",
        "model": model_id,
        **JEV_MODELS[model_id],
    }
    return hashlib.sha256(str(sorted(material.items())).encode()).hexdigest()
