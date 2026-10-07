from __future__ import annotations

from types import SimpleNamespace

import pytest

from lib.transport import admin_mcp, control_mcp, mcp_compat
from lib.host.mcp_client import MCPControlClient


def test_control_and_admin_mcp_share_pinned_dependency_loader():
    assert control_mcp.MCPServer is mcp_compat.MCPServer
    assert admin_mcp.MCPServer is mcp_compat.MCPServer
    if mcp_compat.MCPServer is not None:
        assert control_mcp.create_control_server(control=SimpleNamespace()) is not None
        assert admin_mcp.create_admin_server(service=SimpleNamespace()) is not None


def test_private_control_mcp_uses_stage_specific_deadlines():
    client = MCPControlClient(command="python", args=(), env={})
    assert client.startup_timeout_seconds == 8
    assert client.operation_timeouts["prepare_control_tick"] == 5
    assert client.operation_timeouts["commit_control_tick"] == 5
    assert client.operation_timeouts["abort_control_tick"] == 3
    assert client.operation_timeouts["reconcile_control_session"] == 6

    configured = MCPControlClient(
        command="python", args=(), startup_timeout_seconds=7,
        operation_timeouts={"prepare_control_tick": 2},
    )
    assert configured.startup_timeout_seconds == 7
    assert configured.operation_timeouts["prepare_control_tick"] == 2
    assert configured.operation_timeouts["commit_control_tick"] == 5

    laya = MCPControlClient(
        command="python", args=(),
        env={"DIGITALPSYCHOLOGY_SEMANTIC_PROVIDER": "laya-native",
             "DIGITALPSYCHOLOGY_SYSTEM_ONE_MODE": "shadow"})
    assert laya.operation_timeouts["prepare_control_tick"] == 45
    assert laya.operation_timeouts["commit_control_tick"] == 5

    legacy = MCPControlClient(command="python", args=(), timeout_seconds=20)
    assert legacy.startup_timeout_seconds == 20
    assert set(legacy.operation_timeouts.values()) == {20}
    assert client.latency_metrics()["prepare_control_tick"]["count"] == 0

    with pytest.raises(ValueError, match="unknown MCP control operations"):
        MCPControlClient(command="python", args=(),
                         operation_timeouts={"unknown_tool": 1})
