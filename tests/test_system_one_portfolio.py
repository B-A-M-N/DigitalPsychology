from __future__ import annotations

import pytest

from dataclasses import replace

from lib.system_one_portfolio import ProviderDecision, SemanticDecisionBundle, SystemOnePortfolio
from lib.system_one_runtime import SystemOneRuntime

try:
    from tests.test_system_one_runtime import FixedBackend, frame, judgment
except Exception:
    pytest.skip("runtime test fixture unavailable", allow_module_level=True)


def _runtime(backend_id):
    cap = FixedBackend().capability()
    backend = FixedBackend(identity=type(cap)(**{
        **cap.to_dict(), "backend_id": backend_id}))
    return SystemOneRuntime(backend)


def test_portfolio_records_independent_observations():
    a = _runtime("alpha")
    b = _runtime("beta")
    portfolio = SystemOnePortfolio([a, b])
    request = frame(backend="alpha")
    beta = replace(request, backend_id="beta")
    bundle = portfolio.evaluate({"alpha": request, "beta": beta},
                                observed_revision=3, mode="shadow")
    assert bundle.question_id == "progress_relation"
    assert [item.provider_id for item in bundle.observations] == ["alpha", "beta"]
    assert all(item.result.status == "shadow" for item in bundle.observations)
    assert all(item.observed_revision == 3 for item in bundle.observations)
    assert bundle.selected_provider_id is None


def test_portfolio_rejects_duplicate_or_unknown_providers():
    a = _runtime("alpha")
    with pytest.raises(ValueError):
        SystemOnePortfolio([a, _runtime("alpha")])
    portfolio = SystemOnePortfolio([a])
    request = frame(backend="alpha")
    with pytest.raises(ValueError):
        portfolio.evaluate({"alpha": request}, observed_revision=1, mode="shadow",
                           selected_provider_id="beta")
