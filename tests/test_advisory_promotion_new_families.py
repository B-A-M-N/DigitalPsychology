"""Promotion gates for the advisory families: only matching qualified
question families influence control; drift and identity changes return
to shadow. Mirrors test_system_one_advisory_promotion for route-like
families, whose criteria are the legal routes (slice C)."""

from __future__ import annotations

from dataclasses import replace

import pytest

try:
    from tests.test_system_one_runtime import (
        FixedBackend,
        calibration_record,
        frame,
        judgment,
    )
    from tests.test_system_one_advisory_promotion import _jev_identity
except Exception:
    pytest.skip("runtime test fixture unavailable", allow_module_level=True)

from lib.protocol_artifact import QUESTION_REGISTRY
from lib.system_one_calibration import CalibrationBundle
from lib.system_one_contracts import QuestionSpec, canonical_hash
from lib.system_one_runtime import SystemOneRuntime

FAMILIES = (
    "decomposition_preference",
    "integration_preference",
    "test_strategy_preference",
)

JEV = dict(backend="digitalpsychology-jev-openrouter", model="typesafe/jev-1.14",
           revision="jev-1.14")


def _frame_for(family, bundle=None):
    base = frame(question_ids=(family,), calibration_bundle=bundle, **JEV)
    routes = base.state.legal_routes
    source = QUESTION_REGISTRY[family]
    questions = (QuestionSpec(
        question_id=family, family=source["family"],
        question_type=source["type"], instructions=source["instructions"],
        criteria=tuple((route, "Already-authorized legal route.")
                       for route in routes)),)
    question_bundle_digest = canonical_hash([item.to_dict() for item in questions])
    request_digest = canonical_hash({"identity": base.identity.to_dict(),
                                     "sequence": base.sequence,
                                     "state_digest": base.state_digest,
                                     "question_bundle_digest": question_bundle_digest,
                                     "policy_hash": base.policy_hash})
    return replace(base, questions=questions,
                   question_bundle_digest=question_bundle_digest,
                   request_digest=request_digest)


def _bundle_for(family, **overrides):
    params = dict(question_id=family, status="active")
    params.update(overrides)
    record = calibration_record(**params)
    record = replace(record, backend_id=JEV["backend"], model_id=JEV["model"],
                     model_revision=JEV["revision"],
                     candidate_context_counts=(
                         (3, canonical_hash(sorted(("implement", "diagnose", "verify"))),
                          canonical_hash(sorted(("software",))), 30),))
    return CalibrationBundle({family: record})


@pytest.mark.parametrize("family", FAMILIES)
def test_qualified_family_reaches_advisory(family):
    bundle = _bundle_for(family)
    request = _frame_for(family, bundle)
    answer = judgment(request)
    runtime = SystemOneRuntime(
        FixedBackend(identity=_jev_identity(), responds=lambda _frame: answer),
        calibrations=bundle, enabled_question_families=(family,))
    result = runtime.judge(request, mode="advisory")
    assert result.status == "ok"
    assert result.calibrated
    assert result.judgment.answer(family).calibrated_confidence >= 0.9


@pytest.mark.parametrize("family", FAMILIES)
def test_unqualified_family_stays_shadow(family):
    other = "retry_relation"
    bundle = _bundle_for(other)
    request = _frame_for(family, bundle)
    answer = judgment(request)
    runtime = SystemOneRuntime(
        FixedBackend(identity=_jev_identity(), responds=lambda _frame: answer),
        calibrations=bundle, enabled_question_families=(family, other))
    result = runtime.judge(request, mode="advisory")
    assert result.status == "shadow"


@pytest.mark.parametrize("family", FAMILIES)
def test_drifted_calibration_returns_to_shadow(family):
    bundle = _bundle_for(family, status="active")
    drifted = CalibrationBundle({family: replace(
        bundle.records[family], drift_status="drifted")})
    request = _frame_for(family, drifted)
    answer = judgment(request)
    runtime = SystemOneRuntime(
        FixedBackend(identity=_jev_identity(), responds=lambda _frame: answer),
        calibrations=drifted, enabled_question_families=(family,))
    result = runtime.judge(request, mode="advisory")
    assert result.status == "shadow"


@pytest.mark.parametrize("family", FAMILIES)
def test_revision_change_rejects_stale_calibration(family):
    bundle = _bundle_for(family)
    request = _frame_for(family, bundle)
    answer = judgment(request)
    runtime = SystemOneRuntime(
        FixedBackend(identity=_jev_identity(), responds=lambda _frame: answer),
        calibrations=bundle, enabled_question_families=(family,))
    tampered = replace(request, model_revision="jev-1.15")
    result = runtime.judge(tampered, mode="advisory")
    assert result.status in {"rejected", "shadow"}
