"""Transport adapters for System-1. No model runtime is imported here."""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Callable, Mapping, Optional, Protocol
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .system_one_contracts import SystemOneFrame, SystemOneJudgment


class SystemOneBackend(Protocol):
    backend_id: str
    model_id: str

    def judge(self, frame: SystemOneFrame) -> SystemOneJudgment: ...


@dataclass(frozen=True)
class NullBackend:
    """Explicit no-model backend used by shadow deployments without a provider."""
    backend_id: str = "null"
    model_id: str = "none"

    def judge(self, frame: SystemOneFrame) -> SystemOneJudgment:
        raise RuntimeError("System-1 backend is unavailable")


def _http_post(request: Request, timeout: float) -> bytes:
    with urlopen(request, timeout=timeout) as response:
        return response.read(65537)


@dataclass
class JevCompatibleHTTPBackend:
    """Local Jev-compatible JSON endpoint with a hard latency cap and no retry."""
    endpoint: str
    model_id: str
    timeout_ms: int = 200
    transport: Callable[[Request, float], bytes] = _http_post
    backend_id: str = "jev-compatible-http"

    def __post_init__(self) -> None:
        parsed = urlsplit(self.endpoint) if isinstance(self.endpoint, str) else None
        if (parsed is None or parsed.scheme != "http"
                or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
                or parsed.username is not None or parsed.password is not None):
            raise ValueError("System-1 endpoint must use loopback HTTP")
        if not isinstance(self.model_id, str) or not self.model_id or len(self.model_id) > 256:
            raise ValueError("model_id must be bounded non-empty text")
        if not isinstance(self.timeout_ms, int) or not 1 <= self.timeout_ms <= 250:
            raise ValueError("System-1 timeout must be between 1 and 250ms")

    def judge(self, frame: SystemOneFrame) -> SystemOneJudgment:
        body = json.dumps(frame.canonical_payload(), sort_keys=True,
                          separators=(",", ":")).encode("utf-8")
        request = Request(self.endpoint, data=body,
                          headers={"Content-Type": "application/json",
                                   "Accept": "application/json"}, method="POST")
        # A single transport invocation is intentional; the caller owns fallback.
        raw = self.transport(request, min(self.timeout_ms, 250) / 1000.0)
        if not isinstance(raw, bytes) or len(raw) > 65536:
            raise ValueError("System-1 response exceeds transport bounds")
        parsed = json.loads(raw.decode("utf-8"))
        if not isinstance(parsed, Mapping):
            raise ValueError("System-1 response must be a JSON object")
        return SystemOneJudgment.from_dict(parsed)


__all__ = ["SystemOneBackend", "NullBackend", "JevCompatibleHTTPBackend"]
