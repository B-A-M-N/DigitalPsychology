"""Feature parity: offline evaluation frames vs the real runtime projection.

The finding being corrected: `build_frame()` produced frames with
`workflows=()` and generic option labels, so the offline benchmark tested Laya on
substantially less information than the production runtime supplies. That is a
representation defect, not a model defect.

These tests pin the contract that the offline frame must carry the SAME
information the runtime projection carries, so any measured improvement (or
failure) is attributable to the model rather than to a thinner input.

Compares against the real projection in lib/control/system_one.py.
"""
from __future__ import annotations

from lib.system_one_contracts import WorkflowFact
from qualification.corpus_schema import (
    ControllerDecision,
    CorpusRecord,
    ObservedOutcome,
    SemanticTarget,
)
from qualification.inference_bridge import TaskContext, build_frame

CKPT_REVISION = "1a793eb568e6718f15941d08f85432581df534e3"
CKPT_HASH = "c" * 64

# The three real CSW bundles, with the purpose/domain vocabulary the runtime
# exposes from the compiled registry.
# Required-evidence sets taken verbatim from the authoritative CSW
# transitions.yaml contracts (getter 6 sets, gitter 3, infrae 16).
WORKFLOWS = (
    ("getter", "Establish repository truth and apply authorised changes",
     "repository", "OBSERVED", ("PLANNED", "CHANGING", "VERIFIED", "STABLE"), True,
     # getter.observed.plan.planned requires [intent, risk]
     ("intent", "risk")),
    ("gitter", "Verify repository truth against recorded state",
     "repository", "UNKNOWN", ("OBSERVED", "STALE"), True,
     # gitter.unknown.observe.observed requires [repository_truth]
     ("repository_truth",)),
    ("infrae", "Discover, model and change infrastructure state",
     "infrastructure", "UNKNOWN", ("OBSERVED", "MODELED", "ASSESSED", "PLANNED",
                                   "CHANGING", "VERIFIED", "STABLE"), True,
     # infrae.unknown.discover.observed requires [discovery]
     ("discovery",)),
)


def _context():
    return TaskContext(
        objective="Implement a repository change that must be verified before "
                  "mutation.",
        work_mode="delivery", deliverable_kind="code_behavior",
        current_state="IMPLEMENTED", domain_tags=("software",),
        workflows=WORKFLOWS,
        available_evidence=("repository_truth", "intent"),
        non_goals=("unrelated refactoring",))


def _record():
    return CorpusRecord(
        frame_id="parity-1",
        semantic_target=SemanticTarget(
            frame_id="parity-1", appropriate_route="getter",
            eligible_routes=("getter", "gitter", "infrae"),
            label_provenance="independent_expert_review",
            labelled_by="human-reviewer-1"),
        controller_decision=ControllerDecision(
            selected_route="getter",
            available_candidates=("getter", "gitter", "infrae"),
            policy_snapshot_hash="1" * 64, decision_reason="deterministic"),
        observed_outcome=ObservedOutcome(
            success_criteria_met=True, evidence_refs=("e1",),
            observed_by="host_validator"),
        checkpoint_revision=CKPT_REVISION, checkpoint_content_hash=CKPT_HASH,
        statework_contract_version="getter-v1", calibration_id="none",
        policy_snapshot_hash="1" * 64, trajectory_id="t-parity",
        subject_ref="repo:example")


def _frame():
    return build_frame(
        _record(), _context(), sequence=1,
        backend_id="digitalpsychology-laya-native",
        model_id="laya-typed-decisions", model_revision=CKPT_REVISION,
        model_content_hash=CKPT_HASH, provider_protocol_hash="b" * 64)


def test_frame_carries_real_workflow_facts():
    """The representation defect: workflows=() gave the model nothing to compare."""
    frame = _frame()
    assert frame.state.workflows, "offline frame must carry workflow facts"
    assert len(frame.state.workflows) == 3
    ids = {w.statework_id for w in frame.state.workflows}
    assert ids == {"getter", "gitter", "infrae"}


def test_workflow_facts_carry_the_distinguishing_vocabulary():
    frame = _frame()
    by_id = {w.statework_id: w for w in frame.state.workflows}
    getter = by_id["getter"]
    # The fields that actually separate getter from gitter.
    assert getter.purpose
    assert getter.domain == "repository"
    assert getter.current_state == "OBSERVED"
    assert getter.legal_next_states
    # getter and gitter share a domain but differ in purpose and legal states.
    assert by_id["gitter"].purpose != getter.purpose
    assert by_id["gitter"].legal_next_states != getter.legal_next_states


def test_option_labels_are_distinguishable():
    """Generic labels make every option identical -- useless input."""
    from qualification.inference_bridge import _route_descriptions
    legal = ("getter", "gitter", "infrae")
    options = _route_descriptions(_context(), legal)
    labels = [label for _sid, label in options]
    assert len(set(labels)) == 3, f"labels must differ: {labels}"
    assert all("repository" in l or "infrastructure" in l for l in labels), labels
    # The statework id must still be recoverable as the option key.
    assert [sid for sid, _l in options] == list(legal)


def test_frame_matches_runtime_semantic_state_shape():
    """Structural parity with lib/control/system_one.py's SemanticState."""
    frame = _frame()
    state = frame.state
    # Required task keys, exactly as SemanticState validates.
    assert set(state.task) == {"objective", "work_mode", "deliverable_kind",
                               "task_complexity", "phase", "active_segment",
                               "active_statework", "active_flow", "non_goals",
                               "domain_tags"}
    assert set(state.situation) == {"current_state", "blockers",
                                    "actions_since_goal_progress",
                                    "actions_since_artifact_change",
                                    "verification_actions_since_artifact_change",
                                    "recovery_epochs", "repeated_branch_count"}
    assert state.legal_routes == ("getter", "gitter", "infrae")
    assert isinstance(state.workflows[0], WorkflowFact)


def test_option_order_perturbation_does_not_change_the_frame():
    """Order must not leak the answer: a re-ordered candidate set is a
    different question, judged on its own merits."""
    from qualification.inference_bridge import _route_descriptions
    a = _route_descriptions(_context(), ("getter", "gitter", "infrae"))
    b = _route_descriptions(_context(), ("infrae", "getter", "gitter"))
    assert dict(a) == dict(b), "option content must not depend on order"
    assert [s for s, _ in a] != [s for s, _ in b]


def test_workflows_do_not_encode_the_correct_route():
    """Anti-leakage: the projection must not reveal which route is preferred.

    Purpose, domain and legal-next-states are legitimate pre-decision facts.
    If any field changed with the gold answer, the benchmark would be circular.
    """
    record = _record()
    for label in ("getter", "gitter", "infrae"):
        variant = CorpusRecord(
            frame_id=record.frame_id,
            semantic_target=SemanticTarget(
                frame_id=record.frame_id, appropriate_route=label,
                eligible_routes=("getter", "gitter", "infrae"),
                label_provenance="independent_expert_review",
                labelled_by="human-reviewer-1"),
            controller_decision=record.controller_decision,
            observed_outcome=record.observed_outcome,
            checkpoint_revision=record.checkpoint_revision,
            checkpoint_content_hash=record.checkpoint_content_hash,
            statework_contract_version=record.statework_contract_version,
            calibration_id=record.calibration_id,
            policy_snapshot_hash=record.policy_snapshot_hash,
            trajectory_id=record.trajectory_id, subject_ref=record.subject_ref)
        frame = build_frame(
            variant, _context(), sequence=1,
            backend_id="digitalpsychology-laya-native",
            model_id="laya-typed-decisions", model_revision=CKPT_REVISION,
            model_content_hash=CKPT_HASH, provider_protocol_hash="b" * 64)
        # The workflow facts must be identical regardless of the gold label.
        assert [w.to_dict() for w in frame.state.workflows] == [
            w.to_dict() for w in _frame().state.workflows], (
            "workflow facts must not vary with the gold answer")


# ------------------------------------------------- evidence obligations
def test_required_evidence_distinguishes_getter_from_gitter():
    """The evidence obligation is what actually separates the two.

    getter and gitter share the repository domain. Their required-evidence sets
    do not overlap meaningfully, so exposing obligations gives the model a real
    discriminator rather than a name to memorise.
    """
    from qualification.inference_bridge import _route_descriptions
    options = dict(_route_descriptions(_context(),
                                       ("getter", "gitter", "infrae")))
    # getter.observed.plan.planned requires [intent, risk]
    assert "intent,risk" in options["getter"]
    # gitter.unknown.observe.observed requires [repository_truth]
    assert "repository_truth" in options["gitter"]
    assert options["getter"] != options["gitter"]


def test_held_vs_missing_evidence_is_distinguished():
    """Availability is a pre-decision fact and must be shown as such."""
    from qualification.inference_bridge import _route_descriptions
    options = dict(_route_descriptions(_context(), ("getter", "gitter")))
    assert "(held: intent)" in options["getter"]
    assert "(held: repository_truth)" in options["gitter"]


def test_bare_context_still_builds_a_valid_frame():
    """Backward compatibility: an empty workflow tuple must not crash."""
    from qualification.inference_bridge import TaskContext
    bare = TaskContext(objective="Establish repository truth.",
                       work_mode="diagnose",
                       deliverable_kind="code_behavior")
    frame = build_frame(
        _record(), bare, sequence=1,
        backend_id="digitalpsychology-laya-native",
        model_id="laya-typed-decisions", model_revision=CKPT_REVISION,
        model_content_hash=CKPT_HASH, provider_protocol_hash="b" * 64)
    assert frame.state.workflows == ()
    assert frame.questions[0].question_id == "route_preference"
