"""Reliable MCP stdio transport for subprocess pipes.

The MCP SDK's default stdio transport wraps inherited text streams in AnyIO's
threaded file adapter. On supported Python 3.11 hosts that adapter can block
forever in ``TextIOWrapper.readline()`` for a subprocess pipe. This adapter
keeps the SDK's JSON-RPC parser and server loop while reading and writing raw
UTF-8 bytes from the pipe.
"""
from __future__ import annotations

import os
import sys
from typing import Any

try:
    import asyncio
    import anyio
    from mcp.server.stdio import stdio_server
except ImportError:  # MCP remains optional for library-only consumers.
    anyio = None
    stdio_server = None


class _RawLineInput:
    """Async line source backed by a duplicated OS pipe descriptor."""

    def __init__(self, fd: int) -> None:
        self._fd = fd
        self._buffer = bytearray()
        self._eof = False

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        while True:
            newline = self._buffer.find(b"\n")
            if newline >= 0:
                line = bytes(self._buffer[:newline + 1])
                del self._buffer[:newline + 1]
                return line.decode("utf-8", errors="replace")
            if self._eof:
                if self._buffer:
                    line = bytes(self._buffer)
                    self._buffer.clear()
                    return line.decode("utf-8", errors="replace")
                raise StopAsyncIteration
            chunk = await _read_pipe(self._fd, 65536)
            if chunk:
                self._buffer.extend(chunk)
            else:
                self._eof = True


class _RawTextOutput:
    """Async text sink that writes complete UTF-8 data to a pipe descriptor."""

    def __init__(self, fd: int) -> None:
        self._fd = fd

    async def write(self, value: str) -> None:
        data = value.encode("utf-8")
        await asyncio.to_thread(self._write_all, data)

    def _write_all(self, data: bytes) -> None:
        view = memoryview(data)
        while view:
            written = os.write(self._fd, view)
            if written <= 0:
                raise BrokenPipeError("MCP stdio pipe closed during write")
            view = view[written:]

    async def flush(self) -> None:
        return None


async def _wait_for_descriptor(fd: int) -> None:
    loop = asyncio.get_running_loop()
    ready = loop.create_future()

    def signal_ready() -> None:
        if not ready.done():
            ready.set_result(None)

    loop.add_reader(fd, signal_ready)
    try:
        await ready
    finally:
        loop.remove_reader(fd)


async def _read_pipe(fd: int, size: int) -> bytes:
    if os.name == "nt":
        return await asyncio.to_thread(os.read, fd, size)
    await _wait_for_descriptor(fd)
    return os.read(fd, size)


async def run_stdio_server_async(server: Any) -> None:
    """Run an MCPServer over stdio without threaded TextIOWrapper reads."""
    if anyio is None or stdio_server is None:
        raise RuntimeError("MCP transport dependency is not installed")
    sys.stdout.flush()
    wire_stdin, wire_stdout = os.dup(0), os.dup(1)
    null_stdin = os.open(os.devnull, os.O_RDONLY)
    try:
        os.dup2(null_stdin, 0)
        os.dup2(2, 1)
        async with stdio_server(
                stdin=_RawLineInput(wire_stdin),
                stdout=_RawTextOutput(wire_stdout)) as (read_stream, write_stream):
            await server._lowlevel_server.run(
                read_stream,
                write_stream,
                server._lowlevel_server.create_initialization_options(),
            )
    finally:
        os.dup2(wire_stdin, 0)
        os.dup2(wire_stdout, 1)
        os.close(null_stdin)
        os.close(wire_stdin)
        os.close(wire_stdout)


def run_stdio_server(server: Any) -> None:
    """Synchronous launcher matching ``MCPServer.run('stdio')``."""
    if anyio is None:
        raise RuntimeError("MCP transport dependency is not installed")
    anyio.run(run_stdio_server_async, server)


__all__ = ["run_stdio_server", "run_stdio_server_async"]
