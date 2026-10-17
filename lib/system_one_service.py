"""Optional host-only System-1 composition for DigitalPsychologyService."""
from __future__ import annotations

import json
import os
import warnings
from pathlib import Path

from .system_one_backends import (DisabledSystemOneBackend, LayaProvider,
                                 SemanticProvider, StatelessLoopbackHTTPBackend)
from .system_one_jev import JevOpenRouterProvider, ProviderHealthError
from .system_one_calibration import CalibrationBundle
from .system_one_contracts import SystemOneFrame, SystemOneIdentity
from .system_one_runtime import SystemOneResult, SystemOneRuntime
from .protocol_artifact import QUESTION_REGISTRY


class SystemOneService:
    """Internal behavioral judge; methods are intentionally outside MCP tools."""

    def __init__(self, backend: SemanticProvider, *, mode: str = "disabled",
                 calibration_bundle: CalibrationBundle | None = None,
                 cache_size: int = 128, max_concurrent_requests: int = 4,
                 enabled_question_families: tuple[str, ...] | None = None) -> None:
        if mode not in {"disabled", "shadow", "advisory"}:
            raise ValueError("System-1 mode must be disabled, shadow, or advisory")
        enabled = tuple(QUESTION_REGISTRY if enabled_question_families is None
                        else enabled_question_families)
        if len(set(enabled)) != len(enabled) or any(question not in QUESTION_REGISTRY
                                                    for question in enabled):
            raise ValueError("enabled question families must be registered and unique")
        records = calibration_bundle.records if calibration_bundle else {}
        if mode == "advisory" and not any(
                question in records and records[question].status == "active"
                and records[question].drift_status == "clear" for question in enabled):
            raise ValueError(
                "advisory mode requires at least one active enabled question family")
        self.mode = mode
        self.warmup_latency_ms: int | None = None
        self.runtime_warmed = False
        capability = backend.capability()
        self.enabled_question_families = enabled
        self.runtime = SystemOneRuntime(
            backend, calibrations=calibration_bundle,
            cache_size=cache_size,
            max_concurrent_requests=min(max_concurrent_requests,
                                        capability.max_concurrent_requests),
            enabled_question_families=enabled)

    @classmethod
    def from_environment(cls) -> "SystemOneService":
        """Build a provider-neutral service; optional backends require explicit selection."""
        providers_env = os.environ.get("DIGITALPSYCHOLOGY_SEMANTIC_PROVIDERS", "").strip()
        mode = os.environ.get("DIGITALPSYCHOLOGY_SYSTEM_ONE_MODE", "disabled").strip()
        if providers_env:
            if os.environ.get("DIGITALPSYCHOLOGY_SEMANTIC_PROVIDER", "").strip():
                raise ValueError(
                    "select either the legacy singular provider or the provider list")
            provider_names = tuple(item.strip() for item in providers_env.split(",")
                                   if item.strip())
            if not provider_names or len(provider_names) != len(set(provider_names)):
                raise ValueError(
                    "DIGITALPSYCHOLOGY_SEMANTIC_PROVIDERS must be non-empty and unique")
            if any(item not in {"disabled", "laya-native", "jev-openrouter"}
                   for item in provider_names):
                raise ValueError("unsupported DIGITALPSYCHOLOGY_SEMANTIC_PROVIDERS entry")
            if len(provider_names) == 1:
                if provider_names[0] != "disabled":
                    os.environ["DIGITALPSYCHOLOGY_SEMANTIC_PROVIDER"] = provider_names[0]
                try:
                    return cls.from_environment()
                finally:
                    if provider_names[0] != "disabled":
                        os.environ.pop("DIGITALPSYCHOLOGY_SEMANTIC_PROVIDER", None)
            calibration_path = os.environ.get(
                "DIGITALPSYCHOLOGY_SYSTEM_ONE_CALIBRATION_PATH", "")
            calibration_bundle = CalibrationBundle(
                json.loads(Path(calibration_path).read_text(encoding="utf-8"))
                if calibration_path else {})
            enabled = tuple(filter(None, os.environ.get(
                "DIGITALPSYCHOLOGY_SYSTEM_ONE_QUESTIONS",
                ",".join(QUESTION_REGISTRY)).split(",")))
            if mode == "disabled":
                raise ValueError("a portfolio requires an enabled System-1 mode")
            if len(provider_names) < 2:
                raise ValueError("a portfolio requires at least two providers")
            runtimes = []
            for name in provider_names:
                if name == "disabled":
                    raise ValueError("a disabled provider cannot join an enabled portfolio")
                if name == "laya-native":
                    backend = LayaProvider.from_environment()
                elif name == "jev-openrouter":
                    backend = JevOpenRouterProvider.from_environment(
                        portfolio_explicit=True)
                else:
                    raise ValueError("unsupported portfolio provider")
                runtimes.append(SystemOneRuntime(
                    backend, calibrations=calibration_bundle,
                    cache_size=int(os.environ.get(
                        "DIGITALPSYCHOLOGY_SYSTEM_ONE_CACHE_SIZE", "128")),
                    max_concurrent_requests=int(os.environ.get(
                        "DIGITALPSYCHOLOGY_SYSTEM_ONE_MAX_CONCURRENCY", "4")),
                    enabled_question_families=enabled))
            from .system_one_portfolio import SystemOnePortfolio
            service = cls(DisabledSystemOneBackend(), mode="disabled")
            service.portfolio = SystemOnePortfolio(runtimes)
            service.runtime = runtimes[0]
            service.mode = mode
            service.enabled_question_families = enabled
            return service
        provider = os.environ.get("DIGITALPSYCHOLOGY_SEMANTIC_PROVIDER", "disabled").strip()
        if provider not in {"disabled", "laya-native", "loopback-http", "jev-http",
                            "jev-openrouter"}:
            raise ValueError("unsupported DIGITALPSYCHOLOGY_SEMANTIC_PROVIDER")
        if provider == "jev-http":
            warnings.warn(
                "jev-http is deprecated; use DIGITALPSYCHOLOGY_SEMANTIC_PROVIDER=loopback-http",
                DeprecationWarning, stacklevel=2)
            provider = "loopback-http"
        if mode == "disabled" or provider == "disabled":
            if mode != "disabled" and provider == "disabled":
                raise ValueError("an enabled System-1 mode requires an explicit semantic provider")
            backend: SemanticProvider = DisabledSystemOneBackend()
            mode = "disabled"
        elif provider == "laya-native":
            backend = LayaProvider.from_environment()
        elif provider == "jev-openrouter":
            backend = JevOpenRouterProvider.from_environment()
        else:
            required = {
                "endpoint": os.environ.get("DIGITALPSYCHOLOGY_SYSTEM_ONE_ENDPOINT", ""),
                "model_id": os.environ.get("DIGITALPSYCHOLOGY_SYSTEM_ONE_MODEL_ID", ""),
                "model_revision": os.environ.get("DIGITALPSYCHOLOGY_SYSTEM_ONE_MODEL_REVISION", ""),
                "model_content_hash": os.environ.get("DIGITALPSYCHOLOGY_SYSTEM_ONE_MODEL_SHA256", ""),
                "provider_protocol_hash": os.environ.get("DIGITALPSYCHOLOGY_SYSTEM_ONE_PROVIDER_PROTOCOL_SHA256", ""),
            }
            if any(not value for value in required.values()):
                raise ValueError("loopback-http requires pinned endpoint and provider identity settings")
            backend = StatelessLoopbackHTTPBackend(
                **required,
                timeout_ms=int(os.environ.get("DIGITALPSYCHOLOGY_SYSTEM_ONE_TIMEOUT_MS", "5000")),
                max_concurrent_requests=int(os.environ.get(
                    "DIGITALPSYCHOLOGY_SYSTEM_ONE_MAX_CONCURRENCY", "8")))
        calibration_path = os.environ.get("DIGITALPSYCHOLOGY_SYSTEM_ONE_CALIBRATION_PATH", "")
        if calibration_path:
            value = json.loads(Path(calibration_path).read_text(encoding="utf-8"))
            calibration_bundle = CalibrationBundle.from_mapping(value)
        else:
            calibration_bundle = CalibrationBundle({})
        enabled = tuple(filter(None, os.environ.get(
            "DIGITALPSYCHOLOGY_SYSTEM_ONE_QUESTIONS", ",".join(QUESTION_REGISTRY)).split(",")))
        service = cls(backend, mode=mode, calibration_bundle=calibration_bundle,
                      enabled_question_families=enabled,
                      max_concurrent_requests=int(os.environ.get(
                          "DIGITALPSYCHOLOGY_SYSTEM_ONE_MAX_CONCURRENCY", "4")))
        if isinstance(backend, LayaProvider):
            warmup_mode = os.environ.get(
                "DIGITALPSYCHOLOGY_SYSTEM_ONE_WARMUP_MODE", "background").strip()
            if warmup_mode == "blocking":
                service.warmup_latency_ms = backend.warmup_and_wait()
                service.runtime_warmed = True
            elif warmup_mode == "background":
                backend.warmup()
            else:
                raise ValueError(
                    "DIGITALPSYCHOLOGY_SYSTEM_ONE_WARMUP_MODE must be background or blocking")
        return service

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

    def judge_portfolio(self, frames, *, observed_revision: int,
                        mode: str | None = None) -> "SemanticDecisionBundle":
        if not hasattr(self, "portfolio"):
            raise ValueError("this service was not configured for a provider portfolio")
        from .system_one_portfolio import SemanticDecisionBundle
        return self.portfolio.evaluate(
            frames, observed_revision=observed_revision, mode=mode or self.mode)

    def reset_identity(self, identity: SystemOneIdentity) -> None:
        self.runtime.reset_identity(identity)

    def resync(self, frame: SystemOneFrame) -> None:
        self.runtime.resync(frame)

    def close(self) -> None:
        close = getattr(self.runtime.backend, "close", None)
        if callable(close):
            close()


__all__ = ["SystemOneService"]
