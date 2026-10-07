"""Shared protocol types and bounded host-facing serialization."""
from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from enum import IntEnum
from typing import Any

MAX_RPC_LINE_BYTES = 4 * 1024 * 1024
MAX_TOOL_OUTPUT_BYTES = 32 * 1024
MAX_TOOL_SCHEMA_BYTES = 16 * 1024
MAX_INFLIGHT_SERVER_REQUESTS = 64
SAFE_TOOL_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_SECRET_KEY = re.compile(r"(secret|token|password|credential|authorization|api[_-]?key)", re.IGNORECASE)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*")
_VALIDATOR_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")


class CodexWireMethod:
    """Codex App Server JSON-RPC methods owned by this integration."""

    INITIALIZE = "initialize"
    INITIALIZED = "initialized"
    THREAD_START = "thread/start"
    THREAD_RESUME = "thread/resume"
    TURN_START = "turn/start"
    TURN_INTERRUPT = "turn/interrupt"
    DYNAMIC_TOOL_CALL = "item/tool/call"
    COMMAND_APPROVAL = "item/commandExecution/requestApproval"
    FILE_CHANGE_APPROVAL = "item/fileChange/requestApproval"
    PATCH_APPROVAL = "item/applyPatch/requestApproval"
    TURN_STARTED = "turn/started"
    ITEM_STARTED = "item/started"
    ITEM_COMPLETED = "item/completed"
    AGENT_MESSAGE_DELTA = "item/agentMessage/delta"
    TURN_COMPLETED = "turn/completed"
    THREAD_STATUS_CHANGED = "thread/status/changed"
    THREAD_CLOSED = "thread/closed"

class AppServerProtocolError(RuntimeError):
    """Malformed, oversized, or out-of-order App Server protocol data."""

class AppServerRPCError(RuntimeError):
    def __init__(self, code: int, message: str) -> None:
        self.code = code
        super().__init__(f"Codex App Server error {code}: {message[:256]}")

class CodexExecutionPhase(IntEnum):
    PROCESS_STARTED = 1
    INITIALIZED = 2
    INFERENCE_READY = 3
    THREAD_OPEN = 4
    TURN_ACCEPTED = 5
    MODEL_ACTIVITY_OBSERVED = 6
    ACTION_ACTIVITY_OBSERVED = 7
    VALIDATION_OBSERVED = 8
    COMPLETION_CHECKED = 9
    TERMINAL = 10

def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str).encode("utf-8")

def _safe_text(value: str, limit: int = 4096) -> str:
    text = _BEARER.sub("Bearer [redacted]", value)
    text = re.sub(
        r'''(?i)(["']?(?:api[_-]?key|token|password|secret|credential|authorization)["']?\s*:\s*["']?)[^"'\s,}]+''',
        r"\1[redacted]", text)
    text = re.sub(
        r"(?i)\b(api[_-]?key|token|password|secret|credential|authorization)\s*[:=]\s*[^\s,;]+",
        r"\1=[redacted]", text)
    return text[:limit]

def _safe_value(value: Any, *, depth: int = 0) -> Any:
    """Bound and redact values crossing the model-facing tool result boundary."""
    if depth > 8:
        return "[depth-limited]"
    if value is None or type(value) in (bool, int):
        return value
    if type(value) is float:
        return value if math.isfinite(value) else "[non-finite-number]"
    if isinstance(value, str):
        return _safe_text(value)
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 64:
                result["_truncated"] = True
                break
            name = str(key)[:128]
            if _SECRET_KEY.search(name):
                result[name] = "[redacted]"
            else:
                result[name] = _safe_value(item, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        return [_safe_value(item, depth=depth + 1) for item in value[:128]]
    return _safe_text(str(value))

def _public_json(value: Any, *, limit: int = MAX_TOOL_OUTPUT_BYTES) -> str:
    safe = _safe_value(value)
    encoded = json.dumps(safe, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, default=str)
    if len(encoded.encode("utf-8")) > limit:
        encoded = encoded.encode("utf-8")[:limit].decode("utf-8", "ignore")
        encoded += "…[truncated]"
    return encoded

def _safe_error(error: Any) -> dict[str, Any]:
    if not isinstance(error, Mapping):
        return {}
    code = error.get("code")
    message = error.get("message")
    return {
        "code": code if type(code) is int else None,
        "message": _safe_text(message, 256) if isinstance(message, str) else None,
    }
