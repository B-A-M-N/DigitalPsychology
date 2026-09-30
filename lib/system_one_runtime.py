"""Bounded, fail-closed System-1 inference runtime for host-side use."""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import time
from typing import Callable, Optional, Protocol

from .system_one_backends import NullBackend, SystemOneBackend
from .system_one_calibration import CalibrationRecord
from .system_one_contracts import (SystemOneFrame, SystemOneIdentity,
                                   SystemOneJudgment)


class FrameEligibility(Protocol):
    def __call__(self, frame: SystemOneFrame) -> bool: ...


@dataclass(frozen=True)
class SystemOneResult:
    judgment: Optional[SystemOneJudgment]
    status: str
    calibrated: bool
    cache_hit: bool = False


class CircuitBreaker:
    def __init__(self, *, threshold: int = 3, cooldown_seconds: float = 2.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        if threshold < 1 or cooldown_seconds <= 0:
            raise ValueError("invalid circuit breaker bounds")
        self.threshold = threshold
        self.cooldown_seconds = cooldown_seconds
        self.clock = clock
        self.failures = 0
        self.opened_at: Optional[float] = None

    def permits(self) -> bool:
        if self.opened_at is None:
            return True
        if self.clock() - self.opened_at < self.cooldown_seconds:
            return False
        self.opened_at = None
        self.failures = 0
        return True

    def success(self) -> None:
        self.failures = 0
        self.opened_at = None

    def failure(self) -> None:
        self.failures += 1
        if self.failures >= self.threshold:
            self.opened_at = self.clock()


class SystemOneRuntime:
    """One request per eligible frame, with strict identity and bounded LRU."""
    def __init__(self, backend: Optional[SystemOneBackend] = None, *,
                 calibration: Optional[CalibrationRecord] = None,
                 cache_size: int = 128,
                 eligibility: Optional[FrameEligibility] = None,
                 breaker: Optional[CircuitBreaker] = None) -> None:
        if not 0 <= cache_size <= 4096:
            raise ValueError("cache_size must be between 0 and 4096")
        self.backend = backend or NullBackend()
        self.calibration = calibration
        self.cache_size = cache_size
        self.eligibility = eligibility or (lambda frame: True)
        self.breaker = breaker or CircuitBreaker()
        self._cache: OrderedDict[tuple[object, ...], SystemOneJudgment] = OrderedDict()
        self._latest_sequence: OrderedDict[tuple[str, ...], int] = OrderedDict()

    @property
    def backend_id(self) -> str:
        return self.backend.backend_id

    @property
    def model_id(self) -> str:
        return self.backend.model_id

    @property
    def calibration_hash(self) -> str:
        return self.calibration.digest if self.calibration is not None else ""

    @property
    def max_latency_ms(self) -> Optional[int]:
        return getattr(self.backend, "timeout_ms", None)

    @staticmethod
    def _identity_key(identity: SystemOneIdentity) -> tuple[str, ...]:
        return (identity.namespace_id, identity.application_instance_id,
                identity.application_id, identity.agent_id, identity.agent_instance_id,
                identity.session_id, identity.task_id,
                identity.attempt_id, identity.interaction_id or "")

    @staticmethod
    def _cache_key(frame: SystemOneFrame) -> tuple[object, ...]:
        identity = SystemOneRuntime._identity_key(frame.identity)
        return (identity, frame.policy_hash, frame.backend_id, frame.model_id,
                frame.calibration_hash, frame.state_digest, tuple(frame.questions))

    def _calibrated(self, frame: SystemOneFrame) -> bool:
        record = self.calibration
        return bool(record and record.valid_for(
            backend_id=frame.backend_id, model_id=frame.model_id,
            expected_hash=frame.calibration_hash))

    def _confidence_calibrated(self, frame: SystemOneFrame,
                               answer: SystemOneJudgment) -> bool:
        return bool(self._calibrated(frame) and self.calibration is not None
                    and answer.confidence >= self.calibration.minimum_confidence)

    def _validate(self, frame: SystemOneFrame, answer: SystemOneJudgment) -> None:
        if answer.identity.to_dict() != frame.identity.to_dict():
            raise ValueError("System-1 identity mismatch")
        if (answer.request_id != frame.request_id or answer.sequence != frame.sequence
                or answer.state_digest != frame.state_digest):
            raise ValueError("System-1 response is stale or miscorrelated")
        if answer.policy_hash != frame.policy_hash:
            raise ValueError("System-1 policy mismatch")
        if (answer.backend_id != self.backend.backend_id
                or frame.backend_id != self.backend.backend_id):
            raise ValueError("System-1 backend identity mismatch")
        if answer.model_id != self.backend.model_id or frame.model_id != self.backend.model_id:
            raise ValueError("System-1 model identity mismatch")
        if answer.calibration_hash != frame.calibration_hash:
            raise ValueError("System-1 calibration identity mismatch")
        if answer.protocol_version != frame.protocol_version:
            raise ValueError("System-1 protocol mismatch")
        if answer.protocol_hash != frame.protocol_hash:
            raise ValueError("System-1 protocol hash mismatch")
        for question in frame.questions:
            if getattr(answer, question) is None:
                raise ValueError(f"System-1 omitted requested label {question}")
        if answer.route_preference is not None and answer.route_preference not in frame.route_candidates:
            raise ValueError("System-1 preferred a route outside the static candidate set")

    def judge(self, frame: SystemOneFrame, *, mode: str = "shadow") -> SystemOneResult:
        if mode not in {"disabled", "shadow", "advisory"}:
            raise ValueError("System-1 mode must be disabled, shadow, or advisory")
        if mode == "disabled":
            return SystemOneResult(None, "disabled", False)
        try:
            frame = SystemOneFrame.from_dict(frame.canonical_payload())
        except Exception:
            return SystemOneResult(None, "ineligible", False)
        try:
            eligible = bool(self.eligibility(frame))
        except Exception:
            eligible = False
        if not eligible:
            return SystemOneResult(None, "ineligible", False)
        identity_key = self._identity_key(frame.identity)
        latest = self._latest_sequence.get(identity_key, 0)
        if frame.sequence <= latest:
            return SystemOneResult(None, "stale_request", False)
        self._latest_sequence[identity_key] = frame.sequence
        self._latest_sequence.move_to_end(identity_key)
        while len(self._latest_sequence) > 4096:
            self._latest_sequence.popitem(last=False)
        cache_key = self._cache_key(frame)
        cached = self._cache.get(cache_key)
        if cached is not None:
            self._cache.move_to_end(cache_key)
            answer = SystemOneJudgment(
                identity=frame.identity, request_id=frame.request_id,
                sequence=frame.sequence, state_digest=frame.state_digest,
                policy_hash=frame.policy_hash, backend_id=frame.backend_id,
                model_id=frame.model_id, calibration_hash=frame.calibration_hash,
                action_relation=cached.action_relation,
                progress_relation=cached.progress_relation,
                retry_relation=cached.retry_relation, goal_drift=cached.goal_drift,
                route_preference=cached.route_preference,
                confidence=cached.confidence, protocol_version=frame.protocol_version)
            return SystemOneResult(answer, "ok", self._confidence_calibrated(frame, answer),
                                   cache_hit=True)
        if not self.breaker.permits():
            return SystemOneResult(None, "circuit_open", False)
        try:
            answer = self.backend.judge(frame)
            self._validate(frame, answer)
            if self._latest_sequence.get(identity_key) != frame.sequence:
                raise ValueError("System-1 response is stale after a newer request")
        except Exception:
            self.breaker.failure()
            return SystemOneResult(None, "fallback", False)
        self.breaker.success()
        if self.cache_size:
            self._cache[cache_key] = answer
            self._cache.move_to_end(cache_key)
        while len(self._cache) > self.cache_size:
            self._cache.popitem(last=False)
        calibrated = self._confidence_calibrated(frame, answer)
        if mode == "advisory" and not calibrated:
            return SystemOneResult(answer, "uncalibrated_shadow", False)
        return SystemOneResult(answer, "ok", calibrated)


__all__ = ["CircuitBreaker", "SystemOneRuntime", "SystemOneResult"]
