"""In-process transport for the host-only DP control protocol.

The client calls the same ``ControlPlaneService`` methods as the private MCP
transport. It keeps transaction logic in one service and changes only the
transport boundary for embedded deployments and tests.
"""
from __future__ import annotations

from typing import Any, Mapping

from ..control_protocol import PROTOCOL_VERSION


class InProcessControlClient:
    """Async host client that invokes a local ControlPlaneService directly."""

    def __init__(self, control_service: Any, *, close_service: bool = False) -> None:
        required = (
            "open_control_session", "prepare_control_tick", "commit_control_tick",
            "abort_control_tick", "reconcile_control_session", "close_control_session",
            "behavioral_trial_record",
        )
        missing = [name for name in required
                   if not callable(getattr(control_service, name, None))]
        if missing:
            raise TypeError(
                "in-process control service is missing methods: " + ", ".join(missing))
        self.control_service = control_service
        self.close_service = bool(close_service)

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        if self.close_service:
            close = getattr(self.control_service, "close", None)
            if callable(close):
                close()

    async def open_control_session(self, descriptor: Mapping[str, Any]) -> dict[str, Any]:
        return self._call("open_control_session", descriptor)

    async def prepare_control_tick(self, request: Mapping[str, Any]) -> dict[str, Any]:
        return self._call("prepare_control_tick", request)

    async def commit_control_tick(self, receipt: Mapping[str, Any]) -> dict[str, Any]:
        return self._call("commit_control_tick", receipt)

    async def abort_control_tick(self, receipt: Mapping[str, Any]) -> dict[str, Any]:
        return self._call("abort_control_tick", receipt)

    async def reconcile_control_session(self, report: Mapping[str, Any]) -> dict[str, Any]:
        return self._call("reconcile_control_session", report)

    async def close_control_session(self, request: Mapping[str, Any]) -> dict[str, Any]:
        return self._call("close_control_session", request)

    async def behavioral_trial_record(self, control_session_id: str, *,
                                      trial_id: str) -> dict[str, Any]:
        response = self.control_service.behavioral_trial_record(
            control_session_id, trial_id=trial_id)
        if not isinstance(response, Mapping):
            raise RuntimeError("in-process control service returned a malformed result")
        return self._validated(response)

    def _call(self, method: str, value: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise TypeError("in-process control request must be a mapping")
        response = getattr(self.control_service, method)(dict(value))
        if not isinstance(response, Mapping):
            raise RuntimeError("in-process control service returned a malformed result")
        return self._validated(response)

    @staticmethod
    def _validated(response: Mapping[str, Any]) -> dict[str, Any]:
        result = dict(response)
        if result.get("protocol_version") != PROTOCOL_VERSION:
            raise RuntimeError("DigitalPsychology control protocol version mismatch")
        return result


__all__ = ["InProcessControlClient"]
