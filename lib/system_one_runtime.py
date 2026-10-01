"""Concurrency-safe System-1 dispatch, validation, caching, and calibration."""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, replace
import threading
import time
from typing import Callable, Mapping, Optional, Protocol

from .system_one_backends import ProviderHealthError, SystemOneBackend
from .system_one_calibration import CalibrationBundle, CalibrationRecord
from .system_one_contracts import (SystemOneCapability, SystemOneFrame,
                                   SystemOneIdentity, SystemOneJudgment)


class FrameEligibility(Protocol):
    def __call__(self, frame: SystemOneFrame) -> bool: ...


@dataclass(frozen=True)
class SystemOneResult:
    judgment: Optional[SystemOneJudgment]
    status: str
    calibrated_question_ids: tuple[str, ...] = ()
    cache_hit: bool = False

    @property
    def calibrated(self) -> bool:
        return bool(self.calibrated_question_ids)


class CircuitBreaker:
    """Provider-health breaker. Per-request contract errors never open it."""

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
    """Host-side provider satisfying CFW's internal BehavioralJudge contract."""

    def __init__(self, backend: SystemOneBackend, *,
                 calibrations: Mapping[str, CalibrationRecord] | CalibrationBundle | None = None,
                 cache_size: int = 128,
                 eligibility: Optional[FrameEligibility] = None,
                 breaker: Optional[CircuitBreaker] = None,
                 max_concurrent_requests: int = 4) -> None:
        if not 0 <= cache_size <= 4096:
            raise ValueError("cache_size must be between 0 and 4096")
        if not 1 <= max_concurrent_requests <= 64:
            raise ValueError("max_concurrent_requests must be between 1 and 64")
        self.backend = backend
        if isinstance(calibrations, CalibrationBundle):
            self.calibration_bundle = calibrations
        else:
            self.calibration_bundle = CalibrationBundle(dict(calibrations or {}))
        self.cache_size = cache_size
        self.eligibility = eligibility or (lambda frame: True)
        self.breaker = breaker or CircuitBreaker()
        self._cache: OrderedDict[tuple[object, ...], SystemOneJudgment] = OrderedDict()
        self._latest_sequence: OrderedDict[tuple[str, ...], int] = OrderedDict()
        self._state_lock = threading.RLock()
        self._provider_slots = threading.BoundedSemaphore(max_concurrent_requests)

    @property
    def backend_id(self) -> str:
        return self.backend.backend_id

    @property
    def model_id(self) -> str:
        return self.backend.model_id

    @property
    def max_latency_ms(self) -> int:
        return int(self.backend.max_latency_ms)

    @property
    def calibration_hash(self) -> str:
        return self.calibration_bundle.digest

    def capability(self) -> Mapping[str, object]:
        return self.backend.capability().to_dict()

    def reset_identity(self, identity: SystemOneIdentity) -> None:
        reset = getattr(self.backend, "reset_identity", None)
        if callable(reset):
            reset(identity)
        identity_key = self._identity_key(identity)
        with self._state_lock:
            self._latest_sequence.pop(identity_key, None)
            for key in tuple(self._cache):
                if key[0] == identity_key:
                    self._cache.pop(key, None)

    def resync(self, frame: SystemOneFrame) -> None:
        resync = getattr(self.backend, "resync", None)
        if callable(resync):
            resync(frame)

    @staticmethod
    def _identity_key(identity: SystemOneIdentity) -> tuple[str, ...]:
        return (identity.namespace_id, identity.application_id,
                identity.application_instance_id, identity.agent_id,
                identity.agent_instance_id, identity.session_id, identity.task_id,
                identity.attempt_id, identity.interaction_id or "")

    @classmethod
    def _cache_key(cls, frame: SystemOneFrame) -> tuple[object, ...]:
        return (cls._identity_key(frame.identity), frame.policy_hash,
                frame.backend_id, frame.model_id, frame.model_revision,
                frame.model_content_hash, frame.provider_protocol_hash,
                frame.protocol_hash, frame.state_projection_schema_hash
                if hasattr(frame, "state_projection_schema_hash") else "",
                frame.calibration_bundle_hash, frame.state_digest,
                frame.question_bundle_digest)

    def _validate(self, frame: SystemOneFrame, answer: SystemOneJudgment) -> None:
        capability = self.backend.capability()
        checks = (
            (answer.identity == frame.identity, "identity"),
            (answer.request_id == frame.request_id, "request ID"),
            (answer.sequence == frame.sequence, "sequence"),
            (answer.state_digest == frame.state_digest, "state digest"),
            (answer.question_bundle_digest == frame.question_bundle_digest, "question bundle"),
            (answer.request_digest == frame.request_digest, "request digest"),
            (answer.policy_hash == frame.policy_hash, "policy"),
            (answer.backend_id == capability.backend_id == frame.backend_id, "backend"),
            (answer.model_id == capability.model_id == frame.model_id, "model"),
            (answer.model_revision == capability.model_revision == frame.model_revision,
             "model revision"),
            (answer.model_content_hash == capability.model_content_hash == frame.model_content_hash,
             "model content"),
            (answer.provider_protocol_hash == capability.provider_protocol_hash
             == frame.provider_protocol_hash, "provider protocol"),
            (answer.calibration_bundle_hash == frame.calibration_bundle_hash,
             "calibration bundle"),
            (answer.protocol_version == frame.protocol_version == capability.protocol_version,
             "protocol version"),
            (answer.protocol_hash == frame.protocol_hash == capability.protocol_hash,
             "protocol hash"),
        )
        for valid, name in checks:
            if not valid:
                raise ValueError(f"System-1 response {name} mismatch")
        if tuple(answer.question_id for answer in answer.answers) != frame.question_ids:
            raise ValueError("System-1 response question set or order mismatch")
        for spec, item in zip(frame.questions, answer.answers):
            labels = tuple(label for label, _ in spec.criteria)
            if item.selected not in labels or tuple(p.label for p in item.raw_probabilities) != labels:
                raise ValueError(f"System-1 response labels differ for {spec.question_id}")
        if len(frame.canonical_payload().__repr__().encode("utf-8")) > 65536:
            raise ValueError("System-1 response frame exceeds validation bounds")

    def _calibrate(self, frame: SystemOneFrame,
                   answer: SystemOneJudgment) -> tuple[SystemOneJudgment, tuple[str, ...]]:
        calibrated_answers = []
        calibrated_ids = []
        for item in answer.answers:
            record = self.calibration_bundle.records.get(item.question_id)
            if record is not None and record.valid_for(frame):
                calibrated_answers.append(replace(
                    item, calibrated_probabilities=record.apply(item.raw_probabilities),
                    calibration_hash=record.digest))
                calibrated_ids.append(item.question_id)
            else:
                calibrated_answers.append(item)
        return replace(answer, answers=tuple(calibrated_answers)), tuple(calibrated_ids)

    @staticmethod
    def _rebind(cached: SystemOneJudgment, frame: SystemOneFrame) -> SystemOneJudgment:
        return replace(cached, request_id=frame.request_id, sequence=frame.sequence,
                       request_digest=frame.request_digest)

    def judge(self, frame: SystemOneFrame, *, mode: str = "shadow") -> SystemOneResult:
        if mode not in {"disabled", "shadow", "advisory"}:
            raise ValueError("System-1 mode must be disabled, shadow, or advisory")
        if mode == "disabled":
            return SystemOneResult(None, "disabled")
        if not isinstance(frame, SystemOneFrame):
            try:
                raw_frame = frame.canonical_payload() if callable(
                    getattr(frame, "canonical_payload", None)) else frame
                frame = SystemOneFrame.from_mapping(raw_frame)
            except Exception:
                return SystemOneResult(None, "ineligible")
        try:
            eligible = bool(self.eligibility(frame))
        except Exception:
            eligible = False
        if not eligible:
            return SystemOneResult(None, "ineligible")
        if frame.calibration_bundle_hash != self.calibration_hash:
            return SystemOneResult(None, "calibration_mismatch")

        identity_key = self._identity_key(frame.identity)
        cache_key = self._cache_key(frame)
        with self._state_lock:
            latest = self._latest_sequence.get(identity_key, 0)
            if frame.sequence <= latest:
                return SystemOneResult(None, "stale_request")
            self._latest_sequence[identity_key] = frame.sequence
            self._latest_sequence.move_to_end(identity_key)
            while len(self._latest_sequence) > 4096:
                self._latest_sequence.popitem(last=False)
            cached = self._cache.get(cache_key)
            if cached is not None:
                self._cache.move_to_end(cache_key)
                rebound = self._rebind(cached, frame)
                _, calibrated_ids = self._calibrate(frame, rebound)
                return SystemOneResult(rebound, "ok", calibrated_ids, cache_hit=True)
            if not self.breaker.permits():
                return SystemOneResult(None, "circuit_open")

        if not self._provider_slots.acquire(timeout=self.max_latency_ms / 1000.0):
            with self._state_lock:
                self.breaker.failure()
            return SystemOneResult(None, "provider_busy")
        started = time.monotonic()
        try:
            answer = self.backend.judge(frame)
        except ProviderHealthError:
            with self._state_lock:
                self.breaker.failure()
            return SystemOneResult(None, "fallback")
        except (OSError, TimeoutError):
            with self._state_lock:
                self.breaker.failure()
            return SystemOneResult(None, "fallback")
        except Exception:
            # Wrong protocol, identity, or malformed request responses are local
            # contract violations; they must not disable other agents' calls.
            return SystemOneResult(None, "provider_contract_error")
        finally:
            self._provider_slots.release()

        if (time.monotonic() - started) * 1000 > self.max_latency_ms:
            with self._state_lock:
                self.breaker.failure()
            return SystemOneResult(None, "fallback")
        try:
            self._validate(frame, answer)
        except Exception:
            return SystemOneResult(None, "provider_contract_error")
        with self._state_lock:
            if self._latest_sequence.get(identity_key) != frame.sequence:
                return SystemOneResult(None, "stale_response")
            self.breaker.success()
            calibrated_answer, calibrated_ids = self._calibrate(frame, answer)
            if self.cache_size:
                self._cache[cache_key] = calibrated_answer
                self._cache.move_to_end(cache_key)
                while len(self._cache) > self.cache_size:
                    self._cache.popitem(last=False)
        if mode == "advisory" and not calibrated_ids:
            return SystemOneResult(calibrated_answer, "uncalibrated_shadow", ())
        return SystemOneResult(calibrated_answer, "ok", calibrated_ids)


__all__ = ["CircuitBreaker", "SystemOneRuntime", "SystemOneResult"]
