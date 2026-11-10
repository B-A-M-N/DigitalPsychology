"""Typed executable adjustments: semantic-adjustment/v1 builders."""

from __future__ import annotations

import pytest

from lib.control.adjustment_schema import ADJUSTMENT_SCHEMA, SCHEMA_VERSION
from lib.control.proposal_emitter import (
    SCHEMA_VERSION as EMITTER_SCHEMA_VERSION,
    build_adjustment,
    build_runtime_control_adjustment,
    build_skill_improvement_adjustment,
    build_workflow_preference_adjustment,
    emit_proposals,
    validate_adjustment,
)


def _origin(**overrides):
    base = {
        "provider": "jev",
        "model": "typesafe/jev-1.14",
        "question": "route_preference",
        "decision_id": "req-7",
        "calibration_digest": "c" * 64,
    }
    base.update(overrides)
    return base


def _common(benefit="fewer retries", rollback="restore prior value",
            expiry="2026-12-01T00:00:00Z"):
    return {"projected_benefit": benefit, "rollback_criteria": rollback,
            "expiry": expiry}


def test_version_string_pinned():
    assert SCHEMA_VERSION == "semantic-adjustment/v1"
    assert EMITTER_SCHEMA_VERSION == "semantic-adjustment/v1"
    assert ADJUSTMENT_SCHEMA["schema"] == "semantic-adjustment/v1"
    assert set(ADJUSTMENT_SCHEMA["kinds"]) == {
        "runtime_control", "workflow_preference", "skill_improvement"}


def test_runtime_control_provenance_preserved():
    origin = _origin()
    payload = build_runtime_control_adjustment(
        origin=origin,
        target={"owner": "cfw", "path": "control/retry_policy",
                "base_revision": 12},
        control="retry_policy", proposed_value="no_retry",
        scope="session:req-7", evidence_refs=["eval:retry-sweep-3"],
        **_common())
    assert payload["schema"] == "semantic-adjustment/v1"
    assert payload["kind"] == "runtime_control"
    assert payload["origin"] == origin
    assert payload["target"] == {"owner": "cfw",
                                 "path": "control/retry_policy",
                                 "base_revision": 12}
    assert payload["detail"] == {"control": "retry_policy",
                                 "proposed_value": "no_retry",
                                 "scope": "session:req-7"}
    assert payload["evidence_refs"] == ["eval:retry-sweep-3"]
    assert payload["unknown_evidence"] is False
    assert validate_adjustment(payload) == payload


def test_workflow_preference_missing_evidence_explicit():
    payload = build_workflow_preference_adjustment(
        origin=_origin(question="decomposition_preference"),
        target={"owner": "godkiller", "path": "statework/godkiller",
                "base_hash": "h" * 32},
        statework="godkiller", basis_revisions=[9, 10],
        unknown_evidence=True, **_common())
    assert payload["schema"] == "semantic-adjustment/v1"
    assert payload["detail"] == {"statework": "godkiller",
                                 "basis_revisions": [9, 10]}
    assert payload["evidence_refs"] == []
    assert payload["unknown_evidence"] is True
    assert validate_adjustment(payload) == payload


def test_skill_improvement_provenance_preserved():
    origin = _origin(provider="laya", question="integration_preference")
    payload = build_skill_improvement_adjustment(
        origin=origin,
        target={"owner": "dp", "path": "skill/recover",
                "base_revision": 3, "base_hash": "b" * 16},
        skill="recover",
        proposed_invariant="every recovery names its rollback before acting",
        evidence_refs=["trace:t-1", "trace:t-2"], **_common())
    assert payload["schema"] == "semantic-adjustment/v1"
    assert payload["origin"] == origin
    assert payload["detail"]["skill"] == "recover"
    assert payload["detail"]["proposed_invariant"].startswith("every recovery")
    assert payload["unknown_evidence"] is False
    assert validate_adjustment(payload) == payload


def test_missing_evidence_must_be_explicit():
    with pytest.raises(ValueError):
        build_runtime_control_adjustment(
            origin=_origin(),
            target={"owner": "cfw", "path": "control/retry_policy",
                    "base_revision": 1},
            control="retry_policy", proposed_value="normal", scope="task",
            **_common())
    with pytest.raises(ValueError):
        build_skill_improvement_adjustment(
            origin=_origin(),
            target={"owner": "dp", "path": "skill/recover",
                    "base_revision": 1},
            skill="recover", proposed_invariant="x" * 10,
            evidence_refs=["trace:t-1"], unknown_evidence=True,
            **_common())


def test_malformed_rejected():
    good_origin = _origin()
    good_target = {"owner": "cfw", "path": "control/retry_policy",
                   "base_revision": 1}
    with pytest.raises(ValueError, match="unknown"):
        build_adjustment("not_a_kind", origin=good_origin,
                         target=good_target, detail={},
                         unknown_evidence=True, **_common())
    with pytest.raises(ValueError, match="unknown"):
        build_runtime_control_adjustment(
            origin=good_origin, target=good_target, control="nope",
            proposed_value="normal", scope="task", unknown_evidence=True,
            **_common())
    with pytest.raises(ValueError, match="outside the control range"):
        build_runtime_control_adjustment(
            origin=good_origin, target=good_target,
            control="retry_policy", proposed_value="always",
            scope="task", unknown_evidence=True, **_common())
    with pytest.raises(ValueError, match="unknown"):
        build_workflow_preference_adjustment(
            origin=good_origin,
            target={"owner": "x", "path": "statework/nope",
                    "base_revision": 1},
            statework="nope", basis_revisions=[1],
            unknown_evidence=True, **_common())
    with pytest.raises(ValueError, match="unknown"):
        build_skill_improvement_adjustment(
            origin=good_origin,
            target={"owner": "dp", "path": "skill/nope",
                    "base_revision": 1},
            skill="nope", proposed_invariant="holds",
            unknown_evidence=True, **_common())
    with pytest.raises(ValueError, match="must match"):
        build_runtime_control_adjustment(
            origin=good_origin,
            target={"owner": "cfw", "path": "control/retry_policy",
                    "base_revision": 1},
            control="exploration_budget", proposed_value="low", scope="task",
            unknown_evidence=True, **_common())
    with pytest.raises(ValueError, match="base revision"):
        build_runtime_control_adjustment(
            origin=good_origin,
            target={"owner": "cfw", "path": "control/retry_policy"},
            control="retry_policy", proposed_value="normal", scope="task",
            unknown_evidence=True, **_common())
    bad_origin = _origin(decision_id="")
    with pytest.raises(ValueError):
        build_runtime_control_adjustment(
            origin=bad_origin, target=good_target,
            control="retry_policy", proposed_value="normal", scope="task",
            unknown_evidence=True, **_common())
    with pytest.raises(ValueError):
        build_workflow_preference_adjustment(
            origin=good_origin,
            target={"owner": "x", "path": "statework/getter",
                    "base_revision": 1},
            statework="getter", basis_revisions=[],
            unknown_evidence=True, **_common())
    with pytest.raises(ValueError):
        build_skill_improvement_adjustment(
            origin=good_origin,
            target={"owner": "dp", "path": "skill/recover",
                    "base_revision": 1},
            skill="recover", proposed_invariant="holds",
            unknown_evidence=True, projected_benefit="",
            rollback_criteria="r", expiry="e")
    with pytest.raises(ValueError, match="version"):
        validate_adjustment({"schema": "semantic-adjustment/v9",
                             "kind": "runtime_control"})
    with pytest.raises(ValueError):
        build_runtime_control_adjustment(
            origin=good_origin, target=good_target,
            control="retry_policy", proposed_value="normal", scope="task",
            unknown_evidence=True, projected_benefit="b" * 2000,
            rollback_criteria="r", expiry="e")


def test_emit_proposals_recommendations_unchanged():
    class _Prob:
        def __init__(self, label, probability):
            self.label = label
            self.probability = probability

    class _Answer:
        def __init__(self, selected, raw):
            self.selected = selected
            self.raw_probabilities = tuple(_Prob(label, value)
                                           for label, value in raw)
            self.calibrated_probabilities = None

    class _Judgment:
        model_id = "typesafe/jev-1.14"
        model_revision = "jev-1.14"
        request_id = "req-7"
        state_digest = "d" * 64

        def answer(self, question_id):
            if question_id == "test_strategy_preference":
                return _Answer("testworks", [("testworks", 0.7),
                                             ("godkiller", 0.3)])
            return None

    (proposal,) = emit_proposals(_Judgment(), subject_ref="repo:x")
    assert proposal["kind"] == "test_selection_draft"
    assert "schema" not in proposal
