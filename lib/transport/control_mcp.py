"""Host-only DigitalPsychology MCP control surface.

Run this server over a private host-managed transport. Do not add it to the
controlled model's tool registry.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:  # transport stays optional for library-only consumers
    FastMCP = None

from ..control.service import ControlPlaneService
from ..feedback_loop import trusted_state_root
from ..mcp_server import DigitalPsychologyService
from ..runtime.store import BehaviorStateStore
from ..system_one_service import SystemOneService


def _control_from_environment() -> ControlPlaneService:
    state_root = trusted_state_root()
    event_path = Path(os.environ.get(
        "DIGITALPSYCHOLOGY_EVENT_DB", state_root / "events.sqlite3"))
    state_path = Path(os.environ.get(
        "DIGITALPSYCHOLOGY_BEHAVIOR_STATE_DB",
        state_root / "behavior-state.sqlite3"))
    dp_service = DigitalPsychologyService(
        event_db=event_path,
        system_one_service=SystemOneService.from_environment())
    return ControlPlaneService(
        decision_service=dp_service,
        state_store=BehaviorStateStore(state_path))


def create_control_server(control: ControlPlaneService | None = None):
    if FastMCP is None:
        raise RuntimeError("MCP transport dependency is not installed")
    control = control or _control_from_environment()
    server = FastMCP(
        "digital-psychology-control",
        instructions=("Host-only behavioral control protocol. This server applies no "
                      "CFW or CSW changes itself; the trusted host must apply returned "
                      "directives and submit authoritative receipts."),
    )

    @server.tool()
    def open_control_session(session: dict[str, Any]) -> dict[str, Any]:
        """Open or restore a host-bound control session."""
        return control.open_control_session(session)

    @server.tool()
    def prepare_control_tick(request: dict[str, Any]) -> dict[str, Any]:
        """Prepare a decision without committing it or actuating CFW/CSW."""
        return control.prepare_control_tick(request)

    @server.tool()
    def commit_control_tick(receipt: dict[str, Any]) -> dict[str, Any]:
        """Commit a prepared decision after the host confirms local application."""
        return control.commit_control_tick(receipt)

    @server.tool()
    def abort_control_tick(receipt: dict[str, Any]) -> dict[str, Any]:
        """Abort a prepared decision after host compensation or report recovery."""
        return control.abort_control_tick(receipt)

    @server.tool()
    def reconcile_control_session(report: dict[str, Any]) -> dict[str, Any]:
        """Reconcile persisted DP state against current host authority revisions."""
        return control.reconcile_control_session(report)

    @server.tool()
    def close_control_session(request: dict[str, Any]) -> dict[str, Any]:
        """Close a session when no control decision remains prepared."""
        return control.close_control_session(request)

    return server


def main() -> None:
    control = _control_from_environment()
    server = create_control_server(control)
    try:
        server.run("stdio")
    finally:
        control.close()


if __name__ == "__main__":
    main()


__all__ = ["create_control_server", "main"]
