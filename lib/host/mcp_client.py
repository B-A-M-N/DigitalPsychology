"""Persistent private MCP client for host initiated DigitalPsychology control."""
from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
import importlib.util
import json
import os
from pathlib import Path
import sys
import time
from typing import Any, Mapping, Sequence

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
except ImportError:  # the in-process host path does not require the MCP extra
    ClientSession = StdioServerParameters = stdio_client = None

from ..control_protocol import PROTOCOL_VERSION
from ..compatibility import validate_control_capabilities


DP_CONTROL_TOOLS = frozenset({
    "open_control_session", "prepare_control_tick", "commit_control_tick",
    "abort_control_tick", "reconcile_control_session", "close_control_session",
    "read_behavioral_trial_record",
})
_CONTROL_TOOLS = DP_CONTROL_TOOLS
_DEFAULT_STARTUP_TIMEOUT_SECONDS = 8.0
_DEFAULT_TOOL_TIMEOUTS = {
    "open_control_session": 8.0,
    "prepare_control_tick": 5.0,
    "commit_control_tick": 5.0,
    "abort_control_tick": 3.0,
    "reconcile_control_session": 6.0,
    "close_control_session": 3.0,
    "read_behavioral_trial_record": 5.0,
}


@asynccontextmanager
async def _timeout_scope(seconds: float):
    """Bound an await without moving it into asyncio.wait_for's child task.

    MCP's AnyIO task groups require async context managers to be entered and
    exited by the same task. This small compatibility scope preserves that
    property on Python 3.10 as well as newer versions.
    """
    task = asyncio.current_task()
    if task is None:
        raise RuntimeError("MCP deadlines require an active asyncio task")
    loop = asyncio.get_running_loop()
    cancelling = getattr(task, "cancelling", lambda: 0)()
    expired = False

    def cancel_current_task() -> None:
        nonlocal expired
        expired = True
        task.cancel()

    timer = loop.call_later(seconds, cancel_current_task)
    try:
        yield
    except asyncio.CancelledError as exc:
        if not expired:
            raise
        uncancel = getattr(task, "uncancel", None)
        if callable(uncancel) and uncancel() > cancelling:
            raise
        raise asyncio.TimeoutError from exc
    finally:
        timer.cancel()


async def _within_timeout(awaitable: Any, seconds: float) -> Any:
    async with _timeout_scope(seconds):
        return await awaitable


class MCPControlClient:
    """Own a separate stdio MCP process that is never registered with the model."""

    def __init__(self, *, command: str | None = None,
                 args: Sequence[str] | None = None,
                 env: Mapping[str, str] | None = None,
                 cwd: str | Path | None = None,
                 timeout_seconds: float | None = None,
                 startup_timeout_seconds: float | None = None,
                 operation_timeouts: Mapping[str, float] | None = None) -> None:
        legacy_timeout = (self._validate_timeout(timeout_seconds, "legacy MCP timeout")
                          if timeout_seconds is not None else None)
        self.startup_timeout_seconds = self._validate_timeout(
            startup_timeout_seconds if startup_timeout_seconds is not None else
            legacy_timeout if legacy_timeout is not None else
            _DEFAULT_STARTUP_TIMEOUT_SECONDS,
            "MCP startup timeout")
        self.operation_timeouts = dict(_DEFAULT_TOOL_TIMEOUTS)
        if legacy_timeout is not None:
            self.operation_timeouts = {
                name: legacy_timeout for name in self.operation_timeouts}
        if operation_timeouts is not None:
            if not isinstance(operation_timeouts, Mapping):
                raise TypeError("MCP operation timeouts must be a mapping")
            unknown_operations = set(operation_timeouts) - set(self.operation_timeouts)
            if unknown_operations:
                raise ValueError(
                    f"unknown MCP control operations: {sorted(unknown_operations)}")
            self.operation_timeouts.update({
                name: self._validate_timeout(value, f"{name} timeout")
                for name, value in operation_timeouts.items()
            })
        self.timeout_seconds = legacy_timeout
        self.command = (command or
                        os.environ.get("DIGITALPSYCHOLOGY_CONTROL_MCP_PYTHON") or
                        sys.executable)
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
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None
        self._lock = asyncio.Lock()
        self._latency = {
            name: {"count": 0, "failures": 0, "total_ms": 0.0, "max_ms": 0.0}
            for name in ("startup", *self.operation_timeouts)
        }

    @staticmethod
    def _validate_timeout(value: Any, label: str) -> float:
        if (type(value) not in (int, float) or not 0 < value <= 300):
            raise ValueError(f"{label} must be between 0 and 300 seconds")
        return float(value)

    def latency_metrics(self) -> dict[str, dict[str, float | int]]:
        """Return bounded per-stage call and elapsed-time counters."""
        return {name: dict(values) for name, values in self._latency.items()}

    def _record_latency(self, name: str, started: float, *, failed: bool) -> None:
        elapsed_ms = max(0.0, (time.perf_counter() - started) * 1000.0)
        values = self._latency[name]
        values["count"] = int(values["count"]) + 1
        values["failures"] = int(values["failures"]) + int(failed)
        values["total_ms"] = float(values["total_ms"]) + elapsed_ms
        values["max_ms"] = max(float(values["max_ms"]), elapsed_ms)

    async def start(self) -> None:
        async with self._lock:
            if self._session is not None:
                return
            started = time.perf_counter()
            if ClientSession is None or StdioServerParameters is None or stdio_client is None:
                self._record_latency("startup", started, failed=True)
                raise MCPControlUnavailable(
                    "MCP transport dependencies are unavailable; use an in-process client")
            stack = AsyncExitStack()
            try:
                loop = asyncio.get_running_loop()
                deadline = loop.time() + self.startup_timeout_seconds

                async def bounded_startup(awaitable):
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        raise asyncio.TimeoutError
                    return await _within_timeout(awaitable, remaining)

                read_stream, write_stream = await bounded_startup(
                    stack.enter_async_context(stdio_client(StdioServerParameters(
                        command=self.command,
                        args=list(self.args),
                        env=self.env,
                        cwd=self.cwd,
                    ))))
                session = await bounded_startup(stack.enter_async_context(
                    ClientSession(read_stream, write_stream)))
                await bounded_startup(session.initialize())
                response = await bounded_startup(session.list_tools())
                names = {item.name for item in response.tools}
            except BaseException as exc:
                try:
                    await _within_timeout(stack.aclose(), self.operation_timeouts[
                        "close_control_session"])
                except Exception:
                    pass
                self._record_latency("startup", started, failed=True)
                if isinstance(exc, Exception):
                    raise MCPControlUnavailable(
                        "DP control MCP failed during startup or handshake") from exc
                raise
            if names != _CONTROL_TOOLS:
                try:
                    await _within_timeout(stack.aclose(), self.operation_timeouts[
                        "close_control_session"])
                except Exception:
                    pass
                self._record_latency("startup", started, failed=True)
                raise RuntimeError(
                    "private DP MCP manifest does not match the host control protocol")
            self._stack = stack
            self._session = session
            self._record_latency("startup", started, failed=False)

    async def close(self) -> None:
        async with self._lock:
            stack, self._stack = self._stack, None
            self._session = None
            if stack is not None:
                try:
                    await _within_timeout(
                        stack.aclose(), self.operation_timeouts["close_control_session"])
                except asyncio.TimeoutError as exc:
                    raise MCPControlUnavailable(
                        "DP MCP transport did not close within its deadline") from exc

    async def open_control_session(self, descriptor: Mapping[str, Any]) -> dict[str, Any]:
        value = await self._call("open_control_session", {"session": dict(descriptor)})
        try:
            validate_control_capabilities(value.get("capabilities"))
        except ValueError as exc:
            raise RuntimeError("DigitalPsychology MCP capabilities are incompatible") from exc
        return value

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

    async def behavioral_trial_record(self, control_session_id: str, *,
                                      trial_id: str) -> dict[str, Any]:
        return await self._call("read_behavioral_trial_record", {"request": {
            "protocol_version": PROTOCOL_VERSION,
            "control_session_id": control_session_id,
            "trial_id": trial_id,
        }})

    async def _call(self, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if name not in _CONTROL_TOOLS:
            raise ValueError("host client cannot invoke a non-control MCP tool")
        if self._session is None:
            await self.start()
        session = self._session
        assert session is not None
        started = time.perf_counter()
        try:
            result = await _within_timeout(
                session.call_tool(name, dict(arguments)), self.operation_timeouts[name])
        except Exception as exc:
            self._record_latency(name, started, failed=True)
            raise MCPControlUnavailable(f"DP MCP transport failed during {name}") from exc
        self._record_latency(name, started, failed=False)
        is_error = getattr(result, "is_error", getattr(result, "isError", False))
        if is_error:
            details = "\n".join(
                item.text for item in result.content
                if getattr(item, "type", None) == "text")
            raise RuntimeError(f"DigitalPsychology MCP {name} failed: {details[:2048]}")
        structured = getattr(
            result, "structured_content", getattr(result, "structuredContent", None))
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
