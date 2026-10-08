"""Explicit, credential-safe launch profiles for generic Codex App Server."""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import stat
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

AuthMode = Literal["chatgpt", "openai_api_key", "external_provider"]
_ENV_KEY = re.compile(r"^[A-Z_][A-Z0-9_]{0,127}$")
_PROVIDER_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


class CodexLaunchConfigurationError(ValueError):
    """A safe-to-report launch configuration or credential error."""


def resolve_codex_home(environment: Mapping[str, str] | None = None) -> Path:
    """Resolve the Codex profile directory using the App Server launch rules."""
    env = os.environ if environment is None else environment
    supplied = env.get("CODEX_HOME")
    return Path(supplied).expanduser().resolve() if supplied else Path.home() / ".codex"


def resolve_packaged_codex_binary(configured: str | None = None, *,
                                  home: Path | None = None) -> str:
    """Resolve Codex only from an installed standalone release directory."""
    candidate = configured or shutil.which("codex")
    if not candidate:
        raise CodexLaunchConfigurationError(
            "packaged Codex CLI binary is unavailable")
    account_home = (home or Path.home()).expanduser()
    try:
        binary = Path(candidate).expanduser().resolve(strict=True)
        releases = (account_home / ".codex" / "packages" / "standalone"
                    / "releases").resolve(strict=True)
    except OSError:
        raise CodexLaunchConfigurationError(
            "packaged Codex CLI binary is unavailable") from None
    if (not binary.is_file() or releases not in binary.parents
            or not os.access(binary, os.X_OK)):
        raise CodexLaunchConfigurationError(
            "Codex binary must identify an installed standalone release")
    return str(binary)


@dataclass(frozen=True)
class CodexLaunchProfile:
    binary: str
    auth_mode: AuthMode
    codex_home: Path
    provider_id: str | None = None
    provider_base_url: str | None = None
    provider_env_key: str | None = None
    wire_api: str = "responses"
    requires_openai_auth: bool = False
    secret_env_keys: tuple[str, ...] = ()
    auth_source_home: Path | None = None
    provider_name: str | None = None

    def __post_init__(self) -> None:
        if self.auth_mode not in {"chatgpt", "openai_api_key", "external_provider"}:
            raise CodexLaunchConfigurationError("unsupported Codex auth mode")
        if not isinstance(self.binary, str) or not self.binary.strip():
            raise CodexLaunchConfigurationError("Codex CLI binary must be non-empty")
        if not isinstance(self.codex_home, Path):
            object.__setattr__(self, "codex_home", Path(self.codex_home))
        if self.auth_source_home is not None and not isinstance(
                self.auth_source_home, Path):
            object.__setattr__(self, "auth_source_home", Path(self.auth_source_home))
        keys = tuple(self.secret_env_keys)
        if len(keys) > 16 or any(not isinstance(key, str) or not _ENV_KEY.fullmatch(key)
                                  for key in keys):
            raise CodexLaunchConfigurationError("invalid secret environment key names")
        if len(set(keys)) != len(keys):
            raise CodexLaunchConfigurationError("secret environment key names must be unique")
        if self.auth_mode == "external_provider":
            if (not isinstance(self.provider_id, str)
                    or not _PROVIDER_ID.fullmatch(self.provider_id)):
                raise CodexLaunchConfigurationError("external provider id is invalid")
            if not isinstance(self.provider_env_key, str) or not _ENV_KEY.fullmatch(
                    self.provider_env_key):
                raise CodexLaunchConfigurationError("external provider env key is invalid")
            parsed = urlsplit(str(self.provider_base_url or ""))
            if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                    or parsed.username or parsed.password or parsed.query or parsed.fragment):
                raise CodexLaunchConfigurationError("external provider base URL is invalid")
            if parsed.scheme == "http" and parsed.hostname not in {
                    "127.0.0.1", "localhost", "::1"}:
                raise CodexLaunchConfigurationError(
                    "external provider HTTP URLs must use loopback")
            if self.wire_api not in {"responses", "chat_completions"}:
                raise CodexLaunchConfigurationError("external provider wire API is invalid")
            if self.requires_openai_auth:
                raise CodexLaunchConfigurationError(
                    "external auth providers must not require OpenAI auth")
            if (self.provider_name is not None
                    and (not isinstance(self.provider_name, str)
                         or not self.provider_name.strip()
                         or len(self.provider_name) > 128
                         or any(ord(character) < 32
                                for character in self.provider_name))):
                raise CodexLaunchConfigurationError(
                    "external provider display name is invalid")
            if self.provider_env_key not in keys:
                object.__setattr__(self, "secret_env_keys", (*keys, self.provider_env_key))
        elif any(value is not None for value in (
                self.provider_id, self.provider_base_url, self.provider_env_key,
                self.provider_name)):
            raise CodexLaunchConfigurationError(
                "provider fields are only valid in external-provider mode")
        if self.auth_mode == "openai_api_key" and self.secret_env_keys not in (
                (), ("OPENAI_API_KEY",)):
            raise CodexLaunchConfigurationError(
                "OpenAI API-key mode only accepts OPENAI_API_KEY")


def provider_environment(profile: CodexLaunchProfile,
                         parent: Mapping[str, str]) -> dict[str, str]:
    """Copy only explicitly named credential variables; never log their values."""
    result: dict[str, str] = {}
    for key in profile.secret_env_keys:
        value = parent.get(key)
        if not isinstance(value, str) or not value.strip():
            raise CodexLaunchConfigurationError(
                f"required provider environment variable is missing: {key}")
        if "\x00" in value:
            raise CodexLaunchConfigurationError(
                f"required provider environment variable is invalid: {key}")
        result[key] = value
    return result


def load_named_secret(path: Path, key: str) -> str:
    """Read one named env-file value without evaluating shell syntax."""
    if not _ENV_KEY.fullmatch(key):
        raise CodexLaunchConfigurationError("credential variable name is invalid")
    try:
        metadata = path.stat()
    except OSError:
        raise CodexLaunchConfigurationError(
            f"credential environment file is unavailable for {key}") from None
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077:
        raise CodexLaunchConfigurationError(
            "credential environment file must be a private regular file")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        raise CodexLaunchConfigurationError(
            f"credential environment file is unreadable for {key}") from None
    found: list[str] = []
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        name, separator, raw_value = line.partition("=")
        if not separator or name.strip() != key:
            continue
        try:
            parts = shlex.split(raw_value.strip(), comments=False, posix=True)
        except ValueError:
            raise CodexLaunchConfigurationError(
                f"credential value has invalid quoting for {key}") from None
        if len(parts) != 1 or not parts[0].strip():
            raise CodexLaunchConfigurationError(
                f"credential value is empty or invalid for {key}")
        found.append(parts[0])
    if len(found) != 1:
        raise CodexLaunchConfigurationError(
            f"credential environment file must define {key} exactly once")
    return found[0]


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=True)


def _base_config(model: str) -> str:
    return "\n".join((
        f"model = {_toml_string(model)}",
        'approval_policy = "never"',
        'sandbox_mode = "read-only"',
        "mcp_servers = {}",
        'web_search = "disabled"',
        "",
    ))


def prepare_codex_home(profile: CodexLaunchProfile, *, model: str,
                       parent_environment: Mapping[str, str],
                       api_key: str | None = None) -> None:
    """Create an isolated CODEX_HOME with no inherited config or MCP catalog."""
    if not isinstance(model, str) or not model.strip() or len(model) > 128:
        raise CodexLaunchConfigurationError("model name must be bounded non-empty text")
    home = profile.codex_home.expanduser().resolve()
    home.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.chmod(home, 0o700)
    if any(home.iterdir()):
        raise CodexLaunchConfigurationError(
            "temporary CODEX_HOME must be empty before preparation")

    config = _base_config(model)
    if profile.auth_mode == "external_provider":
        assert profile.provider_id is not None
        assert profile.provider_base_url is not None
        assert profile.provider_env_key is not None
        config += "\n".join((
            f"model_provider = {_toml_string(profile.provider_id)}",
            "",
            f"[model_providers.{profile.provider_id}]",
            f"name = {_toml_string(profile.provider_name or profile.provider_id)}",
            f"base_url = {_toml_string(profile.provider_base_url)}",
            f"env_key = {_toml_string(profile.provider_env_key)}",
            f"wire_api = {_toml_string(profile.wire_api)}",
            f"requires_openai_auth = {str(profile.requires_openai_auth).lower()}",
            "",
        ))
    config_path = home / "config.toml"
    descriptor = os.open(config_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(config)
        stream.flush()
        os.fsync(stream.fileno())

    if profile.auth_mode == "chatgpt":
        source_home = profile.auth_source_home
        if source_home is None:
            configured_home = parent_environment.get("CODEX_HOME")
            source_home = Path(configured_home).expanduser() if configured_home else (
                Path.home() / ".codex")
        source_auth = source_home.expanduser() / "auth.json"
        try:
            auth_stat = source_auth.stat()
        except OSError:
            raise CodexLaunchConfigurationError(
                "ChatGPT auth file is not available in the selected credential home") from None
        if not stat.S_ISREG(auth_stat.st_mode):
            raise CodexLaunchConfigurationError("ChatGPT auth file must be a regular file")
        target_auth = home / "auth.json"
        shutil.copyfile(source_auth, target_auth)
        os.chmod(target_auth, 0o600)
    elif profile.auth_mode == "openai_api_key":
        key = api_key or parent_environment.get("OPENAI_API_KEY")
        if not isinstance(key, str) or not key.strip():
            raise CodexLaunchConfigurationError(
                "OPENAI_API_KEY is required for temporary API-key login")
        login_env = {
            name: str(parent_environment[name])
            for name in ("PATH", "HOME", "TMPDIR", "TEMP", "LANG", "LC_ALL")
            if name in parent_environment
        }
        login_env["CODEX_HOME"] = str(home)
        completed = subprocess.run(
            [profile.binary, "login", "--with-api-key"], input=key + "\n",
            text=True, env=login_env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=30, check=False)
        if completed.returncode != 0:
            raise CodexLaunchConfigurationError(
                "Codex rejected the API key for temporary authentication")
