"""Optional native and loopback System-1 provider adapters.

The Laya package is imported only when a :class:`LayaSystemOneBackend` is
constructed. Normal DigitalPsychology imports and service startup do not load
Laya, PyTorch, or model weights.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import queue
import threading
import time
from typing import Any, Callable, Mapping, Optional, Protocol
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .protocol_artifact import (PROTOCOL_HASH, PROTOCOL_VERSION,
                                QUESTION_REGISTRY, QUESTION_REGISTRY_HASH,
                                STATE_PROJECTION_SCHEMA_HASH)
from .system_one_contracts import (QuestionAnswer, QuestionProbability,
                                   SystemOneCapability, SystemOneFrame,
                                   SystemOneIdentity, SystemOneJudgment,
                                   canonical_hash)


MAX_FRAME_BYTES = 32768
MAX_SYSTEM_ONE_LATENCY_MS = 30000
MODEL_FILES = (
    "model.safetensors",
    "rl_agent_config.json",
    "encoder/config.json",
    "tokenizer/tokenizer.json",
    "tokenizer/tokenizer_config.json",
)
_PROVIDER_PROTOCOL = {
    "adapter": "digitalpsychology-laya-native",
    "adapter_version": "1",
    "laya_api": "Agent.decide-json-questions-v1",
    "state_rendering": "semantic-state-json-v1",
    "response": "selected-label-and-option-probabilities-v1",
}
PROVIDER_PROTOCOL_HASH = canonical_hash(_PROVIDER_PROTOCOL)


class ProviderHealthError(RuntimeError):
    """The provider did not complete one bounded inference attempt."""


class ProviderContractError(ValueError):
    """The provider returned a malformed or out-of-contract result."""


class SystemOneBackend(Protocol):
    backend_id: str
    model_id: str
    model_revision: str
    model_content_hash: str
    provider_protocol_hash: str
    max_latency_ms: int

    def capability(self) -> SystemOneCapability: ...

    def judge(self, frame: SystemOneFrame) -> SystemOneJudgment: ...

    def reset_identity(self, identity: SystemOneIdentity) -> None: ...

    def resync(self, frame: SystemOneFrame) -> None: ...


class DisabledSystemOneBackend:
    """Provider-neutral backend used by the default disabled service."""

    backend_id = "null"
    model_id = "none"
    model_revision = "none"
    model_content_hash = hashlib.sha256(b"digitalpsychology-disabled-model").hexdigest()
    provider_protocol_hash = hashlib.sha256(b"digitalpsychology-disabled-provider").hexdigest()
    max_latency_ms = 1

    def __init__(self) -> None:
        self._capability = _capability(
            backend_id=self.backend_id, model_id=self.model_id,
            model_revision=self.model_revision,
            model_content_hash=self.model_content_hash,
            provider_protocol_hash=self.provider_protocol_hash,
            max_concurrent_requests=1)

    def capability(self) -> SystemOneCapability:
        return self._capability

    def judge(self, frame: SystemOneFrame) -> SystemOneJudgment:
        raise ProviderHealthError("the disabled provider cannot perform inference")

    def reset_identity(self, identity: SystemOneIdentity) -> None:
        return None

    def resync(self, frame: SystemOneFrame) -> None:
        return None

    def close(self) -> None:
        return None


def model_content_digest(model_path: str | Path) -> str:
    """Hash the exact checkpoint, model config, and tokenizer inputs used by Laya."""
    root = Path(model_path).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Laya model path must be a local directory")
    material: list[tuple[str, str]] = []
    for relative in MODEL_FILES:
        path = root / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Laya model artifact is missing or linked: {relative}")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        material.append((relative, digest.hexdigest()))
    return hashlib.sha256(json.dumps(material, separators=(",", ":"),
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


def _capability(*, backend_id: str, model_id: str, model_revision: str,
                model_content_hash: str, provider_protocol_hash: str,
                stateful_sessions: bool = False,
                max_concurrent_requests: int = 4) -> SystemOneCapability:
    return SystemOneCapability(
        backend_id=backend_id, model_id=model_id, model_revision=model_revision,
        model_content_hash=model_content_hash,
        provider_protocol_hash=provider_protocol_hash,
        protocol_version=PROTOCOL_VERSION, protocol_hash=PROTOCOL_HASH,
        question_registry_hash=QUESTION_REGISTRY_HASH,
        state_projection_schema_hash=STATE_PROJECTION_SCHEMA_HASH,
        supported_question_families=tuple(QUESTION_REGISTRY),
        max_state_bytes=MAX_FRAME_BYTES, stateful_sessions=stateful_sessions,
        max_concurrent_requests=max_concurrent_requests)


def _frame_bytes(frame: SystemOneFrame) -> bytes:
    body = json.dumps(frame.canonical_payload(), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")
    if len(body) > MAX_FRAME_BYTES:
        raise ValueError("System-1 frame exceeds provider byte limit")
    return body


def _http_post(request: Request, timeout: float) -> bytes:
    with urlopen(request, timeout=timeout) as response:
        return response.read(MAX_FRAME_BYTES + 1)


def _laya_questions(frame: SystemOneFrame) -> dict[str, dict[str, Any]]:
    questions: dict[str, dict[str, Any]] = {}
    for question in frame.questions:
        if question.question_type != "choice":
            raise ValueError("Laya adapter currently accepts fixed choice questions only")
        options = dict(question.criteria)
        if not options or len(options) > 24:
            raise ValueError("Laya choice questions require one to 24 fixed options")
        questions[question.question_id] = {
            "type": "choice",
            "instructions": question.instructions,
            "criteria": options,
        }
    return questions


def _probabilities(value: Any, labels: tuple[str, ...]) -> tuple[QuestionProbability, ...]:
    if not isinstance(value, Mapping) or set(map(str, value)) != set(labels):
        raise ProviderContractError("Laya probabilities must cover exactly the question options")
    raw = [float(value[label]) for label in labels]
    if any(not 0 <= item <= 1 for item in raw):
        raise ProviderContractError("Laya returned an invalid option probability")
    total = sum(raw)
    if total <= 0:
        raise ProviderContractError("Laya returned an empty probability distribution")
    return tuple(QuestionProbability(label, probability / total)
                 for label, probability in zip(labels, raw))


def _judgment_from_laya(frame: SystemOneFrame, result: Any) -> SystemOneJudgment:
    values = getattr(result, "values", None)
    probabilities = getattr(result, "probabilities", None)
    if not isinstance(values, Mapping) or not isinstance(probabilities, Mapping):
        raise ProviderContractError("Laya did not return typed values and probabilities")
    answers_by_id = getattr(result, "answers", None)
    if not isinstance(answers_by_id, Mapping):
        answers_by_id = values
    answers = []
    for question in frame.questions:
        question_id = question.question_id
        labels = tuple(label for label, _ in question.criteria)
        raw_answer = answers_by_id.get(question_id)
        if isinstance(raw_answer, Mapping):
            if raw_answer.get("type") != "choice":
                raise ProviderContractError(f"Laya returned a non-choice answer for {question_id}")
            selected = raw_answer.get("choice")
        else:
            selected = raw_answer
        if not isinstance(selected, str) or selected not in labels:
            raise ProviderContractError(f"Laya omitted or returned an illegal label for {question_id}")
        raw_probabilities = probabilities.get(question_id)
        if raw_probabilities is None and isinstance(raw_answer, Mapping):
            raw_probabilities = raw_answer.get("probabilities")
        answers.append(QuestionAnswer(
            question_id=question_id, selected=selected,
            raw_probabilities=_probabilities(raw_probabilities, labels)))
    return SystemOneJudgment(
        identity=frame.identity, request_id=frame.request_id, sequence=frame.sequence,
        state_digest=frame.state_digest,
        question_bundle_digest=frame.question_bundle_digest,
        request_digest=frame.request_digest, policy_hash=frame.policy_hash,
        backend_id=frame.backend_id, model_id=frame.model_id,
        model_revision=frame.model_revision,
        model_content_hash=frame.model_content_hash,
        provider_protocol_hash=frame.provider_protocol_hash,
        calibration_bundle_hash=frame.calibration_bundle_hash,
        answers=tuple(answers), protocol_version=frame.protocol_version,
        protocol_hash=frame.protocol_hash)


@dataclass
class _Inference:
    frame: SystemOneFrame
    done: threading.Event
    deadline_monotonic: float
    cancelled: bool = False
    judgment: Optional[SystemOneJudgment] = None
    error: Optional[BaseException] = None


class LayaSystemOneBackend:
    """Native Laya adapter pinned to a local checkpoint and immutable identity.

    The expected content hash is required. The adapter never downloads a model
    or resolves a mutable hub alias. Inference is serialized on one daemon
    worker so a timed-out CPU call cannot create an unbounded worker backlog.
    """

    backend_id = "digitalpsychology-laya-native"

    def __init__(self, model_path: str | Path, *, model_id: str,
                 model_revision: str, expected_model_content_hash: str,
                 device: str = "cpu", max_latency_ms: int = MAX_SYSTEM_ONE_LATENCY_MS,
                 loader: Optional[Callable[..., Any]] = None) -> None:
        self.model_path = Path(model_path).expanduser().resolve(strict=True)
        self.model_id = model_id
        self.model_revision = model_revision
        self.device = device
        self.max_latency_ms = int(max_latency_ms)
        self.model_content_hash = model_content_digest(self.model_path)
        if expected_model_content_hash != self.model_content_hash:
            raise ValueError("Laya checkpoint content hash differs from the configured pin")
        if not model_id or len(model_id) > 256:
            raise ValueError("model_id must be bounded non-empty text")
        if not model_revision or len(model_revision) > 256:
            raise ValueError("model_revision must be bounded non-empty text")
        if not 1 <= self.max_latency_ms <= MAX_SYSTEM_ONE_LATENCY_MS:
            raise ValueError(
                f"Laya inference timeout must be between 1 and {MAX_SYSTEM_ONE_LATENCY_MS}ms")
        if device not in {"cpu", "cuda", "mps", "xpu"}:
            raise ValueError("unsupported Laya device")
        self._loader = loader
        self._agent = None
        self._agent_lock = threading.Lock()
        self._agent_ready = threading.Event()
        self._agent_loading = False
        self._agent_error: Optional[BaseException] = None
        self._queue: queue.Queue[_Inference | None] = queue.Queue(maxsize=1)
        self._worker: Optional[threading.Thread] = None
        self._worker_lock = threading.Lock()
        self._closed = False
        self._closed_lock = threading.Lock()
        self._capability = _capability(
            backend_id=self.backend_id, model_id=model_id,
            model_revision=model_revision, model_content_hash=self.model_content_hash,
            provider_protocol_hash=PROVIDER_PROTOCOL_HASH,
            max_concurrent_requests=1)

    @classmethod
    def from_environment(cls, *, model_id: str = "laya-typed-decisions") -> "LayaSystemOneBackend":
        """Construct only from explicit, predownloaded model identity settings."""
        if importlib.util.find_spec("laya") is None:
            raise RuntimeError(
                "Laya System-1 support is optional; install digital-psychology[laya]")
        path = os.environ.get("DIGITALPSYCHOLOGY_LAYA_MODEL_PATH", "")
        revision = os.environ.get("DIGITALPSYCHOLOGY_LAYA_MODEL_REVISION", "")
        content_hash = os.environ.get("DIGITALPSYCHOLOGY_LAYA_MODEL_SHA256", "")
        if not path or not revision or not content_hash:
            raise ValueError(
                "Laya requires DIGITALPSYCHOLOGY_LAYA_MODEL_PATH, "
                "DIGITALPSYCHOLOGY_LAYA_MODEL_REVISION, and DIGITALPSYCHOLOGY_LAYA_MODEL_SHA256")
        return cls(path, model_id=model_id, model_revision=revision,
                   expected_model_content_hash=content_hash,
                   device=os.environ.get("DIGITALPSYCHOLOGY_LAYA_DEVICE", "cpu"),
                   max_latency_ms=int(os.environ.get(
                       "DIGITALPSYCHOLOGY_LAYA_TIMEOUT_MS", str(MAX_SYSTEM_ONE_LATENCY_MS))))

    def capability(self) -> SystemOneCapability:
        return self._capability

    def _ensure_agent(self) -> None:
        with self._agent_lock:
            if self._agent is not None:
                return
            if self._agent_error is not None:
                raise ProviderHealthError("Laya model initialization failed") from self._agent_error
            owner = not self._agent_loading
            if owner:
                self._agent_loading = True
        if not owner:
            self._agent_ready.wait()
            with self._agent_lock:
                if self._agent is not None:
                    return
                error = self._agent_error
            raise ProviderHealthError("Laya model initialization failed") from error
        try:
            loader = self._loader
            if loader is None:
                try:
                    loader = importlib.import_module("laya").load
                except ImportError as exc:
                    raise RuntimeError(
                        "Laya System-1 support is optional; install digital-psychology[laya]") from exc
            agent = loader(str(self.model_path), device=self.device)
        except BaseException as exc:
            with self._agent_lock:
                self._agent_error = exc
                self._agent_loading = False
                self._agent_ready.set()
            raise ProviderHealthError("Laya model initialization failed") from exc
        with self._agent_lock:
            self._agent = agent
            self._agent_loading = False
            self._agent_ready.set()

    def warmup(self) -> None:
        """Load the pinned model in the background without delaying MCP startup."""
        with self._agent_lock:
            if self._agent is not None or self._agent_loading or self._agent_error is not None:
                return
        def initialize() -> None:
            try:
                self._ensure_agent()
            except Exception:
                # The first inference reports a bounded host-visible fallback.
                pass

        threading.Thread(target=initialize, name="digitalpsychology-laya-load",
                         daemon=True).start()

    def reset_identity(self, identity: SystemOneIdentity) -> None:
        # Native Laya is stateless; no session state crosses calls or identities.
        return None

    def resync(self, frame: SystemOneFrame) -> None:
        _frame_bytes(frame)

    def _ensure_worker(self) -> None:
        with self._worker_lock:
            if self._closed:
                raise ProviderHealthError("Laya backend is closed")
            if self._worker is None:
                self._worker = threading.Thread(
                    target=self._worker_main, name="digitalpsychology-laya", daemon=True)
                self._worker.start()

    def _worker_main(self) -> None:
        while True:
            job = self._queue.get()
            if job is None:
                return
            if job.cancelled or time.monotonic() >= job.deadline_monotonic:
                job.error = ProviderHealthError("Laya request expired before execution")
                job.done.set()
                continue
            try:
                self._ensure_agent()
                if job.cancelled or time.monotonic() >= job.deadline_monotonic:
                    job.error = ProviderHealthError("Laya request expired during model initialization")
                    job.done.set()
                    continue
                assert self._agent is not None
                result = self._agent.decide(
                    job.frame.state.to_dict(), questions=_laya_questions(job.frame),
                    return_details=True, lang="en")
                job.judgment = _judgment_from_laya(job.frame, result)
            except ProviderContractError as exc:
                job.error = exc
            except (TypeError, ValueError, KeyError) as exc:
                job.error = ProviderContractError("Laya returned a malformed result")
                job.error.__cause__ = exc
            except Exception as exc:
                job.error = ProviderHealthError("Laya inference failed")
                job.error.__cause__ = exc
            finally:
                job.done.set()

    def judge(self, frame: SystemOneFrame) -> SystemOneJudgment:
        _frame_bytes(frame)
        if (frame.backend_id != self.backend_id or frame.model_id != self.model_id
                or frame.model_revision != self.model_revision
                or frame.model_content_hash != self.model_content_hash
                or frame.provider_protocol_hash != PROVIDER_PROTOCOL_HASH):
            raise ValueError("System-1 frame provider identity differs from loaded Laya checkpoint")
        deadline = time.monotonic() + self.max_latency_ms / 1000.0
        job = _Inference(frame=frame, done=threading.Event(),
                         deadline_monotonic=deadline)
        with self._closed_lock:
            if self._closed:
                raise ProviderHealthError("Laya backend is closed")
            self._ensure_worker()
            try:
                self._queue.put(job, timeout=max(0.0, deadline - time.monotonic()))
            except queue.Full as exc:
                raise ProviderHealthError("Laya provider is still handling a prior request") from exc
        if not job.done.wait(max(0.0, deadline - time.monotonic())):
            job.cancelled = True
            raise ProviderHealthError("Laya inference exceeded its configured deadline")
        if job.error is not None:
            if isinstance(job.error, ProviderContractError):
                raise job.error
            if isinstance(job.error, ProviderHealthError):
                raise job.error
            raise ProviderHealthError("Laya inference failed") from job.error
        if job.judgment is None:
            raise ProviderHealthError("Laya inference produced no result")
        return job.judgment

    def close(self, *, timeout: float = 0.25) -> None:
        """Stop accepting jobs, discard queued stale work, and bound the join."""
        with self._closed_lock:
            if self._closed:
                worker = self._worker
            else:
                self._closed = True
                worker = self._worker
                while True:
                    try:
                        queued = self._queue.get_nowait()
                    except queue.Empty:
                        break
                    if queued is not None:
                        queued.cancelled = True
                        queued.error = ProviderHealthError("Laya backend closed before execution")
                        queued.done.set()
                if worker is not None:
                    try:
                        self._queue.put_nowait(None)
                    except queue.Full:
                        pass
        if worker is not None:
            worker.join(timeout=max(0.0, min(float(timeout), 2.0)))


@dataclass
class StatelessLoopbackHTTPBackend:
    """Stateless typed JSON adapter for an explicitly pinned loopback service."""

    endpoint: str
    model_id: str
    model_revision: str
    model_content_hash: str
    provider_protocol_hash: str
    timeout_ms: int = 5000
    transport: Callable[[Request, float], bytes] = _http_post
    backend_id: str = "stateless-loopback-http"
    max_concurrent_requests: int = 8

    def __post_init__(self) -> None:
        parsed = urlsplit(self.endpoint) if isinstance(self.endpoint, str) else None
        if (parsed is None or parsed.scheme != "http"
                or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
                or parsed.username is not None or parsed.password is not None):
            raise ValueError("System-1 endpoint must use loopback HTTP")
        if not 1 <= int(self.timeout_ms) <= MAX_SYSTEM_ONE_LATENCY_MS:
            raise ValueError(
                f"System-1 timeout must be between 1 and {MAX_SYSTEM_ONE_LATENCY_MS}ms")
        self.timeout_ms = int(self.timeout_ms)
        if not 1 <= int(self.max_concurrent_requests) <= 64:
            raise ValueError("max_concurrent_requests must be between 1 and 64")

    @property
    def max_latency_ms(self) -> int:
        return self.timeout_ms

    def capability(self) -> SystemOneCapability:
        return _capability(
            backend_id=self.backend_id, model_id=self.model_id,
            model_revision=self.model_revision, model_content_hash=self.model_content_hash,
            provider_protocol_hash=self.provider_protocol_hash,
            max_concurrent_requests=self.max_concurrent_requests)

    def reset_identity(self, identity: SystemOneIdentity) -> None:
        return None

    def resync(self, frame: SystemOneFrame) -> None:
        _frame_bytes(frame)

    def judge(self, frame: SystemOneFrame) -> SystemOneJudgment:
        body = _frame_bytes(frame)
        request = Request(self.endpoint, data=body,
                          headers={"Content-Type": "application/json",
                                   "Accept": "application/json"}, method="POST")
        try:
            raw = self.transport(request, self.timeout_ms / 1000.0)
        except (OSError, TimeoutError) as exc:
            raise ProviderHealthError("System-1 loopback provider is unavailable") from exc
        if not isinstance(raw, bytes) or len(raw) > MAX_FRAME_BYTES:
            raise ValueError("System-1 response exceeds transport bounds")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("System-1 response is not valid JSON") from exc
        judgment = SystemOneJudgment.from_mapping(payload)
        return judgment


__all__ = ["SystemOneBackend", "ProviderHealthError", "ProviderContractError",
           "DisabledSystemOneBackend", "LayaSystemOneBackend",
           "StatelessLoopbackHTTPBackend",
           "PROVIDER_PROTOCOL_HASH", "model_content_digest"]
