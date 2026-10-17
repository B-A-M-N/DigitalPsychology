from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.system_one_service import SystemOneService


def _base_env(monkeypatch, tmp_path, providers=None, singular=None, mode="shadow",
              key="test-key"):
    monkeypatch.setenv("DIGITALPSYCHOLOGY_SYSTEM_ONE_MODE", mode)
    if providers is not None:
        monkeypatch.setenv("DIGITALPSYCHOLOGY_SEMANTIC_PROVIDERS", providers)
    else:
        monkeypatch.delenv("DIGITALPSYCHOLOGY_SEMANTIC_PROVIDERS", raising=False)
    if singular is not None:
        monkeypatch.setenv("DIGITALPSYCHOLOGY_SEMANTIC_PROVIDER", singular)
    else:
        monkeypatch.delenv("DIGITALPSYCHOLOGY_SEMANTIC_PROVIDER", raising=False)
    monkeypatch.setenv("DIGITALPSYCHOLOGY_LAYA_MODEL_PATH", str(tmp_path / "missing-laya"))
    if key is None:
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    else:
        monkeypatch.setenv("OPENROUTER_API_KEY", key)
    monkeypatch.setenv("DIGITALPSYCHOLOGY_JEV_ENABLED", "true")


def test_disabled_environment_does_not_import_laya(tmp_path, monkeypatch):
    monkeypatch.delenv("DIGITALPSYCHOLOGY_SEMANTIC_PROVIDER", raising=False)
    monkeypatch.delenv("DIGITALPSYCHOLOGY_SEMANTIC_PROVIDERS", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("DIGITALPSYCHOLOGY_LAYA_MODEL_PATH", str(tmp_path / "missing"))
    service = SystemOneService.from_environment()
    assert service.capability()["backend_id"] == "null"
    service.close()


def test_singular_jev_environment_uses_class_factory(tmp_path, monkeypatch):
    _base_env(monkeypatch, tmp_path, singular="jev-openrouter")
    service = SystemOneService.from_environment()
    try:
        assert service.capability()["backend_id"] == "digitalpsychology-jev-openrouter"
        assert service.capability()["model_id"] == "typesafe/jev-1.14"
    finally:
        service.close()


def test_singular_plural_variable_does_not_mutate_environment(tmp_path, monkeypatch):
    _base_env(monkeypatch, tmp_path, providers="jev-openrouter")
    service = SystemOneService.from_environment()
    try:
        assert service.capability()["backend_id"] == "digitalpsychology-jev-openrouter"
    finally:
        service.close()
    import os
    assert os.environ.get("DIGITALPSYCHOLOGY_SEMANTIC_PROVIDER") is None


def test_conflicting_provider_configuration_is_rejected(tmp_path, monkeypatch):
    _base_env(monkeypatch, tmp_path, providers="jev-openrouter",
              singular="jev-openrouter")
    with pytest.raises(ValueError, match="select either"):
        SystemOneService.from_environment()


def test_duplicate_portfolio_providers_are_rejected(tmp_path, monkeypatch):
    _base_env(monkeypatch, tmp_path, providers="jev-openrouter,jev-openrouter")
    with pytest.raises(ValueError, match="unique"):
        SystemOneService.from_environment()


def test_invalid_portfolio_mode_is_rejected(tmp_path, monkeypatch):
    _base_env(monkeypatch, tmp_path, providers="jev-openrouter,laya-native", mode="disabled")
    with pytest.raises(ValueError, match="portfolio requires an enabled"):
        SystemOneService.from_environment()


def test_missing_jev_credential_is_rejected(tmp_path, monkeypatch):
    _base_env(monkeypatch, tmp_path, singular="jev-openrouter", key=None)
    with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
        SystemOneService.from_environment()


def test_plural_provider_portfolio_constructs_independent_runtimes(tmp_path, monkeypatch):
    try:
        import laya  # noqa: F401
    except ImportError:
        pytest.skip("Laya optional dependency is unavailable")
    _base_env(monkeypatch, tmp_path, providers="laya-native,jev-openrouter",
              key="test-key")
    service = SystemOneService.from_environment()
    try:
        assert service.is_portfolio
        assert [runtime.backend_id for runtime in service.runtimes] == [
            "digitalpsychology-laya-native", "digitalpsychology-jev-openrouter"]
        assert service.runtime is service.runtimes[0]
    finally:
        service.close()


def test_canonical_calibration_is_loaded_for_portfolio(tmp_path, monkeypatch):
    from lib.system_one_calibration import CalibrationBundle
    try:
        import laya  # noqa: F401
        from tests.test_system_one_runtime import calibration_record
    except Exception:
        pytest.skip("portfolio fixture dependencies unavailable")
    record = calibration_record()
    bundle = CalibrationBundle({record.question_id: record})
    path = tmp_path / "active-calibration.json"
    path.write_text(json.dumps(bundle.to_dict()), encoding="utf-8")
    _base_env(monkeypatch, tmp_path, providers="laya-native,jev-openrouter")
    monkeypatch.setenv("DIGITALPSYCHOLOGY_SYSTEM_ONE_CALIBRATION_PATH", str(path))
    service = SystemOneService.from_environment()
    try:
        assert service.calibration_bundle_hash == bundle.digest
        for runtime in service.runtimes:
            assert runtime.calibration_hash == bundle.digest
    finally:
        service.close()


def test_portfolio_lifecycle_broadcasts_to_all_providers(tmp_path, monkeypatch):
    from lib.system_one_contracts import SystemOneIdentity
    try:
        import laya  # noqa: F401
    except ImportError:
        pytest.skip("Laya optional dependency is unavailable")
    _base_env(monkeypatch, tmp_path, providers="laya-native,jev-openrouter")
    service = SystemOneService.from_environment()
    identity = SystemOneIdentity(
        namespace_id="n", application_id="a", application_instance_id="i",
        agent_id="agent", agent_instance_id="instance", session_id="s",
        task_id="t", attempt_id="a")
    try:
        service.reset_identity(identity)
        service.close()
        with pytest.raises(RuntimeError):
            service.reset_identity(identity)
    finally:
        try:
            service.close()
        except Exception:
            pass
