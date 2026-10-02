"""Transport failure classification and host degraded-state reporting."""
from __future__ import annotations

import asyncio
from typing import Any, Mapping

from .mcp_client import MCPControlUnavailable

class HostDegradation:
    """Responsibility-focused host bridge collaborator."""

    def __init__(self, bridge: Any) -> None:
        self.host = bridge

    def _status(self, result: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return {
            "status": "degraded" if self.host._fault else "ready",
            "control_session_id": self.host.identity.scope,
            "behavior_revision": self.host._state.revision,
            "host_action_index": self.host._action_index,
            "behavior_state": self.host._state.to_dict(),
            "result": dict(result) if result is not None else None,
            "fault": self.host._fault,
        }

    def _degraded(self, message: str, failure: Exception) -> Mapping[str, Any]:
        self.host._fault = f"{message}: {failure}"
        self.host._notify_fault(self.host._fault)
        return self.host._status()

    def _notify_fault(self, message: str) -> None:
        if callable(self.host.fault_handler):
            self.host.fault_handler(message)

    @staticmethod
    def _transport_failure(error: Exception) -> bool:
        return isinstance(error, (MCPControlUnavailable, ConnectionError, OSError,
                                  TimeoutError, asyncio.TimeoutError))
