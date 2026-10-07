"""Pinned local defaults for the optional native Laya runtime."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping


MODEL_ID = "laya-typed-decisions"
MODEL_REPOSITORY = "convaiinnovations/laya-typed-decisions"
MODEL_REVISION = "1a793eb568e6718f15941d08f85432581df534e3"
MODEL_CONTENT_SHA256 = "cda55a3e793f4c4268a25066ceb1568aa302fd210d96656e3032756fd722b642"
WEIGHTS_SHA256 = "4fa56de72383a9d3efa9cfa78955733c81b9fc8067a587ca4beb82c78107a24e"
DEFAULT_DESTINATION = (
    Path.home() / ".cache/laya/convaiinnovations-laya-typed-decisions-1a793eb")


def model_settings(environment: Mapping[str, str] | None = None) -> dict[str, str]:
    """Resolve explicit pins or the repository's immutable local checkpoint."""
    env = os.environ if environment is None else environment
    return {
        "model_path": env.get("DIGITALPSYCHOLOGY_LAYA_MODEL_PATH",
                              str(DEFAULT_DESTINATION)),
        "model_revision": env.get("DIGITALPSYCHOLOGY_LAYA_MODEL_REVISION",
                                  MODEL_REVISION),
        "model_content_sha256": env.get("DIGITALPSYCHOLOGY_LAYA_MODEL_SHA256",
                                        MODEL_CONTENT_SHA256),
        "model_id": env.get("DIGITALPSYCHOLOGY_LAYA_MODEL_ID", MODEL_ID),
        "device": env.get("DIGITALPSYCHOLOGY_LAYA_DEVICE", "cpu"),
    }


__all__ = ["DEFAULT_DESTINATION", "MODEL_CONTENT_SHA256", "MODEL_ID",
           "MODEL_REPOSITORY", "MODEL_REVISION", "WEIGHTS_SHA256",
           "model_settings"]
