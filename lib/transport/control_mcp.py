"""Host-only DigitalPsychology MCP control surface.

Run this server over a private host-managed transport. Do not add it to the
controlled model's tool registry.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..control.service import ControlPlaneService
from ..control_protocol import PROTOCOL_VERSION, bounded_control_mapping
from ..feedback_loop import trusted_state_root
from ..service import DigitalPsychologyService
from ..runtime.store import BehaviorStateStore
from ..system_one_service import SystemOneService
from .stdio import run_stdio_server
from .mcp_compat import MCPServer


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
    if MCPServer is None:
        raise RuntimeError("MCP transport dependency is not installed")
    control = control or _control_from_environment()
    server = MCPServer(
        "digital-psychology-control",
        instructions=("Host-only behavioral control protocol. This server applies no "
                      "CFW or CSW changes itself; the trusted host must apply returned "
                      "directives and submit authoritative receipts."),
    )

    # MCP 2.x's sync tool wrapper stalls on this private stdio child. These
    # bounded DP operations run inline through async handlers; model inference
    # remains capped by the System-1 provider deadline.
    @server.tool()
    async def open_control_session(session: dict[str, Any]) -> dict[str, Any]:
        """Open or restore a host-bound control session."""
        return control.open_control_session(session)

    @server.tool()
    async def prepare_control_tick(request: dict[str, Any]) -> dict[str, Any]:
        """Prepare a decision without committing it or actuating CFW/CSW."""
        return control.prepare_control_tick(request)

    @server.tool()
    async def commit_control_tick(receipt: dict[str, Any]) -> dict[str, Any]:
        """Commit a prepared decision after the host confirms local application."""
        return control.commit_control_tick(receipt)

    @server.tool()
    async def abort_control_tick(receipt: dict[str, Any]) -> dict[str, Any]:
        """Abort a prepared decision after host compensation or report recovery."""
        return control.abort_control_tick(receipt)

    @server.tool()
    async def reconcile_control_session(report: dict[str, Any]) -> dict[str, Any]:
        """Reconcile persisted DP state against current host authority revisions."""
        return control.reconcile_control_session(report)

    @server.tool()
    async def close_control_session(request: dict[str, Any]) -> dict[str, Any]:
        """Close a session when no control decision remains prepared."""
        return control.close_control_session(request)

    @server.tool()
    async def read_behavioral_trial_record(request: dict[str, Any]) -> dict[str, Any]:
        """Read DP's internal evidence for an externally evaluated trial."""
        data = bounded_control_mapping(request, "behavioral trial record request")
        if set(data) != {"protocol_version", "control_session_id", "trial_id"}:
            raise ValueError("behavioral trial record request fields are invalid")
        if data["protocol_version"] != PROTOCOL_VERSION:
            raise ValueError("behavioral trial record protocol version is invalid")
        for name in ("control_session_id", "trial_id"):
            value = data[name]
            if not isinstance(value, str) or not value or len(value) > 256:
                raise ValueError(f"{name} must be bounded text")
        return control.behavioral_trial_record(
            data["control_session_id"], trial_id=data["trial_id"])

    @server.tool()
    async def record_system_one_outcome(request: dict[str, Any]) -> dict[str, Any]:
        """Record an independently evidenced label from the trusted host."""
        return control.record_system_one_outcome(request)

    return server


def main() -> None:
    control = _control_from_environment()
    server = create_control_server(control)
    try:
        run_stdio_server(server)
    finally:
        control.close()


if __name__ == "__main__":
    main()


__all__ = ["create_control_server", "main"]
