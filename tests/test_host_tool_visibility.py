import asyncio

import pytest

from lib.host.tool_visibility import (
    DP_ADMIN_TOOLS, assert_dp_tools_not_model_visible,
)
from lib.host.mcp_client import DP_CONTROL_TOOLS
from lib.behavior_state import GoalItemSnapshot, GoalSetSnapshot
from lib.runtime.contracts import RuntimeIdentity
from lib.runtime.host import RuntimeHost


def test_private_dp_tool_names_are_rejected_from_agent_registry():
    assert_dp_tools_not_model_visible(("read", "write", "search"))
    for private in sorted(DP_CONTROL_TOOLS | DP_ADMIN_TOOLS):
        with pytest.raises(RuntimeError, match="model-visible"):
            assert_dp_tools_not_model_visible(("read", private))


def test_standalone_admin_mcp_requires_explicit_enablement(monkeypatch):
    from lib.transport import admin_mcp

    monkeypatch.delenv("DIGITALPSYCHOLOGY_ENABLE_ADMIN_MCP", raising=False)
    monkeypatch.setattr(
        admin_mcp, "DigitalPsychologyService",
        lambda: pytest.fail("admin service must not start while disabled"))
    with pytest.raises(RuntimeError, match="admin MCP is disabled"):
        admin_mcp.main()


def test_control_bridge_requires_the_controlled_agents_actual_tool_registry():
    identity = RuntimeIdentity(
        namespace_id="ns", application_id="app", application_instance_id="instance",
        agent_instance_id="agent-instance", session_id="session", task_id="task",
        attempt_id="attempt", workflow_id="workflow", subject_ref="repo:task",
        segment_id="segment", agent_id="agent", behavioral_subject="repo:task")
    goals = GoalSetSnapshot(revision=0, items=(
        GoalItemSnapshot(criterion_id="criterion"),))

    async def exercise():
        with pytest.raises(RuntimeError, match="tool registry"):
            await RuntimeHost().open_control_bridge(
                identity=identity, objective="Complete the task",
                objective_criteria=("criterion",), initial_goal_set=goals)

    asyncio.run(exercise())
