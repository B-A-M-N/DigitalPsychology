from pathlib import Path

from lib.laya_configuration import (
    DEFAULT_DESTINATION,
    MODEL_CONTENT_SHA256,
    MODEL_ID,
    MODEL_REVISION,
    model_settings,
)


def test_default_model_settings_resolve_the_pinned_local_checkpoint():
    settings = model_settings({})

    assert settings["model_path"] == str(DEFAULT_DESTINATION)
    assert settings["model_id"] == MODEL_ID
    assert settings["model_revision"] == MODEL_REVISION
    assert settings["model_content_sha256"] == MODEL_CONTENT_SHA256
    assert settings["device"] == "cpu"


def test_model_settings_preserve_explicit_local_overrides():
    settings = model_settings({
        "DIGITALPSYCHOLOGY_LAYA_MODEL_PATH": "/models/laya",
        "DIGITALPSYCHOLOGY_LAYA_DEVICE": "cuda",
    })

    assert settings["model_path"] == str(Path("/models/laya"))
    assert settings["device"] == "cuda"
    assert settings["model_revision"] == MODEL_REVISION
    assert settings["model_content_sha256"] == MODEL_CONTENT_SHA256
