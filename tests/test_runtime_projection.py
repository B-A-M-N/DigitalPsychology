from __future__ import annotations

from lib.runtime.projection import ModelProjection, replace_model_projection


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
