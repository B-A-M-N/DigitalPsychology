"""Versioned per-family provider qualification for selector binding."""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

try:
    from tests.test_system_one_runtime import calibration_record
except Exception:
    pytest.skip("runtime calibration fixture unavailable", allow_module_level=True)

from lib.control.engine import ControlDecisionEngine
from lib.control.provider_qualification import (
    QUALIFIED,
    UNKNOWN,
    UNQUALIFIED,
    provider_key_for_backend,
    resolve_portfolio_qualification,
    resolve_provider_qualification,
)
from lib.system_one_calibration import CalibrationBundle
from lib.system_one_provider_selection import SemanticProviderSelector

LAYA_BACKEND = "digitalpsychology-laya-native"
JEV_BACKEND = "digitalpsychology-jev-openrouter"
LAYA_MODEL = "laya-model"
LAYA_REVISION = "laya-rev"


class _FakeBackend:
    def __init__(self, backend_id, model_id, revision):
        self.backend_id = backend_id
        self.model_id = model_id
        self.model_revision = revision


class _FakeRuntime:
    """Duck-typed SystemOneRuntime carrying a real calibration bundle."""

    def __init__(self, backend_id, model_id, revision, bundle):
        self.backend_id = backend_id
        self.backend = _FakeBackend(backend_id, model_id, revision)
        self.calibration_bundle = bundle


def _laya_record(question_id="progress_relation", **overrides):
    record = calibration_record(question_id)
    return replace(
        record,
        backend_id=overrides.get("backend_id", LAYA_BACKEND),
        model_id=overrides.get("model_id", LAYA_MODEL),
        model_revision=overrides.get("model_revision", LAYA_REVISION),
        **{k: v for k, v in overrides.items()
           if k not in {"backend_id", "model_id", "model_revision"}})


def _laya_runtime(question_id="progress_relation", **overrides):
    return _FakeRuntime(
        LAYA_BACKEND, LAYA_MODEL, LAYA_REVISION,
        CalibrationBundle({question_id: _laya_record(question_id, **overrides)}))


def _select_laya_only(selector):
    return selector.select(
        query_planned=True, remote_suitable=False,
        laya_available=True, jev_available=False)


def test_qualified_family_resolves_with_stable_snapshot():
    runtime = _laya_runtime()
    first = resolve_provider_qualification(runtime, "progress_relation")
    second = resolve_provider_qualification(runtime, "progress_relation")
    assert first.status == QUALIFIED
    assert first.snapshot_id == second.snapshot_id
    assert first.calibration_digest
    assert first.model_revision == LAYA_REVISION


def test_snapshot_change_rebuilds_selector_instead_of_serving_memoized_state():
    runtime = _laya_runtime()
    service = SimpleNamespace(mode="advisory", runtimes=[runtime])
    engine = ControlDecisionEngine(
        decision_service=SimpleNamespace(_system_one_service=service))
    first = engine._get_selector(question_id="progress_relation")
    assert engine._get_selector(question_id="progress_relation") is first
    assert _select_laya_only(first).strategy == "laya"

    # Calibration rotation changes the bundle digest: the memoized
    # selector must be discarded, not re-served.
    rotated = replace(_laya_record(), temperature=0.6)
    runtime.calibration_bundle = CalibrationBundle(
        {"progress_relation": rotated})
    second = engine._get_selector(question_id="progress_relation")
    assert second is not first
    assert _select_laya_only(second).strategy == "laya"

    # A failing gate flips the rebuilt selector to unqualified while the
    # stale selector would still have advised.
    runtime.calibration_bundle = CalibrationBundle(
        {"progress_relation": _laya_record(status="candidate")})
    third = engine._get_selector(question_id="progress_relation")
    assert third is not first and third is not second
    assert _select_laya_only(third).strategy == "none"
    assert _select_laya_only(first).strategy == "laya"


def test_unknown_family_stays_unqualified_fail_closed():
    runtime = _laya_runtime("progress_relation")
    entry = resolve_provider_qualification(runtime, "retry_relation")
    assert entry.status == UNKNOWN
    portfolio = resolve_portfolio_qualification([runtime], "retry_relation")
    assert portfolio.selector_qualification == {"laya": UNQUALIFIED}
    selector = SemanticProviderSelector(
        default_provider="laya",
        qualification=portfolio.selector_qualification)
    assert _select_laya_only(selector).strategy == "none"


def test_missing_digest_is_unqualified_despite_artifact_hash():
    runtime = _FakeRuntime(
        LAYA_BACKEND, LAYA_MODEL, LAYA_REVISION, CalibrationBundle({}))
    # A stray artifact hash must never qualify on its own.
    runtime.calibration_hash = "f" * 64
    entry = resolve_provider_qualification(runtime, "progress_relation")
    assert entry.status == UNQUALIFIED
    assert entry.calibration_digest == ""
    portfolio = resolve_portfolio_qualification([runtime], "progress_relation")
    assert portfolio.selector_qualification == {"laya": UNQUALIFIED}


def test_per_family_granularity_one_qualified_one_not():
    bundle = CalibrationBundle({
        "progress_relation": _laya_record("progress_relation"),
        "retry_relation": _laya_record("retry_relation", status="candidate"),
    })
    runtime = _FakeRuntime(LAYA_BACKEND, LAYA_MODEL, LAYA_REVISION, bundle)
    qualified = resolve_provider_qualification(runtime, "progress_relation")
    other = resolve_provider_qualification(runtime, "retry_relation")
    assert qualified.status == QUALIFIED
    assert other.status == UNQUALIFIED
    assert qualified.snapshot_id != other.snapshot_id


def test_model_revision_change_invalidates_snapshot_and_qualification():
    runtime = _laya_runtime()
    before = resolve_provider_qualification(runtime, "progress_relation")
    assert before.status == QUALIFIED
    runtime.backend = _FakeBackend(LAYA_BACKEND, LAYA_MODEL, "laya-rev-2")
    after = resolve_provider_qualification(runtime, "progress_relation")
    assert after.snapshot_id != before.snapshot_id
    assert after.status == UNQUALIFIED


def test_unrecognized_backend_is_unknown_and_unqualified_in_selector():
    runtime = _FakeRuntime(
        "test-backend", "test-model", "test-revision",
        CalibrationBundle({"progress_relation": calibration_record()}))
    entry = resolve_provider_qualification(runtime, "progress_relation")
    assert entry.status == UNKNOWN
    assert provider_key_for_backend("test-backend") is None
    portfolio = resolve_portfolio_qualification([runtime], "progress_relation")
    assert portfolio.selector_qualification == {}
