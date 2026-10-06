"""Guard the boundary between private DP MCP tools and agent-visible tools."""
from __future__ import annotations

from typing import Sequence

from .mcp_client import DP_CONTROL_TOOLS


DP_ADMIN_TOOLS = frozenset({
    "ingest_behavior_event", "behavioral_advice", "validate_routing_profile",
    "run_registered_slow_loop",
})


def assert_dp_tools_not_model_visible(model_visible_tools: Sequence[str]) -> None:
    """Reject a host tool registry that exposes either DP MCP surface."""
    if (not isinstance(model_visible_tools, Sequence)
            or isinstance(model_visible_tools, (str, bytes))
            or len(model_visible_tools) > 512
            or any(not isinstance(item, str) or not item or len(item) > 256
                   for item in model_visible_tools)):
        raise ValueError("model-visible tool registry must be a bounded sequence of names")
    visible = set(model_visible_tools)
    private = visible & (DP_CONTROL_TOOLS | DP_ADMIN_TOOLS)
    if private:
        raise RuntimeError(
            "private DigitalPsychology MCP tools are model-visible: "
            + ", ".join(sorted(private)))


__all__ = ["DP_ADMIN_TOOLS", "assert_dp_tools_not_model_visible"]
