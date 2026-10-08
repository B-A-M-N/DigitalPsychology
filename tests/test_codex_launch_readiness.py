from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from cognitive_runtime_core.integrations.codex.client import (
    _codex_inspection_environment,
)
from cognitive_runtime_core.integrations.codex.launch import (
    CodexLaunchConfigurationError,
    CodexLaunchProfile,
    resolve_packaged_codex_binary,
)
from cognitive_runtime_core.integrations.codex.feature_policy import (
    reviewed_codex_feature_catalog,
)
from cognitive_runtime_core.integrations.codex.readiness import (
    CodexInfrastructureUnavailable,
    inspect_inference_readiness,
)


def _profile(tmp_path: Path, *, mode: str = "external_provider") -> CodexLaunchProfile:
    values = {
        "binary": "/packaged/codex",
        "auth_mode": mode,
        "codex_home": tmp_path / "codex-home",
    }
    if mode == "external_provider":
        values.update({
            "provider_id": "freeinference",
            "provider_base_url": "http://127.0.0.1:18769/v1",
            "provider_env_key": "FREEINFERENCE_API_KEY",
            "secret_env_keys": ("FREEINFERENCE_API_KEY",),
        })
    return CodexLaunchProfile(**values)


class _ReadinessClient:
    def __init__(self, account: object, requirements: object, *, error: Exception | None = None):
        self.account = account
        self.requirements = requirements
        self.error = error

    async def request(self, method, params=None, *, timeout=30.0):
        if self.error is not None:
            raise self.error
        if method == "account/read":
            return self.account
        if method == "configRequirements/read":
            return self.requirements
        raise AssertionError(method)


def _readiness(client: _ReadinessClient, profile: CodexLaunchProfile):
    return asyncio.run(inspect_inference_readiness(client, profile))


def test_packaged_binary_must_be_inside_standalone_release(tmp_path):
    release = tmp_path / ".codex/packages/standalone/releases/1.0.0/bin"
    release.mkdir(parents=True)
    binary = release / "codex"
    binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    binary.chmod(0o700)

    assert resolve_packaged_codex_binary(str(binary), home=tmp_path) == str(binary)

    unrelated = tmp_path / "bin/codex"
    unrelated.parent.mkdir()
    unrelated.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    unrelated.chmod(0o700)
    with pytest.raises(CodexLaunchConfigurationError):
        resolve_packaged_codex_binary(str(unrelated), home=tmp_path)


def test_external_provider_readiness_rejects_chatgpt_workspace_routing(tmp_path):
    profile = _profile(tmp_path)
    client = _ReadinessClient(
        {"account": {"type": "chatgpt"}, "workspaceRouting": {
            "chatgptAccountId": "acct", "backendOrigin": "https://example.test",
            "accountRoutingOverride": "us"}},
        {"requirements": {"modelProvider": "freeinference"}})

    with pytest.raises(CodexInfrastructureUnavailable) as captured:
        _readiness(client, profile)

    assert captured.value.readiness.failure_class == "provider_configuration_mismatch"
    assert captured.value.readiness.workspace_routing_required is False


def test_external_provider_readiness_maps_workspace_failure_to_mismatch(tmp_path):
    with pytest.raises(CodexInfrastructureUnavailable) as captured:
        _readiness(_ReadinessClient({}, {}, error=RuntimeError(
            "workspace routing discovery failed")), _profile(tmp_path))

    assert captured.value.readiness.failure_class == "provider_configuration_mismatch"


def test_external_provider_readiness_rejects_managed_provider_drift(tmp_path):
    client = _ReadinessClient(
        {"account": {"type": "apiKey"}, "requiresOpenaiAuth": False},
        {"requirements": {"modelProvider": "openai"}})

    with pytest.raises(CodexInfrastructureUnavailable) as captured:
        _readiness(client, _profile(tmp_path))

    assert captured.value.readiness.failure_class == "provider_configuration_mismatch"


def test_inspection_environment_does_not_receive_provider_credentials(tmp_path):
    profile = _profile(tmp_path)
    environment = {
        "PATH": "/usr/bin", "FREEINFERENCE_API_KEY": "fi-secret",
        "OPENAI_API_KEY": "openai-secret", "ANTHROPIC_API_KEY": "anthropic-secret",
    }

    inspected = _codex_inspection_environment(environment, profile)

    assert inspected == {"PATH": "/usr/bin"}
    assert environment["FREEINFERENCE_API_KEY"] == "fi-secret"


def test_reviewed_packaged_codex_feature_catalog_is_accepted():
    version = "codex-cli 0.160.1"
    digest = "3fe988b7cd6e943692cc315f98824cdda73be8c382971b9b1fa8ec5ed7bb8696"

    assert reviewed_codex_feature_catalog(version, digest)
    assert not reviewed_codex_feature_catalog(version, "0" * 64)
