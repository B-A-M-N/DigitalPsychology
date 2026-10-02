"""Persistent private MCP client for host initiated DigitalPsychology control."""
from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from ..control_protocol import PROTOCOL_VERSION


_CONTROL_TOOLS = frozenset({
    "open_control_session", "prepare_control_tick", "commit_control_tick",
    "abort_control_tick", "reconcile_control_session", "close_control_session",
})


class MCPControlClient:
    """Own a separate stdio MCP process that is never registered with the model."""

    def __init__(self, *, command: str | None = None,
                 args: Sequence[str] | None = None,
                 env: Mapping[str, str] | None = None,
                 cwd: str | Path | None = None,
                 timeout_seconds: float = 30.0) -> None:
        if not isinstance(timeout_seconds, (int, float)) or not 0 < timeout_seconds <= 300:
            raise ValueError("MCP control timeout must be between 0 and 300 seconds")
        self.command = command or sys.executable
        if args is None:
            if importlib.util.find_spec("digital_psychology") is not None:
                args = ("-m", "digital_psychology.transport.control_mcp")
            else:
                args = ("-m", "lib.transport.control_mcp")
                if cwd is None:
                    cwd = Path(__file__).resolve().parents[2]
        self.args = tuple(args)
        if (not self.command or len(self.command) > 4096
                or any(not isinstance(arg, str) or len(arg) > 4096 for arg in self.args)
                or len(self.args) > 32):
            raise ValueError("MCP server command is malformed")
        self.env = dict(env) if env is not None else None
        self.cwd = str(cwd) if cwd is not None else None
        self.timeout_seconds = float(timeout_seconds)
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        async with self._lock:
            if self._session is not None:
                return
            stack = AsyncExitStack()
            try:
                read_stream, write_stream = await stack.enter_async_context(
                    stdio_client(StdioServerParameters(
                        command=self.command,
                        args=list(self.args),
                        env=self.env,
                        cwd=self.cwd,
                    )))
                session = await stack.enter_async_context(
                    ClientSession(read_stream, write_stream))
                await asyncio.wait_for(session.initialize(), self.timeout_seconds)
                response = await asyncio.wait_for(
                    session.list_tools(), self.timeout_seconds)
                names = {item.name for item in response.tools}
            except BaseException as exc:
                try:
                    await stack.aclose()
                except Exception:
                    pass
                if isinstance(exc, Exception):
                    raise MCPControlUnavailable(
                        "DP control MCP failed during startup or handshake") from exc
                raise
            if names != _CONTROL_TOOLS:
                await stack.aclose()
                raise RuntimeError(
                    "private DP MCP manifest does not match the host control protocol")
            self._stack = stack
            self._session = session

    async def close(self) -> None:
        async with self._lock:
            stack, self._stack = self._stack, None
            self._session = None
            if stack is not None:
                await stack.aclose()

    async def open_control_session(self, descriptor: Mapping[str, Any]) -> dict[str, Any]:
        return await self._call("open_control_session", {"session": dict(descriptor)})

    async def prepare_control_tick(self, request: Mapping[str, Any]) -> dict[str, Any]:
        return await self._call("prepare_control_tick", {"request": dict(request)})

    async def commit_control_tick(self, receipt: Mapping[str, Any]) -> dict[str, Any]:
        return await self._call("commit_control_tick", {"receipt": dict(receipt)})

    async def abort_control_tick(self, receipt: Mapping[str, Any]) -> dict[str, Any]:
        return await self._call("abort_control_tick", {"receipt": dict(receipt)})

    async def reconcile_control_session(self, report: Mapping[str, Any]) -> dict[str, Any]:
        return await self._call("reconcile_control_session", {"report": dict(report)})

    async def close_control_session(self, request: Mapping[str, Any]) -> dict[str, Any]:
        return await self._call("close_control_session", {"request": dict(request)})

    async def _call(self, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if name not in _CONTROL_TOOLS:
            raise ValueError("host client cannot invoke a non-control MCP tool")
        if self._session is None:
            await self.start()
        session = self._session
        assert session is not None
        try:
            result = await asyncio.wait_for(
                session.call_tool(name, dict(arguments)), self.timeout_seconds)
        except Exception as exc:
            raise MCPControlUnavailable(f"DP MCP transport failed during {name}") from exc
        if result.isError:
            details = "\n".join(
                item.text for item in result.content
                if getattr(item, "type", None) == "text")
            raise RuntimeError(f"DigitalPsychology MCP {name} failed: {details[:2048]}")
        structured = getattr(result, "structuredContent", None)
        if isinstance(structured, Mapping):
            value = dict(structured)
        else:
            text_blocks = [item.text for item in result.content
                           if getattr(item, "type", None) == "text"]
            if len(text_blocks) != 1:
                raise RuntimeError("DigitalPsychology MCP returned no structured result")
            try:
                parsed = json.loads(text_blocks[0])
            except (TypeError, ValueError) as exc:
                raise RuntimeError("DigitalPsychology MCP returned invalid JSON") from exc
            if not isinstance(parsed, Mapping):
                raise RuntimeError("DigitalPsychology MCP result must be an object")
            value = dict(parsed)
        if value.get("protocol_version") != PROTOCOL_VERSION:
            raise RuntimeError("DigitalPsychology MCP protocol version mismatch")
        return value


class MCPControlUnavailable(RuntimeError):
    """The private DP MCP child or transport is unavailable."""


__all__ = ["MCPControlClient", "MCPControlUnavailable"]
