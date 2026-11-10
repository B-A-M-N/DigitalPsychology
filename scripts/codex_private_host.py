#!/usr/bin/env python3
"""Run Codex through the private CFW/DP host boundary.

The DP control MCP is spawned by HostControlBridge and never enters Codex's
model-facing MCP catalog. Codex receives only the bounded CFW workspace tools
registered by this host; native execution and other MCP servers stay disabled.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import shutil
import sys
import tomllib
import uuid
from pathlib import Path
from typing import Any, Mapping

DP_ROOT = Path(__file__).resolve().parents[1]
CFW_ROOT = Path(os.environ.get("COGNITIVE_FRAMEWORKS_ROOT", DP_ROOT.parent / "CognitiveFrameWorks")).resolve()
CSW_ROOT = Path(os.environ.get("COGNITIVE_STATEWORK_ROOT", DP_ROOT.parent / "CognitiveStateWork")).resolve()
for source in (CFW_ROOT / "scripts", CSW_ROOT / "scripts", DP_ROOT):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from cognitive_runtime_core.integrations.cfw import DigitalPsychologyHarnessLifecycle
from cognitive_runtime_core.integrations.codex.cfw_host import (
    CodexActionBinding,
    CodexAppServerAdapter,
)
from cognitive_runtime_core.integrations.codex.client import JsonlAppServerClient
from cognitive_runtime_core.integrations.codex.launch import CodexLaunchProfile
from lib.behavior_state import GoalItemSnapshot, GoalSetSnapshot
from lib.host import HostControlBridge, MCPControlClient
from lib.runtime.adapters.cfw import CFWRuntimeAdapter
from lib.runtime.contracts import ActionCandidate, RuntimeIdentity, RuntimeObservation
from runtime_contract.actions import (
    HostExecutionContext,
    ProtectedResourcePolicy,
    default_action_registry,
)
from runtime_contract.task import AcceptanceCriterion, TaskGoalContract, TaskRequest
from runtime_host.host_api import CognitiveRuntime
from digital_psychology_adapter import CFWBehaviorAdapter

_MAX_FILE_READ = 32_000
_MAX_FILE_WRITE = 1_000_000
_SECRET_PART = re.compile(
    r"(?:^|[._-])(?:secret|token|password|credential|api[_-]?key|private[_-]?key)(?:$|[._-])",
    re.IGNORECASE,
)
_SENSITIVE_COMPONENTS = frozenset({
    ".aws", ".ssh", ".codex", ".harvardcodex", ".env", "auth.json",
    "credentials.json", "fistage.txt", "jev.txt",
})
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)([\"\']?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret)[\"\']?\s*[:=]\s*[\"\']?)([^\"\'\s,;}]+)([\"\']?)"
)
_BEARER = re.compile(r"(?i)(\bbearer\s+)[A-Za-z0-9._~+/=-]{12,}")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
_API_TOKEN = re.compile(r"\bsk-[A-Za-z0-9._-]{16,}\b")


def _clean_model_text(value: str) -> str:
    value = _SECRET_ASSIGNMENT.sub(r"\1[redacted]\3", value)
    value = _BEARER.sub(r"\1[redacted]", value)
    value = _JWT.sub("[redacted-token]", value)
    return _API_TOKEN.sub("[redacted-api-key]", value)


def _workspace_path(workspace: Path, raw: Any) -> Path:
    if not isinstance(raw, str) or not raw.strip() or len(raw) > 4096:
        raise ValueError("workspace path must be bounded non-empty text")
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = workspace / candidate
    resolved = candidate.resolve(strict=False)
    try:
        relative = resolved.relative_to(workspace)
    except ValueError as exc:
        raise PermissionError("path escapes the selected workspace") from exc
    parts = relative.parts
    if any(part.casefold() in _SENSITIVE_COMPONENTS
           or part.casefold().startswith(".env.")
           or part.casefold() == ".envrc"
           or _SECRET_PART.search(part) for part in parts):
        raise PermissionError("sensitive paths are not available through Codex host tools")
    return resolved


def _list_directory(path: Path, workspace: Path) -> list[str]:
    names = []
    for child in sorted(path.iterdir(), key=lambda item: item.name.casefold()):
        try:
            _workspace_path(workspace, child.relative_to(workspace).as_posix())
        except (PermissionError, ValueError):
            continue
        names.append(child.name)
        if len(names) >= 256:
            break
    return names


def _read_file(workspace: Path, arguments: Mapping[str, Any]) -> dict[str, Any]:
    path = _workspace_path(workspace, arguments.get("path"))
    if not path.is_file():
        raise FileNotFoundError("requested workspace file does not exist")
    full_payload = path.read_bytes()
    digest = hashlib.sha256(full_payload).hexdigest()
    truncated = len(full_payload) > _MAX_FILE_READ
    payload = full_payload[:_MAX_FILE_READ]
    text = payload.decode("utf-8", errors="replace")
    return {
        "path": path.relative_to(workspace).as_posix(),
        "content": _clean_model_text(text),
        "sha256": digest,
        "truncated": truncated,
    }


def _inspect_path(workspace: Path, arguments: Mapping[str, Any]) -> dict[str, Any]:
    path = _workspace_path(workspace, arguments.get("path", "."))
    if path.is_dir():
        entries = _list_directory(path, workspace)
        return {"path": path.relative_to(workspace).as_posix() or ".", "entries": entries}
    if path.is_file():
        return {"path": path.relative_to(workspace).as_posix(), "type": "file"}
    raise FileNotFoundError("requested workspace path does not exist")


def _write_file(workspace: Path, arguments: Mapping[str, Any]) -> dict[str, Any]:
    path = _workspace_path(workspace, arguments.get("path"))
    content = arguments.get("content")
    if not isinstance(content, str) or len(content.encode("utf-8")) > _MAX_FILE_WRITE:
        raise ValueError("file content must be text up to 1 MB")
    if path.exists() and path.is_dir():
        raise IsADirectoryError("target is a directory")
    path.parent.mkdir(parents=True, exist_ok=True)
    # Re-resolve after creating parents to catch a symlink introduced in the path.
    path = _workspace_path(workspace, path.relative_to(workspace).as_posix())
    staging = path.with_name(f".{path.name}.codex-dp-partial-{uuid.uuid4().hex}")
    try:
        staging.write_text(content, encoding="utf-8")
        os.replace(staging, path)
    finally:
        try:
            staging.unlink()
        except FileNotFoundError:
            pass
    return {"path": path.relative_to(workspace).as_posix(),
            "bytes_written": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _profile_runtime(profile: str) -> tuple[list[str], dict[str, str], str, CodexLaunchProfile | None]:
    env = {
        key: os.environ[key]
        for key in ("PATH", "HOME", "LANG", "LC_ALL", "TERM", "TMPDIR")
        if key in os.environ
    }
    if profile == "codex":
        codex_home = Path.home() / ".codex"
        binary = shutil.which("codex")
        if not binary:
            raise RuntimeError("Codex CLI is unavailable on PATH")
        env["CODEX_HOME"] = str(codex_home)
        command = [binary, "app-server"]
        launch_profile = None
    else:
        codex_home = Path(os.environ.get("HARVARDCODEX_HOME", Path.home() / ".harvardcodex"))
        binary = shutil.which("codex")
        if not binary:
            raise RuntimeError("Codex CLI is unavailable on PATH")
        env["CODEX_HOME"] = str(codex_home)
        env["HARVARDCODEX_HOME"] = str(codex_home)
        api_key = os.environ.get("FREEINFERENCE_API_KEY")
        if not isinstance(api_key, str) or not api_key.strip():
            raise RuntimeError("HarvardCodex provider environment is unavailable")
        env["FREEINFERENCE_API_KEY"] = api_key
        env["FI_ALLOW_INSECURE_LOCALHOST"] = "1"
        config = tomllib.loads((codex_home / "config.toml").read_text(encoding="utf-8"))
        provider_id = str(config.get("model_provider") or "freeinference")
        provider = config.get("model_providers", {}).get(provider_id)
        if not isinstance(provider, Mapping):
            raise RuntimeError("HarvardCodex provider configuration is unavailable")
        launch_profile = CodexLaunchProfile(
            binary=binary, auth_mode="external_provider", codex_home=codex_home,
            provider_id=provider_id, provider_base_url=str(provider.get("base_url") or ""),
            provider_env_key=str(provider.get("env_key") or "FREEINFERENCE_API_KEY"),
            wire_api=str(provider.get("wire_api") or "responses"),
            requires_openai_auth=bool(provider.get("requires_openai_auth", False)),
            secret_env_keys=(str(provider.get("env_key") or "FREEINFERENCE_API_KEY"),),
            provider_name=str(provider.get("name") or provider_id))
        command = [binary, "app-server"]
    return command, env, _configured_model(codex_home), launch_profile


def _configured_model(codex_home: Path) -> str:
    config_path = codex_home / "config.toml"
    try:
        config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        config = {}
    model = config.get("model")
    if not isinstance(model, str) or not model.strip():
        return "gpt-6-luna"
    return model.strip()[:128]


def _dp_environment(state_root: Path) -> dict[str, str]:
    parent = os.environ
    env = {key: parent[key] for key in ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR")
           if key in parent}
    env.update({
        "PYTHONPATH": os.pathsep.join((str(CFW_ROOT / "scripts"),
                                        str(CSW_ROOT / "scripts"), str(DP_ROOT))),
        "DIGITALPSYCHOLOGY_ROOT": str(DP_ROOT),
        "DIGITALPSYCHOLOGY_STATE_ROOT": str(state_root / "dp-state"),
        "DIGITALPSYCHOLOGY_EVENT_DB": str(state_root / "dp-events.sqlite3"),
        "DIGITALPSYCHOLOGY_BEHAVIOR_STATE_DB": str(state_root / "dp-behavior.sqlite3"),
    })
    return env


def _make_task(task: str, workspace: Path, model: str, task_id: str) -> TaskRequest:
    criterion_id = "task-outcome"
    goal = TaskGoalContract(
        objective=task[:4096], deliverable_kind="code_behavior", work_mode="delivery",
        acceptance_criteria=(AcceptanceCriterion(
            criterion_id, "Address the requested workspace task and report observed results.",
            "code_behavior", verification_strength="host_observation"),),
        task_complexity="complex")
    return TaskRequest(
        task_id=task_id, application_id="digital-psychology-codex-host",
        application_instance_id="digital-psychology-codex-host",
        namespace_id="local-codex", agent_id="codex",
        shape="implement", model=model,
        harness="codex-app-server", harness_version="host-adapter-v1",
        toolset="cfw-workspace-actions-v1", goal_contract=goal)


async def _run(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace).expanduser().resolve(strict=True)
    if not workspace.is_dir():
        raise ValueError("workspace must be a directory")
    task = args.task.strip()
    if args.check_only:
        task = task or "Check the private DigitalPsychology and CFW host integration."
    elif not task:
        raise ValueError("provide a task after --")
    task_id = "codex-dp-" + uuid.uuid4().hex
    state_parent = Path(os.environ.get(
        "XDG_STATE_HOME", Path.home() / ".local" / "state")) / "digitalpsychology" / "codex-host"
    state_parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(state_parent, 0o700)
    state_root = state_parent / task_id
    state_root.mkdir(mode=0o700)

    command, codex_env, configured_model, launch_profile = _profile_runtime(args.profile)
    model = (args.model or configured_model).strip()
    if not model or len(model) > 128:
        raise ValueError("model must be bounded non-empty text")
    request = _make_task(task, workspace, model, task_id)
    criterion_ids = tuple(item.criterion_id for item in request.goal_contract.acceptance_criteria)
    protected = ProtectedResourcePolicy.default()
    protected_roots = set(protected.protected_roots)
    protected_roots.add(str((workspace / ".git").resolve()))
    host_policy = ProtectedResourcePolicy(
        protected_roots=tuple(sorted(protected_roots)),
        authorized_write_roots=(str(workspace),))
    host_context = HostExecutionContext(
        principal="local-codex", task_scope=task_id,
        filesystem_scope=(str(workspace),), task_id=task_id,
        protected_resources=host_policy)
    runtime = CognitiveRuntime(host_context=host_context)
    actions = (
        CodexActionBinding(
            name="cfw_read", description="Read a bounded, non-sensitive workspace file.",
            input_schema={"type": "object", "properties": {
                "path": {"type": "string", "maxLength": 4096}},
                "required": ["path"], "additionalProperties": False},
            action_binding_id="read", descriptor=default_action_registry()["read"],
            executor=lambda values: _read_file(workspace, values)),
        CodexActionBinding(
            name="cfw_inspect", description="List a workspace directory or inspect a file path.",
            input_schema={"type": "object", "properties": {
                "path": {"type": "string", "maxLength": 4096}},
                "required": ["path"], "additionalProperties": False},
            action_binding_id="inspect", descriptor=default_action_registry()["inspect"],
            executor=lambda values: _inspect_path(workspace, values)),
        CodexActionBinding(
            name="cfw_write", description="Write text to a non-sensitive workspace file.",
            input_schema={"type": "object", "properties": {
                "path": {"type": "string", "maxLength": 4096},
                "content": {"type": "string", "maxLength": _MAX_FILE_WRITE}},
                "required": ["path", "content"], "additionalProperties": False},
            action_binding_id="write", descriptor=default_action_registry()["write"],
            executor=lambda values: _write_file(workspace, values)),
    )

    def bridge_factory(*, request: TaskRequest, task_handle: Any,
                       session_id: str, **_: Any) -> HostControlBridge:
        behavior = task_handle.create_component_adapter(CFWBehaviorAdapter)
        cfw = CFWRuntimeAdapter(behavior)
        subject = request.subject_ref or request.task_id
        identity = RuntimeIdentity(
            namespace_id=request.namespace_id, application_id=request.application_id,
            application_instance_id=request.application_instance_id,
            agent_id=request.agent_id,
            agent_instance_id=task_handle.agent_instance_id,
            session_id=session_id, task_id=request.task_id,
            attempt_id=request.attempt_id, workflow_id=f"private:{task_id}",
            subject_ref=subject, segment_id="segment-0", behavioral_subject=subject,
            goal_contract_hash=str(task_handle.bundle.pinned.get("goal_contract_hash") or "") or None)
        initial_goals = GoalSetSnapshot(revision=0, items=(GoalItemSnapshot(
            criterion_id=criterion_ids[0], claim=request.goal_contract.objective[:512]),))
        client = MCPControlClient(
            command=sys.executable, args=("-m", "lib.transport.control_mcp"),
            cwd=DP_ROOT, env=_dp_environment(state_root))
        return HostControlBridge(
            identity=identity, objective=request.goal_contract.objective,
            objective_criteria=criterion_ids, initial_goal_set=initial_goals,
            cfw=cfw, csw=None, client=client,
            host_policy_context={"deliverable_kind": request.goal_contract.deliverable_kind,
                                 "codex_native_actions_closed": True})

    def candidate_factory(*, action: Mapping[str, Any], binding: str,
                          invocation_id: str, **_: Any) -> ActionCandidate:
        parameter_digest = str(action.get("parameter_digest") or "")
        return ActionCandidate(
            action_class="file_change" if binding == "write" else "read",
            proposed_purpose=("Update a workspace artifact" if binding == "write"
                              else "Inspect a workspace artifact"),
            affected_criterion_ids=criterion_ids, action_id=invocation_id,
            tool_name="cfw_" + binding, operation=binding,
            target_ref_digest=hashlib.sha256(parameter_digest.encode()).hexdigest(),
            mutating=binding == "write", reversible=True,
            relation_certainty="explicit", host_declared_relation="advances_primary_goal")

    def observation_factory(*, boundary: str, binding: str | None = None,
                            bridge: HostControlBridge, evidence_ref: str | None = None,
                            **_: Any) -> RuntimeObservation:
        completed = boundary == "post_action"
        items = bridge.goal_set.items
        status_map = {item.criterion_id: {
            "READY": "unmet", "ACTIVE": "in_progress", "VERIFIED": "verified",
            "BLOCKED": "blocked", "DEPENDENCY_BLOCKED": "blocked",
            "DEFERRED_BY_OPERATOR": "blocked"}[item.status] for item in items}
        return RuntimeObservation(
            action_index=max(0, bridge._action_index + (1 if completed else 0)),
            action_completed=completed, artifact_changed=completed and binding == "write",
            goal_set=bridge.goal_set, criterion_status=status_map,
            evidence_refs=(evidence_ref,) if evidence_ref else ())

    lifecycle = DigitalPsychologyHarnessLifecycle(
        bridge_factory=bridge_factory, component_adapter_factory=CFWBehaviorAdapter,
        require_shared_csw=False, candidate_factory=candidate_factory,
        observation_factory=observation_factory)
    adapter = CodexAppServerAdapter(
        request=request, action_bindings=actions, model=model, cwd=workspace,
        runtime=runtime, runtime_harness_lifecycle=lifecycle,
        runtime_mode="standalone_enforcing", client_name="digital-psychology-codex-host",
        client_title="DigitalPsychology Private Codex Host", client_version="1.0.0")
    client = None
    try:
        launch_environment = ({launch_profile.provider_env_key: codex_env[launch_profile.provider_env_key]}
                              if launch_profile is not None and launch_profile.provider_env_key
                              else None)
        client = await JsonlAppServerClient.launch(
            cwd=workspace, command=command, env=codex_env,
            launch_profile=launch_profile, launch_environment=launch_environment)
        status = await adapter.open(client)
        lifecycle_status = status.get("runtime_harness_lifecycle_status") or {}
        if (status.get("model_visible_dp_tools") != 0
                or lifecycle_status.get("private_dp_bridge_open") is not True
                or lifecycle_status.get("cfw_authority_verified") is not True):
            raise RuntimeError("private DP/CFW host handshake did not verify")
        if args.check_only:
            print(f"{args.profile}: private DP/CFW bridge ready; DP control tools visible to model: 0")
            return 0
        await adapter.run_turn(task, timeout=args.timeout)
        answer = adapter._last_assistant_message.strip()
        if not answer:
            raise RuntimeError("Codex completed without an assistant response")
        print(_clean_model_text(answer))
        return 0
    finally:
        try:
            await adapter.close()
        except Exception:
            if client is not None:
                await client.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a Codex turn with a private DigitalPsychology/CFW host bridge.")
    parser.add_argument("--profile", choices=("codex", "harvardcodex"), required=True)
    parser.add_argument("--workspace", default=os.getcwd(),
                        help="workspace directory; defaults to the current directory")
    parser.add_argument("--model", help="optional model override")
    parser.add_argument("--timeout", type=float, default=900.0)
    parser.add_argument("--check-only", action="store_true",
                        help="open the private host bridge without sending a model turn")
    parser.add_argument("task", nargs=argparse.REMAINDER,
                        help="task text after --")
    args = parser.parse_args()
    args.task = " ".join(args.task).removeprefix("--").strip()
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        # App Server and provider stderr are suppressed by the client. Keep
        # diagnostics local and type-only so credentials cannot enter terminal output.
        print(f"digital-psychology Codex host failed: {type(exc).__name__}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
