"""Pinned, allowlisted Jev model identities and provider-protocol bindings."""
from __future__ import annotations

import hashlib
from typing import Mapping

JEV_MODELS: Mapping[str, Mapping[str, object]] = {
    "typesafe/jev-1.13": {
        "model_revision": "jev-1.13",
        "context_limit": 32768,
        "served_model_ids": frozenset({"typesafe/jev-1.13-20260917"}),
    },
    "typesafe/jev-1.14": {
        "model_revision": "jev-1.14",
        "context_limit": 65536,
        "served_model_ids": frozenset({"typesafe/jev-1.14-20261014"}),
    },
}
DEFAULT_JEV_MODEL = "typesafe/jev-1.14"


def served_model_identity(model_id: str, served_model: str) -> dict[str, str]:
    """Bind a documented returned snapshot to the requested allowlisted alias."""
    identity = JEV_MODELS.get(model_id)
    if identity is None or served_model not in identity["served_model_ids"]:
        raise ValueError(f"Jev returned an unrecognized model snapshot: {served_model}")
    content_hash = hashlib.sha256(f"openrouter:{served_model}".encode()).hexdigest()
    return {
        "served_model": served_model,
        "provider_revision": served_model.rsplit("-", 1)[-1],
        "model_content_hash": content_hash,
    }


def provider_protocol_hash(model_id: str) -> str:
    if model_id not in JEV_MODELS:
        raise ValueError("unsupported Jev model")
    material = {
        "adapter": "digitalpsychology-jev-openrouter",
        "adapter_version": "2",
        "api": "openrouter-decisions-alpha-v1",
        "projection": "jev-family-semantic-projection/v2",
        "response": "choice-type-distribution-and-confidence-v1",
        "security": "allowlisted-minimal-facts-v2",
        "model": model_id,
        "served_model_ids": sorted(JEV_MODELS[model_id]["served_model_ids"]),
        "model_revision": JEV_MODELS[model_id]["model_revision"],
        "context_limit": JEV_MODELS[model_id]["context_limit"],
    }
    return hashlib.sha256(str(sorted(material.items())).encode()).hexdigest()
