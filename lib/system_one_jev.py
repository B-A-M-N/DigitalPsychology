"""Bounded native OpenRouter Jev adapter for DigitalPsychology System-1.

Jev is an independently hosted decision provider. It receives a minimal
semantic projection through OpenRouter's Decisions API and returns typed
choice distributions. It never receives credentials from model-visible tools
and never acquires DP decision authority by itself.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
import re
import threading
import time
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .protocol_artifact import (PROTOCOL_HASH, PROTOCOL_VERSION,
                                QUESTION_REGISTRY, QUESTION_REGISTRY_HASH,
                                STATE_PROJECTION_SCHEMA_HASH)
from .system_one_backends import ProviderContractError, ProviderHealthError
from .system_one_jev_models import DEFAULT_JEV_MODEL, JEV_MODELS, provider_protocol_hash
from .system_one_contracts import (QuestionAnswer, QuestionProbability,
                                   SystemOneCapability, SystemOneFrame,
                                   SystemOneIdentity, SystemOneJudgment,
                                   canonical_hash)

DECISIONS_ENDPOINT = "https://openrouter.ai/api/alpha/decisions"
ALLOWED_MODELS = frozenset({"typesafe/jev-1.13", "typesafe/jev-1.14"})

_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(?:authorization|bearer|api[_ -]?key|openrouter|password|"
               r"secret|token|credential)\b\s*[:=]\s*[^,;}\"']+"),
    re.compile(r"(?i)(?:sk-[a-z0-9_-]{20,}|github_pat_[a-z0-9_]{20,}|ghp_[a-z0-9]{20,})"),
    re.compile(r"(?i)\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
)
_PATH_PATTERN = re.compile(r"(?i)(?:/home/|/Users/|/root/|[A-Za-z]:\\)[^\s\"']+")
_REDACTED = "[REDACTED]"


class JevSecretError(ProviderContractError):
    """The outbound projection contains a prohibited secret or absolute path."""


@dataclass(frozen=True)
class JevProjection:
    payload: Mapping[str, Any]
    digest: str
    byte_count: int


def _clean(value: Any, *, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value:
        return None
    for pattern in _SECRET_PATTERNS:
        value = pattern.sub(_REDACTED, value)
    value = _PATH_PATTERN.sub(_REDACTED, value)
    return value[:limit]


def _slots(values: Any) -> list[str]:
    if not isinstance(values, (tuple, list)):
        return []
    return [f"criterion_{index + 1}" for index, _ in enumerate(values[:8])]


def jev_state_projection(frame: SystemOneFrame) -> dict[str, Any]:
    """Project only host-typed, bounded decision facts for the remote provider."""
    state = frame.state.to_dict()
    task = state["task"]
    situation = state["situation"]
    candidate = state["candidate"]
    projected: dict[str, Any] = {
        "schema": "jev-minimal-semantic-projection/v1",
        "task": {
            "work_mode": task["work_mode"],
            "deliverable_kind": task["deliverable_kind"],
            "task_complexity": task["task_complexity"],
            "phase": task.get("phase"),
            "domain_tags": [_clean(item, limit=64) or "redacted"
                            for item in task.get("domain_tags", ())[:8]],
        },
        "situation": {
            "current_state": _clean(situation.get("current_state"), limit=64),
            "blockers": [_clean(item, limit=128) or "redacted"
                         for item in situation.get("blockers", ())[:4]],
            "actions_since_goal_progress": situation.get(
                "actions_since_goal_progress"),
            "actions_since_artifact_change": situation.get(
                "actions_since_artifact_change"),
            "recovery_epochs": situation.get("recovery_epochs"),
            "repeated_branch_count": situation.get("repeated_branch_count"),
        },
        "criteria": [{
            "id": _clean(item["criterion_id"], limit=64) or f"criterion_{index + 1}",
            "status": item["status"], "evidence_status": item["evidence_status"],
        } for index, item in enumerate(state["criteria"][:8])],
        "candidate": {
            "action_class": _clean(candidate["action_class"], limit=64) or "unknown",
            "host_relation": candidate.get("host_declared_relation"),
            "target_kind": _clean(candidate.get("target_kind"), limit=64),
            "purpose": _clean(candidate.get("proposed_purpose"), limit=256),
            "affected_criteria": _slots(candidate.get("affected_criterion_ids")),
            "relation_certainty": candidate.get("relation_certainty"),
        },
        "convergence": {key: value for key, value in state["convergence"].items()
                        if isinstance(value, (int, float)) and value >= 0},
    }
    if state["failed_approaches"]:
        projected["failed_approaches"] = [{
            "cause_class": _clean(item.get("cause_class"), limit=64) or "unknown",
            "hypothesis": _clean(item.get("hypothesis_summary"), limit=128),
            "mechanism": _clean(item.get("mechanism"), limit=128),
            "planned_action_class": _clean(item.get("planned_action_class"), limit=64),
            "result_class": _clean(item.get("failed_result_class"), limit=64),
            "affected_criteria": _slots(item.get("affected_criterion_ids")),
        } for item in state["failed_approaches"][:4]]
    if state["legal_routes"]:
        projected["legal_routes"] = [_clean(item, limit=64) or "redacted"
                                      for item in state["legal_routes"][:8]]
    payload = {key: value for key, value in projected.items()
               if value not in (None, (), [], {})}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False)
    _assert_no_secrets(payload)
    if len(encoded.encode("utf-8")) > 8192:
        raise JevSecretError("Jev projection exceeds its privacy and size boundary")
    return payload


def _assert_no_secrets(value: Any) -> None:
    if isinstance(value, str):
        if _PATH_PATTERN.search(value):
            raise JevSecretError("Jev projection contains an absolute filesystem path")
        for pattern in _SECRET_PATTERNS:
            if pattern.search(value):
                raise JevSecretError("Jev projection contains a suspected secret")
    elif isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str):
                if _PATH_PATTERN.search(key):
                    raise JevSecretError("Jev projection contains an absolute path")
                for pattern in _SECRET_PATTERNS:
                    if pattern.search(key):
                        raise JevSecretError("Jev projection contains a suspected secret")
            _assert_no_secrets(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _assert_no_secrets(item)


def build_jev_request(frame: SystemOneFrame) -> dict[str, Any]:
    """Translate the canonical frame without inventing provider criteria."""
    questions: dict[str, Any] = {}
    for question in frame.questions:
        if question.question_id in questions:
            raise ProviderContractError("Jev frame contains duplicate question IDs")
        questions[question.question_id] = {
            "type": question.question_type,
            "instructions": question.instructions,
            "criteria": {label: description for label, description in question.criteria},
        }
    return {
        "model": frame.model_id,
        "state": jev_state_projection(frame),
        "questions": questions,
    }


def _probability_map(value: Any, labels: tuple[str, ...]) -> tuple[QuestionProbability, ...]:
    if not isinstance(value, Mapping) or set(map(str, value)) != set(labels):
        raise ProviderContractError("Jev probabilities must cover exactly the question options")
    values: dict[str, float] = {}
    for label in labels:
        raw = value[label]
        if not isinstance(raw, (int, float)) or isinstance(raw, bool) or not math.isfinite(float(raw)) \
                or not 0.0 <= float(raw) <= 1.0:
            raise ProviderContractError("Jev returned a non-finite or out-of-range probability")
        values[label] = float(raw)
    total = sum(values.values())
    if not math.isfinite(total) or total <= 0.0:
        raise ProviderContractError("Jev returned an invalid probability distribution")
    if abs(total - 1.0) > 0.02:
        raise ProviderContractError("Jev probabilities do not form a consistent distribution")
    return tuple(QuestionProbability(label, values[label] / total) for label in labels)


def judgment_from_jev(frame: SystemOneFrame, response: Mapping[str, Any],
                      *, elapsed_ms: float) -> SystemOneJudgment:
    if not isinstance(response, Mapping):
        raise ProviderContractError("Jev response must be JSON")
    actual_model = response.get("model")
    if actual_model != frame.model_id:
        raise ProviderContractError("Jev returned an unexpected model substitution")
    provider_revision = response.get("provider_revision")
    if not isinstance(provider_revision, str) or not provider_revision.strip() \
            or len(provider_revision) > 256:
        raise ProviderContractError("Jev response omits its provider revision")
    answers = response.get("answers")
    if not isinstance(answers, Mapping) or set(map(str, answers)) != set(frame.question_ids):
        raise ProviderContractError("Jev response question set does not match the request")
    built = []
    for question in frame.questions:
        row = answers[question.question_id]
        if not isinstance(row, Mapping) or set(row) != {"choice", "probabilities"}:
            raise ProviderContractError(f"Jev answer is malformed for {question.question_id}")
        selected = row["choice"]
        labels = tuple(label for label, _description in question.criteria)
        if not isinstance(selected, str) or selected not in labels:
            raise ProviderContractError(f"Jev returned an illegal choice for {question.question_id}")
        built.append(QuestionAnswer(
            question_id=question.question_id, selected=selected,
            raw_probabilities=_probability_map(row["probabilities"], labels)))
    diagnostics = {
        "schema_version": "jev-inference-diagnostics/v1",
        "projection_schema": "jev-minimal-semantic-projection/v1",
        "projection_digest": canonical_hash(jev_state_projection(frame)),
        "projection_bytes": len(json.dumps(
            jev_state_projection(frame), sort_keys=True, separators=(",", ":"),
            ensure_ascii=False).encode("utf-8")),
        "latency_ms": min(30000, max(0, round(elapsed_ms))),
        "provider_revision": provider_revision,
        "advisory_eligible": False,
    }
    return SystemOneJudgment(
        identity=frame.identity, request_id=frame.request_id,
        sequence=frame.sequence, state_digest=frame.state_digest,
        question_bundle_digest=frame.question_bundle_digest,
        request_digest=frame.request_digest, policy_hash=frame.policy_hash,
        backend_id=frame.backend_id, model_id=frame.model_id,
        model_revision=frame.model_revision,
        model_content_hash=frame.model_content_hash,
        provider_protocol_hash=frame.provider_protocol_hash,
        calibration_bundle_hash=frame.calibration_bundle_hash,
        answers=tuple(built), protocol_version=frame.protocol_version,
        protocol_hash=frame.protocol_hash,
        local_inference_diagnostics=diagnostics)


def _http_post(request: Request, timeout: float) -> bytes:
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.read(65537)
    except HTTPError as exc:
        if 400 <= exc.code < 500:
            raise ProviderContractError(f"OpenRouter rejected the Jev request ({exc.code})") from exc
        raise ProviderHealthError("OpenRouter Jev provider is unavailable") from exc
    except (OSError, TimeoutError, URLError) as exc:
        raise ProviderHealthError("OpenRouter Jev provider is unavailable") from exc


@dataclass
class JevOpenRouterProvider:
    """Independent OpenRouter-backed provider with strict identity binding."""

    api_key: str
    model_id: str = DEFAULT_JEV_MODEL
    model_revision: str = ""
    timeout_ms: int = 3000
    max_concurrent_requests: int = 2
    endpoint: str = DECISIONS_ENDPOINT
    transport: Callable[[Request, float], bytes] = _http_post
    backend_id: str = "digitalpsychology-jev-openrouter"

    def __post_init__(self) -> None:
        if not isinstance(self.api_key, str) or not self.api_key.strip():
            raise ValueError("Jev requires an OpenRouter API key")
        if self.model_id not in ALLOWED_MODELS:
            raise ValueError("Jev model must be an explicitly allowlisted typesafe/jev version")
        self._model_identity = JEV_MODELS[self.model_id]
        if not self.model_revision:
            self.model_revision = self._model_identity["model_revision"]
        if self.model_revision != self._model_identity["model_revision"]:
            raise ValueError("Jev model revision is not the pinned allowlisted revision")
        self.provider_protocol_hash = provider_protocol_hash(self.model_id)
        if type(self.timeout_ms) is not int or not 1 <= self.timeout_ms <= 10000:
            raise ValueError("Jev timeout must be between 1 and 10000ms")
        if not 1 <= int(self.max_concurrent_requests) <= 8:
            raise ValueError("Jev max_concurrent_requests must be between 1 and 8")
        self.timeout_ms = int(self.timeout_ms)
        self.max_concurrent_requests = int(self.max_concurrent_requests)
        self.max_latency_ms = self.timeout_ms
        self._slots = threading.BoundedSemaphore(self.max_concurrent_requests)
        self._closed = False
        # The exact remote endpoint is fixed; no caller-supplied mutable identity.
        if self.endpoint != DECISIONS_ENDPOINT:
            raise ValueError("Jev must use the pinned OpenRouter Decisions endpoint")
        self._capability = SystemOneCapability(
            backend_id=self.backend_id, model_id=self.model_id,
            model_revision=self.model_revision,
            model_content_hash=hashlib.sha256(
                f"openrouter:{self.model_id}".encode()).hexdigest(),
            provider_protocol_hash=self.provider_protocol_hash,
            protocol_version=PROTOCOL_VERSION, protocol_hash=PROTOCOL_HASH,
            question_registry_hash=QUESTION_REGISTRY_HASH,
            state_projection_schema_hash=STATE_PROJECTION_SCHEMA_HASH,
            supported_question_families=tuple(QUESTION_REGISTRY),
            max_state_bytes=8192, stateful_sessions=False,
            max_concurrent_requests=self.max_concurrent_requests)

    def capability(self) -> SystemOneCapability:
        return self._capability

    def reset_identity(self, identity: SystemOneIdentity) -> None:
        return None

    def resync(self, frame: SystemOneFrame) -> None:
        return None

    def judge(self, frame: SystemOneFrame) -> SystemOneJudgment:
        if self._closed:
            raise ProviderHealthError("Jev provider is closed")
        expected = self._capability
        if (frame.backend_id != expected.backend_id
                or frame.model_id != expected.model_id
                or frame.model_revision != expected.model_revision
                or frame.model_content_hash != expected.model_content_hash
                or frame.provider_protocol_hash != expected.provider_protocol_hash):
            raise ValueError("System-1 frame provider identity differs from Jev configuration")
        request_payload = build_jev_request(frame)
        body = json.dumps(request_payload, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False).encode("utf-8")
        request = Request(
            self.endpoint, data=body,
            headers={"Authorization": f"Bearer {self.api_key}",
                     "Content-Type": "application/json",
                     "Accept": "application/json"},
            method="POST")
        started = time.monotonic()
        if not self._slots.acquire(timeout=self.timeout_ms / 1000.0):
            raise ProviderHealthError("Jev provider concurrency limit reached")
        try:
            raw = self.transport(request, self.timeout_ms / 1000.0)
        finally:
            self._slots.release()
        elapsed_ms = (time.monotonic() - started) * 1000.0
        if not isinstance(raw, bytes) or not raw or len(raw) > 65536:
            raise ProviderContractError("Jev response exceeds transport bounds")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ProviderContractError("Jev response is not valid JSON") from exc
        return judgment_from_jev(frame, payload, elapsed_ms=elapsed_ms)

    def close(self) -> None:
        self._closed = True


def from_environment(*, portfolio_explicit: bool = False) -> JevOpenRouterProvider:
    """Build Jev only when the operator explicitly enables it and supplies a key."""
    enabled = os.environ.get(
        "DIGITALPSYCHOLOGY_JEV_ENABLED", "true" if portfolio_explicit else "false"
    ).strip().lower()
    if enabled not in {"0", "false", "no", "1", "true", "yes"}:
        raise ValueError("DIGITALPSYCHOLOGY_JEV_ENABLED must be a boolean")
    if enabled in {"0", "false", "no"}:
        raise ProviderHealthError("Jev is disabled")
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        raise ValueError("OPENROUTER_API_KEY is required when Jev is enabled")
    return JevOpenRouterProvider(
        api_key=key,
        model_id=os.environ.get("DIGITALPSYCHOLOGY_JEV_MODEL", DEFAULT_JEV_MODEL).strip(),
        model_revision=os.environ.get("DIGITALPSYCHOLOGY_JEV_MODEL_REVISION", "").strip(),
        timeout_ms=int(os.environ.get("DIGITALPSYCHOLOGY_JEV_TIMEOUT_MS", "3000")),
        max_concurrent_requests=int(os.environ.get(
            "DIGITALPSYCHOLOGY_JEV_MAX_CONCURRENCY", "2")))


__all__ = ["DECISIONS_ENDPOINT", "ALLOWED_MODELS",
           "JevOpenRouterProvider", "JevSecretError", "build_jev_request",
           "jev_state_projection", "judgment_from_jev", "from_environment"]
