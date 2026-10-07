"""CFW host compatibility types over neutral Codex protocol helpers."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from cognitive_runtime_core.integrations.codex.protocol import (
    MAX_INFLIGHT_SERVER_REQUESTS,
    MAX_RPC_LINE_BYTES,
    MAX_TOOL_OUTPUT_BYTES,
    MAX_TOOL_SCHEMA_BYTES,
    AppServerProtocolError,
    AppServerRPCError,
    CodexExecutionPhase,
    CodexWireMethod,
    SAFE_TOOL_NAME,
    _BEARER,
    _SECRET_KEY,
    _VALIDATOR_ID,
    _json_bytes,
    _public_json,
    _safe_error,
    _safe_text,
    _safe_value,
)
from runtime_contract.task import TaskRequest

# Keep the old import name while allowing hosts to choose safe tool names.
_TOOL_NAME = SAFE_TOOL_NAME


def _goal_contract_data(request: TaskRequest) -> dict[str, Any]:
    contract: Any = request.goal_contract
    if contract is None and isinstance(request.observation_context, Mapping):
        contract = request.observation_context.get("goal_contract")
    serializer = getattr(contract, "to_dict", None)
    if callable(serializer):
        contract = serializer()
    return dict(contract) if isinstance(contract, Mapping) else {}
