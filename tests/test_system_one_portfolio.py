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


def _frame_for(backend_id):
    request = frame(backend=backend_id)
    return request


def test_portfolio_rejects_mismatched_states_before_inference():
    runtime_a = _runtime("alpha")
    runtime_b = _runtime("beta")
    portfolio = SystemOnePortfolio([runtime_a, runtime_b])
    common = _frame_for("alpha")
    altered = replace(common, backend_id="beta", request_id="different-request")
    with pytest.raises(Exception, match="common decision basis"):
        portfolio.evaluate({"alpha": common, "beta": altered},
                           observed_revision=1, mode="shadow")
    assert not runtime_a.backend.calls and not runtime_b.backend.calls


def test_portfolio_rejects_stale_decision_revision():
    portfolio = SystemOnePortfolio([_runtime("alpha")])
    with pytest.raises(Exception, match="stale"):
        portfolio.evaluate({"alpha": _frame_for("alpha")}, observed_revision=5,
                           mode="shadow", current_decision_revision=4)


def test_portfolio_partial_unavailability_is_recorded_independently():
    class OfflineBackend(FixedBackend):
        def judge(self, _frame):
            from lib.system_one_runtime import ProviderHealthError
            raise ProviderHealthError("offline")

    cap = FixedBackend().capability()
    online = SystemOneRuntime(FixedBackend(identity=type(cap)(**{
        **cap.to_dict(), "backend_id": "alpha"})))
    offline = SystemOneRuntime(OfflineBackend(identity=type(cap)(**{
        **cap.to_dict(), "backend_id": "beta"})))
    portfolio = SystemOnePortfolio([online, offline])
    bundle = portfolio.evaluate({"alpha": _frame_for("alpha"), "beta": _frame_for("beta")},
                                observed_revision=1, mode="shadow")
    statuses = {item.provider_id: item.result.status for item in bundle.observations}
    assert statuses == {"alpha": "shadow", "beta": "unavailable"}


def test_portfolio_deadline_bounds_total_evaluation():
    import time
    runtime = _runtime("alpha")
    portfolio = SystemOnePortfolio([runtime])
    started = time.monotonic()
    with pytest.raises(Exception, match="portfolio deadline"):
        portfolio.evaluate({"alpha": _frame_for("alpha")}, observed_revision=1,
                           mode="shadow", total_deadline_ms=0)
    assert time.monotonic() - started < 5.0
