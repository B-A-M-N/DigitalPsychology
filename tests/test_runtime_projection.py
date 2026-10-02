from __future__ import annotations

from lib.behavior_state import BehaviorState, CSWBehaviorSnapshot
from lib.runtime.projection import (ModelProjection, build_behavior_projection,
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
