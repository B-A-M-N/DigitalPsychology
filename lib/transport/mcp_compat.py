"""Load the MCP server API supported by the pinned MCP dependency range."""
from __future__ import annotations


def load_server_class():
    try:
        from mcp.server.mcpserver import MCPServer
        return MCPServer
    except ImportError:
        try:
            from mcp.server.fastmcp import FastMCP
            return FastMCP
        except ImportError:
            return None


MCPServer = load_server_class()

__all__ = ["MCPServer", "load_server_class"]
