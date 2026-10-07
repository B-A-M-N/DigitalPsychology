"""Pinned closure policy for Codex native action surfaces."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence

_NATIVE_MODEL_TOOL_FEATURES = (
    # Native model-callable integrations and alternate execution surfaces.
    "agent_message_board", "apps", "auth_elicitation", "browser_use",
    "browser_use_external", "browser_use_full_cdp_access", "code_mode",
    "computer_use", "codex_apps_mcp_2026_07_28", "enable_mcp_apps",
    "exec_permission_approvals", "image_generation", "in_app_browser",
    "in_app_chat", "in_app_dictation", "in_app_local_automation",
    "in_app_updates", "multi_agent", "multi_agent_v2", "parallel",
    "plugin_sharing", "plugins", "remote_plugin", "request_permissions_tool",
    "rmcp_client", "skill_mcp_dependency_install", "skill_search", "skills",
    "sqlite", "tool_call_mcp_elicitation", "tool_suggest",
    "unified_exec_tty", "unified_exec_zsh_fork", "view_image",
    "shell_zsh_fork", "web_search_request", "worktrees",
    "write_stdin_approval",
)
# These features can start work or add persistent/local behavior without being
# a registered host action. Keep App Server transport infrastructure enabled.
_NATIVE_SIDE_EFFECT_FEATURES = (
    "daemon_auto_start", "goals", "hooks", "memories", "prevent_idle_sleep",
    "realtime_conversation", "shell_snapshot", "shell_snapshot_v2", "sleep_tool",
)
_NATIVE_EXEC_FEATURES = ("shell_tool", "unified_exec")
_NATIVE_WORKSPACE_FEATURES = ("workspace_dependencies",)
_HOST_ONLY_FEATURES = (set(_NATIVE_EXEC_FEATURES) | set(_NATIVE_MODEL_TOOL_FEATURES)
                       | set(_NATIVE_SIDE_EFFECT_FEATURES)
                       | set(_NATIVE_WORKSPACE_FEATURES))
NATIVE_FEATURE_CATALOG_VERSION = "codex-native-host-policy-v1"
NATIVE_FEATURE_CATALOG_SHA256 = "82b363a94a1372a9ef42cafe5b160c67f38bf2502583d34858faa97d8b4bada2"
REVIEWED_CODEX_CLI_VERSION = "codex-cli 0.160.0"
REVIEWED_CODEX_FEATURE_CATALOG_SHA256 = (
    "3310a31f8ec0d7a985fb07cf6043bc060dc5c438c4b23058e14b9b1144b86714")


def _native_feature_policy_identity() -> dict[str, str]:
    groups = {
        "model_callable_disabled": sorted(_NATIVE_MODEL_TOOL_FEATURES),
        "execution_disabled": sorted(_NATIVE_EXEC_FEATURES),
        "persistent_side_effects_disabled": sorted(_NATIVE_SIDE_EFFECT_FEATURES),
        "workspace_dependency_features_disabled": sorted(_NATIVE_WORKSPACE_FEATURES),
    }
    canonical = json.dumps({"version": NATIVE_FEATURE_CATALOG_VERSION,
                            "groups": groups}, sort_keys=True,
                           separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(canonical).hexdigest()
    if digest != NATIVE_FEATURE_CATALOG_SHA256:
        raise RuntimeError(
            "native feature policy catalog is unknown; update its version and pinned hash")
    return {"version": NATIVE_FEATURE_CATALOG_VERSION, "policy_sha256": digest}


def _native_feature_catalog_identity(supported_features: Sequence[str],
                                     cli_version: str) -> dict[str, object]:
    """Pin the installed CLI version and its complete discovered feature list."""
    policy = _native_feature_policy_identity()
    feature_names = sorted(set(supported_features))
    canonical = json.dumps({"codex_cli_version": cli_version,
                            "feature_names": feature_names}, sort_keys=True,
                           separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(canonical).hexdigest()
    if (cli_version != REVIEWED_CODEX_CLI_VERSION
            or digest != REVIEWED_CODEX_FEATURE_CATALOG_SHA256):
        raise RuntimeError(
            "Codex CLI feature catalog is unknown; review and pin its version and full feature list")
    return {
        **policy,
        "codex_cli_version": cli_version,
        "feature_names": feature_names,
        "catalog_sha256": digest,
    }


def _host_only_feature_args(supported_features: set[str]) -> list[str]:
    _native_feature_policy_identity()
    native_exec = [feature for feature in _NATIVE_EXEC_FEATURES
                   if feature in supported_features]
    if not native_exec:
        raise RuntimeError(
            "Codex CLI exposes no recognized native execution feature flag; "
            "refusing to launch App Server")
    # shell_tool and unified_exec are distinct CLI generations. The CLI also
    # exposes a code_mode_host service, but that service is not the model-facing
    # tool; disabling code_mode closes that entrypoint while keeping App Server
    # infrastructure available for host-registered dynamicTools.
    disabled = list(dict.fromkeys((
        *native_exec, *(feature for feature in (
            *_NATIVE_MODEL_TOOL_FEATURES, *_NATIVE_SIDE_EFFECT_FEATURES,
            *_NATIVE_WORKSPACE_FEATURES)
                       if feature in supported_features))))
    return [argument for feature in disabled
            for argument in ("--disable", feature)]
