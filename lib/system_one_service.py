"""Optional host-only System-1 composition for DigitalPsychologyService."""
from __future__ import annotations

import json
import os
from pathlib import Path

from .system_one_backends import LayaSystemOneBackend, SystemOneBackend
from .system_one_calibration import CalibrationBundle
from .system_one_contracts import SystemOneFrame, SystemOneIdentity
from .system_one_runtime import SystemOneResult, SystemOneRuntime


class SystemOneService:
    """Internal behavioral judge; methods are intentionally outside MCP tools."""

    def __init__(self, backend: SystemOneBackend, *, mode: str = "disabled",
                 calibration_bundle: CalibrationBundle | None = None,
                 cache_size: int = 128, max_concurrent_requests: int = 4) -> None:
        if mode not in {"disabled", "shadow", "advisory"}:
            raise ValueError("System-1 mode must be disabled, shadow, or advisory")
        if mode == "advisory" and not calibration_bundle:
            raise ValueError("advisory System-1 mode requires an active calibration bundle")
        self.mode = mode
        self.runtime = SystemOneRuntime(
            backend, calibrations=calibration_bundle,
            cache_size=cache_size,
            max_concurrent_requests=max_concurrent_requests)

    @classmethod
    def from_environment(cls) -> "SystemOneService":
        """Opt in explicitly to the locally downloaded Laya checkpoint."""
        mode = os.environ.get("DIGITALPSYCHOLOGY_SYSTEM_ONE_MODE", "disabled")
        backend = LayaSystemOneBackend.from_environment()
        calibration_path = os.environ.get("DIGITALPSYCHOLOGY_SYSTEM_ONE_CALIBRATION_PATH", "")
        if calibration_path:
            value = json.loads(Path(calibration_path).read_text(encoding="utf-8"))
            calibration_bundle = CalibrationBundle.from_mapping(value)
        else:
            calibration_bundle = CalibrationBundle({})
        return cls(backend, mode=mode, calibration_bundle=calibration_bundle)

    @property
    def cache_size(self) -> int:
        return self.runtime.cache_size

    @property
    def max_latency_ms(self) -> int:
        return self.runtime.max_latency_ms

    @property
    def calibration_bundle_hash(self) -> str:
        return self.runtime.calibration_hash

    def capability(self):
        return self.runtime.capability()

    def judge(self, frame: SystemOneFrame, *, mode: str | None = None) -> SystemOneResult:
        return self.runtime.judge(frame, mode=mode or self.mode)

    def reset_identity(self, identity: SystemOneIdentity) -> None:
        self.runtime.reset_identity(identity)

    def resync(self, frame: SystemOneFrame) -> None:
        self.runtime.resync(frame)

    def close(self) -> None:
        close = getattr(self.runtime.backend, "close", None)
        if callable(close):
            close()


__all__ = ["SystemOneService"]
