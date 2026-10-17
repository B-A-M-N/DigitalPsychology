from __future__ import annotations

import json
from urllib.error import HTTPError

import pytest

from lib.system_one_backends import ProviderContractError, ProviderHealthError
from lib.system_one_jev import (JevQuotaExceededError, JevSecretError,
                                JevThrottledError, _http_post)
from lib.system_one_jev_models import JEV_MODELS

try:
    from tests.test_system_one_jev import _frame
except Exception:
    pytest.skip("Jev fixture unavailable", allow_module_level=True)


class _Response:
    def __init__(self, payload): self.payload = payload
    def read(self, size): return self.payload


def test_http_throttle_quota_and_auth_are_classified():
    def transport(request, timeout):
        raise HTTPError("https://openrouter.ai/api/alpha/decisions", 429,
                        "Too Many Requests", {"Retry-After": "1"}, None)
    provider = JevThrottledError
    from lib.system_one_jev import JevOpenRouterProvider
    p = JevOpenRouterProvider("key", transport=transport, timeout_ms=10)
    with pytest.raises(JevThrottledError, match="retry-after=1"):
        p.judge(_frame())

    def unauthorized(request, timeout):
        raise HTTPError("https://openrouter.ai/api/alpha/decisions", 401,
                        "Unauthorized", {}, None)
    p = JevOpenRouterProvider("key", transport=unauthorized, timeout_ms=10)
    with pytest.raises(ProviderContractError, match="credentials"):
        p.judge(_frame())

    def quota(request, timeout):
        raise HTTPError("https://openrouter.ai/api/alpha/decisions", 402,
                        "Payment Required", {}, None)
    p = JevOpenRouterProvider("key", transport=quota, timeout_ms=10)
    with pytest.raises(JevQuotaExceededError):
        p.judge(_frame())

    def unavailable(request, timeout):
        raise HTTPError("https://openrouter.ai/api/alpha/decisions", 500,
                        "Server Error", {}, None)
    p = JevOpenRouterProvider("key", transport=unavailable, timeout_ms=10)
    with pytest.raises(ProviderHealthError):
        p.judge(_frame())
