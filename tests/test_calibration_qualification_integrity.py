"""Qualification-record integrity for Laya advisory activation (directive §8).

A qualification must bind to a specific artifact combination, not to a model name.
These tests verify that a checkpoint replacement, schema change, or invalid
calibration record cannot inherit advisory privileges, and that disqualification is
specific to the artifact combination rather than banning the family permanently.

Frames are built with the real ``SystemOneFrame`` so the checks exercise the
production contract rather than a stand-in.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from lib.protocol_artifact import QUESTION_REGISTRY
from lib.system_one_calibration import CalibrationRecord
from lib.system_one_contracts import (
    ActivityFact,
    CriterionState,
    QuestionSpec,
    SemanticState,
    SystemOneFrame,
    SystemOneIdentity,
    canonical_hash,
)

BACKEND_ID = "digitalpsychology-laya-native"
MODEL_ID = "convaiinnovations/laya-typed-decisions"
MODEL_REVISION = "1a793eb568e6718f15941d08f85432581df534e3"
MODEL_HASH = "a" * 64
PROVIDER_HASH = "b" * 64
EVALUATOR_HASH = "c" * 64


def _record(question_id: str = "progress_relation", **overrides):
    values = dict(
        question_id=question_id, backend_id=BACKEND_ID, model_id=MODEL_ID,
        model_revision=MODEL_REVISION, model_content_hash=MODEL_HASH,
        provider_protocol_hash=PROVIDER_HASH, evaluator_contract_hash=EVALUATOR_HASH,
        temperature=0.5, minimum_advisory_confidence=0.9, sample_count=100,
        training_count=60, calibration_count=20, holdout_count=20,
        training_outcome_digest="d" * 64, calibration_outcome_digest="e" * 64,
        holdout_outcome_digest="f" * 64,
        holdout_accuracy=0.95, holdout_brier=0.05, holdout_ece=0.04,
        holdout_log_loss=0.1,
        holdout_class_metrics=tuple(
            (label, 1, 0, 0, 1.0, 1.0)
            for label in QUESTION_REGISTRY[question_id]["criteria"]),
        qualification_minimum_accuracy=0.8,
        qualification_maximum_brier=0.25,
        qualification_maximum_ece=0.12,
        work_modes=("delivery",), deliverable_kinds=("code_behavior",),
        independent_trajectory_count=30, training_trajectory_count=18,
        calibration_trajectory_count=6, holdout_trajectory_count=6,
        independent_fixture_count=30, training_fixture_count=18,
        calibration_fixture_count=6, holdout_fixture_count=6,
        minimum_independent_trajectories=30, minimum_independent_fixtures=30,
        cohort_counts=(("delivery", "code_behavior", 30),),
        minimum_cohort_trajectories=20, minimum_outcomes=100,
        status="active", drift_status="clear",
    )
    values.update(overrides)
    return CalibrationRecord(**values)


def _state(*, work_mode="delivery", deliverable_kind="code_behavior",
           domain_tags=("software",), legal_routes=("implement", "diagnose", "verify")):
    activity = ActivityFact(
        sequence=1, activity_type="action", source="host_observed",
        action_class="verification", host_declared_relation="ambiguous",
        target_kind="workspace", proposed_purpose="verify the requested result",
        affected_criterion_ids=("C1",))
    return SemanticState(
        task={"objective": "Deliver the requested behavior.", "work_mode": work_mode,
              "deliverable_kind": deliverable_kind, "task_complexity": "standard",
              "phase": "implementation", "active_segment": "segment-1",
              "active_statework": None, "active_flow": None,
              "non_goals": ["unrelated refactoring"],
              "domain_tags": list(domain_tags)},
        situation={"current_state": "IMPLEMENTED", "blockers": [],
                   "actions_since_goal_progress": 2,
                   "actions_since_artifact_change": 1,
                   "verification_actions_since_artifact_change": 0,
                   "recovery_epochs": 0, "repeated_branch_count": 0},
        criteria=(CriterionState("C1", "Requested behavior works", "functional"),),
        activity=(activity,), workflows=(), convergence=(
            ("support_actions_since_goal_progress", 2),),
        candidate=activity, legal_routes=legal_routes)


def _frame(*, question_id="progress_relation", backend=BACKEND_ID, model=MODEL_ID,
           revision=MODEL_REVISION, content_hash=MODEL_HASH,
           provider_hash=PROVIDER_HASH, protocol_version=None, protocol_hash=None,
           state=None):
    record = _record(question_id)
    identity = SystemOneIdentity(
        namespace_id="tenant", application_id="app", application_instance_id="app-1",
        agent_id="agent-1", agent_instance_id="agent-1-instance",
        session_id="session-1", task_id="task-1", attempt_id="attempt-1",
        interaction_id="interaction-1")
    state = state or _state()
    source = QUESTION_REGISTRY[question_id]
    criteria = source["criteria"]
    if question_id == "route_preference":
        criteria = {route: "Already-authorized legal route." for route in state.legal_routes}
    questions = (QuestionSpec(
        question_id=question_id, family=source["family"],
        question_type=source["type"], instructions=source["instructions"],
        criteria=tuple((key, description) for key, description in criteria.items())),)
    policy_hash = "1" * 64
    state_digest = canonical_hash(state.to_dict())
    bundle_digest = canonical_hash([q.to_dict() for q in questions])
    request_digest = canonical_hash({
        "identity": identity.to_dict(), "sequence": 1,
        "state_digest": state_digest, "question_bundle_digest": bundle_digest,
        "policy_hash": policy_hash})
    return SystemOneFrame(
        identity=identity, request_id="request-1", sequence=1,
        state_digest=state_digest,
        question_bundle_digest=bundle_digest,
        request_digest=request_digest, policy_hash=policy_hash,
        backend_id=backend, model_id=model, model_revision=revision,
        model_content_hash=content_hash, provider_protocol_hash=provider_hash,
        calibration_bundle_hash=canonical_hash({}), state=state, questions=questions,
        protocol_version=protocol_version or record.protocol_version,
        protocol_hash=protocol_hash or record.protocol_hash)


def test_active_record_valid_for_matching_frame():
    assert _record().valid_for(_frame()) is True


# ------------------------------------------------ checkpoint / weight identity
def test_replaced_weights_invalidate_qualification():
    assert _record().valid_for(_frame(content_hash="f" * 64)) is False


def test_superseded_revision_invalidates_qualification():
    assert _record().valid_for(_frame(revision="0" * 40)) is False


def test_different_backend_invalidates_qualification():
    assert _record().valid_for(_frame(backend="some-other-backend")) is False


def test_different_provider_protocol_invalidates_qualification():
    assert _record().valid_for(_frame(provider_hash="9" * 64)) is False


# ------------------------------------------------ protocol / schema drift
def test_protocol_version_drift_is_rejected_at_construction():
    """Schema/protocol drift fails closed on BOTH sides of the boundary.

    Neither ``SystemOneFrame`` nor ``CalibrationRecord`` can be constructed with a
    non-current protocol version, so a record calibrated under another protocol
    cannot exist, let alone serve a live frame.
    """
    with pytest.raises(ValueError):
        _record(protocol_version="0.0.0-legacy")
    with pytest.raises(ValueError):
        _frame(protocol_version="0.0.0-legacy")


def test_protocol_hash_drift_invalidates_qualification():
    record = replace(_record(), protocol_hash="0" * 64)
    assert record.valid_for(_frame()) is False


def test_question_registry_drift_invalidates_qualification():
    record = replace(_record(), question_registry_hash="0" * 64)
    assert record.valid_for(_frame()) is False


def test_state_projection_schema_drift_invalidates_qualification():
    record = replace(_record(), state_projection_schema_hash="0" * 64)
    assert record.valid_for(_frame()) is False


# ------------------------------------------------ status / drift
def test_inactive_status_invalidates_qualification():
    assert _record(status="candidate").valid_for(_frame()) is False
    assert _record(status="stale").valid_for(_frame()) is False
    assert _record(status="rolled_back").valid_for(_frame()) is False


def test_drift_status_invalidates_qualification():
    assert _record(drift_status="warning").valid_for(_frame()) is False
    assert _record(drift_status="drifted").valid_for(_frame()) is False


# ------------------------------------------------ holdout quality gates
@pytest.mark.parametrize("field,value", [
    ("holdout_accuracy", 0.79),
    ("holdout_brier", 0.26),
    ("holdout_ece", 0.13),
])
def test_failed_holdout_quality_invalidates_qualification(field, value):
    assert _record(**{field: value}).valid_for(_frame()) is False


# ------------------------------------------------ sample-count gates
def test_insufficient_total_outcomes_invalidates_qualification():
    """Below the 100-outcome floor, with partitions still consistent."""
    record = _record(sample_count=99, training_count=59, calibration_count=20,
                     holdout_count=20)
    assert record.valid_for(_frame()) is False


@pytest.mark.parametrize("field,value", [
    ("independent_trajectory_count", 29),
    ("independent_fixture_count", 29),
])
def test_insufficient_independence_is_rejected_at_construction(field, value):
    """The independent trajectory/fixture floors are construction-time invariants.

    An under-sampled record cannot be built at all, so it can never be consulted
    by ``valid_for``.
    """
    with pytest.raises(ValueError):
        _record(**{field: value})


# ------------------------------------------------ cohort scope
def test_uncovered_work_mode_invalidates_qualification():
    """A qualification for delivery work must not serve another supported mode."""
    state = _state(work_mode="research")
    assert _record().valid_for(_frame(state=state)) is False


def test_uncovered_deliverable_kind_invalidates_qualification():
    state = _state(deliverable_kind="analysis")
    assert _record().valid_for(_frame(state=state)) is False


def test_thin_cohort_is_rejected_at_construction():
    """A cohort below its minimum cannot be expressed as a calibration record.

    The count floor is enforced by ``CalibrationRecord`` itself, so an
    under-sampled applicability cohort fails closed before it can ever be
    consulted by ``valid_for``.
    """
    with pytest.raises(ValueError):
        _record(cohort_counts=(("delivery", "code_behavior", 5),))


# ------------------------------------------------ family scoping
def test_qualification_is_family_specific():
    record = _record(question_id="progress_relation")
    assert record.valid_for(_frame(question_id="retry_relation")) is False


def test_route_preference_requires_matching_legal_route_context():
    """route_preference is bound to the eligible-route set it was calibrated on."""
    record = _record(
        question_id="route_preference",
        holdout_class_metrics=tuple(
            (label, 1, 0, 0, 1.0, 1.0)
            for label in QUESTION_REGISTRY["route_preference"]["criteria"]),
        candidate_context_counts=((
            3, canonical_hash(sorted(("implement", "diagnose", "verify"))),
            canonical_hash(sorted(("software",))), 30),),
    )
    state = _state(domain_tags=("software",),
                   legal_routes=("implement", "diagnose", "verify"))
    matching = _frame(question_id="route_preference", state=state)
    assert record.valid_for(matching) is True

    # Same record against a different legal-route set must not serve.
    other_state = replace(state, legal_routes=("implement", "verify"))
    other = _frame(question_id="route_preference", state=other_state)
    assert record.valid_for(other) is False


def test_route_preference_rejects_thin_route_cohort_at_construction():
    """A route-context sample below the cohort minimum cannot be expressed.

    ``CalibrationRecord`` rejects an under-sampled route context at construction,
    so a thin route cohort can never be consulted by ``valid_for``.
    """
    with pytest.raises(ValueError):
        _record(
            question_id="route_preference",
            holdout_class_metrics=tuple(
                (label, 1, 0, 0, 1.0, 1.0)
                for label in QUESTION_REGISTRY["route_preference"]["criteria"]),
            candidate_context_counts=((
                3, canonical_hash(sorted(("implement", "diagnose", "verify"))),
                canonical_hash(sorted(("software",))), 5),),
        )


# ------------------------------------------------ disqualification is per-artifact
def test_disqualification_does_not_ban_the_family():
    """A stale record fails; a fresh record for the same family qualifies.

    Disqualification binds to the artifact combination, not to the task family,
    so a rehabilitated checkpoint can be qualified independently.
    """
    stale = _record(status="rolled_back")
    assert stale.valid_for(_frame()) is False

    fresh_revision = "NEWREVISION0000000000000000000000"
    fresh = _record(status="active", model_revision=fresh_revision)
    frame = _frame(revision=fresh_revision)
    assert fresh.valid_for(frame) is True
