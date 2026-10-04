from __future__ import annotations

from lib.behavior_state import BehaviorState, CSWBehaviorSnapshot
from lib.runtime.projection import (ModelProjection, build_behavior_projection,
                                    cached_replace_model_projection,
                                    model_projection_is_current,
                                    replace_model_projection)


def test_model_projection_replaces_old_guidance_without_exposing_audit_fields():
    context = "Host prompt before the DP guidance slot."
    for revision in range(100):
        text = (
            "===== CURRENT BEHAVIOR GUIDANCE =====\n"
            f"Required work: revision {revision}."
        )
        context = replace_model_projection(
            context, ModelProjection(text=text, semantic_digest=f"digest-{revision}"))

    assert context.count("===== CURRENT BEHAVIOR GUIDANCE =====") == 1
    assert "Required work: revision 99." in context
    assert "revision 0." not in context
    assert "behavior_revision" not in context
    assert "intervention_id" not in context
    assert "directive_id" not in context
    assert "Host prompt before the DP guidance slot." in context


def test_internal_revision_without_semantic_change_is_a_projection_noop():
    first = build_behavior_projection(
        BehaviorState(revision=1), CSWBehaviorSnapshot(revision=1),
        objective="Implement the requested view", goal_set=None,
        cfw_connected=True, csw_connected=True, outcome_evidence_connected=True)
    second = build_behavior_projection(
        BehaviorState(revision=2), CSWBehaviorSnapshot(revision=2),
        objective="Implement the requested view", goal_set=None,
        cfw_connected=True, csw_connected=True, outcome_evidence_connected=True)

    assert first.audit.metadata["behavior_revision"] == 1
    assert second.audit.metadata["behavior_revision"] == 2
    assert first.model.semantic_digest == second.model.semantic_digest

    context = replace_model_projection("Host context", first.model)
    assert replace_model_projection(context, second.model) is context


def test_stable_host_objective_can_be_omitted_and_bridge_reuses_current_slot():
    projection = build_behavior_projection(
        BehaviorState(revision=1), CSWBehaviorSnapshot(revision=1),
        objective="Implement the requested view", goal_set=None,
        cfw_connected=True, csw_connected=True, outcome_evidence_connected=True,
        host_has_stable_objective_context=True,
    )
    assert "User objective" not in projection.model.text
    context = replace_model_projection("Stable host task objective", projection.model)
    assert model_projection_is_current(context, projection.model)

    cached, digest = cached_replace_model_projection(
        context, projection.model, projection.model.semantic_digest)
    assert cached is context
    assert digest == projection.model.semantic_digest


def test_all_nonframework_behavior_controls_have_visible_projection_effects():
    baseline = build_behavior_projection(
        BehaviorState(), CSWBehaviorSnapshot(revision=0), objective="", goal_set=None,
        cfw_connected=True, csw_connected=False, outcome_evidence_connected=False)
    tuned_state = BehaviorState(
        cognitive_mode="VERIFY", verification_intensity="strict",
        exploration_budget="low", retry_policy="no_retry", recovery_level=2,
        context_strategy="compact", response_strategy="structured")
    tuned = build_behavior_projection(
        tuned_state, CSWBehaviorSnapshot(revision=0), objective="", goal_set=None,
        cfw_connected=True, csw_connected=False, outcome_evidence_connected=False)

    assert tuned.model.semantic_digest != baseline.model.semantic_digest
    for text in (
        "Current operating mode: verify.",
        "Keep exploration small",
        "Keep working context concise",
        "Organize the response into clear sections",
        "Check claimed progress",
        "change the approach before retrying",
        "If the current approach is blocked",
    ):
        assert text in tuned.model.text
    assert tuned.audit.metadata["model_controls"] == {
        "cognitive_mode": "VERIFY", "exploration_budget": "low",
        "context_strategy": "compact", "response_strategy": "structured",
        "verification_intensity": "strict", "retry_policy": "no_retry",
        "recovery_level": 2,
    }
