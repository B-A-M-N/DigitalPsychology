"""Construct durable action and control-transition telemetry records."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from ..behavior_state import BehaviorState, BehavioralDecision
from ..control_protocol import ControlSessionDescriptor, parse_goal_set


def build_control_transition_trace(scope: str, prior_state: BehaviorState,
                              state: BehaviorState,
                              decision: BehavioralDecision,
                              basis: Mapping[str, Any],
                              applied_directive_ids: Sequence[str]
                              ) -> dict[str, Any]:
    from ..telemetry.control_trace import ControlTransitionTrace

    facts = dict(basis.get("behavioral_facts") or {})
    raw_receipt = basis.get("semantic_influence_receipt")
    receipt = dict(raw_receipt) if isinstance(raw_receipt, Mapping) else {}
    receipt_id = receipt.get("receipt_id")
    question_ids = list(
        str(item) for item in receipt.get("question_ids", ())
        if isinstance(item, str))[:8]
    system_one = basis.get("system_one")
    system_one = system_one if isinstance(system_one, Mapping) else {}
    frame = system_one.get("frame")
    frame = frame if isinstance(frame, Mapping) else {}
    for question in frame.get("questions", ()):
        if not isinstance(question, Mapping):
            continue
        question_id = question.get("question_id")
        if isinstance(question_id, str) and question_id and question_id not in question_ids:
            question_ids.append(question_id)
            if len(question_ids) >= 8:
                break
    opportunity_question_id = basis.get("semantic_query_opportunity_question_id")
    if (isinstance(opportunity_question_id, str) and opportunity_question_id
            and opportunity_question_id not in question_ids and len(question_ids) < 8):
        question_ids.append(opportunity_question_id)
    question_ids = tuple(question_ids)
    semantic_influenced = any(receipt.get(name) is True for name in (
        "changed_selection", "changed_mode", "changed_statework_preference"))
    semantic_influenced = semantic_influenced or bool(receipt.get("changed_frameworks"))
    old_intervention_ids = {item.intervention_id for item in prior_state.active_interventions}
    influenced_interventions = sum(
        1 for item in state.active_interventions
        if receipt_id and item.intervention_id not in old_intervention_ids
        and item.semantic_influence_receipt_id == receipt_id)
    linked_outcomes = [item for item in decision.intervention_outcomes
                       if item.semantic_influence_receipt_id]
    effective_interventions = sum(item.outcome == "effective" for item in linked_outcomes)
    ineffective_interventions = sum(
        item.outcome in {"ineffective", "regressed"} for item in linked_outcomes)
    influenced_intervention_ids = tuple(sorted(
        item.intervention_id for item in state.active_interventions
        if receipt_id and item.intervention_id not in old_intervention_ids
        and item.semantic_influence_receipt_id == receipt_id))
    semantic_outcome_links = tuple(sorted(
        (str(item.semantic_influence_receipt_id), str(item.intervention_id),
         str(item.outcome), int(item.observed_revision))
        for item in linked_outcomes
        if item.semantic_influence_receipt_id))
    raw_result = system_one.get("result")
    raw_result = raw_result if isinstance(raw_result, Mapping) else {}
    semantic_latency_ms = raw_result.get("latency_ms")
    from ..telemetry.behavioral_trace import classify_justification
    evidence_classes = []
    if facts.get("evidence_refs"):
        evidence_classes.append("host_evidence_ref")
    if facts.get("progress_made"):
        evidence_classes.append("host_progress")
    if facts.get("regression_observed"):
        evidence_classes.append("host_regression")
    if facts.get("cfw_signal_codes"):
        evidence_classes.append("cfw_signal")
    if basis.get("semantic_judgment_used"):
        evidence_classes.append("semantic_advisory")
    if facts.get("candidate_rejection_stage"):
        evidence_classes.append("cfw_action_gate")
    if facts.get("ward_hard_safety"):
        evidence_classes.append("ward_hard_safety")
    if not evidence_classes:
        evidence_classes.append("policy_observation")
    stage = facts.get("candidate_rejection_stage")
    from ..telemetry.material_progress import normalized_behavior_codes
    behavior_codes = normalized_behavior_codes(
        facts.get("active_trigger_codes") or (),
        facts.get("cfw_signal_codes") or ())
    justification = classify_justification(
        boundary_kind=str(basis.get("boundary_kind") or ""),
        hard_safety=bool(facts.get("ward_hard_safety")),
        validator_evidence=(basis.get("boundary_kind") == "validator"
                            and bool(facts.get("evidence_refs"))),
        csw_transition=(prior_state.active_stateworks != state.active_stateworks),
        deterministic_host_fact=bool(facts.get("regression_observed")),
        material_progress=bool(facts.get("material_progress_observed")),
        semantic_advisory=bool(basis.get("semantic_judgment_used")),
        heuristic=bool(behavior_codes),
    )
    causes = tuple(dict.fromkeys(
        [decision.reason] + [item.cause for item in decision.directives]))
    drift_detected = bool(behavior_codes & {"goal_drift", "semantic_goal_drift"})
    applied_ids = set(applied_directive_ids)
    drift_applied = any(
        item.directive_id in applied_ids
        and item.cause in {"goal_drift", "semantic_goal_drift"}
        for item in decision.directives)
    return ControlTransitionTrace(
        control_session_id=scope,
        boundary_sequence=int(basis["boundary_sequence"]),
        host_action_index=int(basis["host_action_index"]),
        boundary_kind=str(basis["boundary_kind"]),
        behavior_revision=state.revision,
        prior_mode=prior_state.cognitive_mode,
        resulting_mode=state.cognitive_mode,
        transition_causes=causes,
        justification_class=justification,
        semantic_query_attempted=bool(basis.get("semantic_query_attempted")),
        semantic_judgment_returned=bool(
            basis.get("semantic_judgment_returned")),
        semantic_judgment_used=bool(basis.get("semantic_judgment_used")),
        semantic_query_failed=bool(basis.get("semantic_query_failed")),
        semantic_question_ids=question_ids,
        semantic_influence_receipt_id=(
            str(receipt_id) if isinstance(receipt_id, str) and receipt_id else None),
        semantic_influenced_decision=semantic_influenced,
        semantic_candidate_change=bool(receipt.get("changed_selection")),
        semantic_mode_change=bool(receipt.get("changed_mode")),
        semantic_framework_changes=len(tuple(receipt.get("changed_frameworks") or ())),
        semantic_statework_change=bool(receipt.get("changed_statework_preference")),
        semantic_influenced_interventions=influenced_interventions,
        semantic_effective_interventions=effective_interventions,
        semantic_ineffective_interventions=ineffective_interventions,
        semantic_query_opportunity=(
            bool(basis.get("semantic_query_opportunity")) or
            bool(basis.get("semantic_query_attempted")) or
            bool(receipt and question_ids)),
        semantic_query_opportunity_question_id=(
            str(opportunity_question_id) if isinstance(opportunity_question_id, str)
            and opportunity_question_id else (question_ids[0] if question_ids else None)),
        semantic_latency_ms=(float(semantic_latency_ms)
                             if isinstance(semantic_latency_ms, (int, float))
                             and not isinstance(semantic_latency_ms, bool) else None),
        semantic_influenced_intervention_ids=influenced_intervention_ids,
        semantic_intervention_outcomes=semantic_outcome_links,
        goal_drift_detected=drift_detected,
        goal_drift_intervention_applied=drift_applied,
        cfw_rejection_stage=(str(stage) if stage else None),
        model_projection_digest=(str(basis.get("model_projection_digest"))
                                 if basis.get("model_projection_digest") else None),
        ward_hard_safety=bool(facts.get("ward_hard_safety")),
        cfw_preflight_decision=(
            str(facts.get("cfw_preflight_decision")
                or facts.get("candidate_preflight_result"))
            if (facts.get("cfw_preflight_decision")
                or (stage == "cfw_preflight"
                    and facts.get("candidate_preflight_result"))) else None),
        cfw_final_decision=(
            str(facts.get("cfw_final_decision")
                or facts.get("candidate_preflight_result"))
            if (facts.get("cfw_final_decision")
                or (stage == "cfw_final_gate"
                    and facts.get("candidate_preflight_result"))) else None),
        cfw_gate_receipts=facts.get("candidate_gate_receipts"),
    ).to_dict()

def build_committed_action_trace(descriptor: ControlSessionDescriptor,
                            prior_state: BehaviorState,
                            state: BehaviorState,
                            decision: BehavioralDecision,
                            basis: Mapping[str, Any],
                            application_events: list[Mapping[str, Any]],
                            applied_directive_ids: Sequence[str]) -> Any:
    """Build a committed-action record from private host/control evidence."""
    from ..attention import fairness_bound_actions
    from ..telemetry.behavioral_trace import ActionTrace, classify_justification

    goal_set = parse_goal_set(basis["goal_set"])
    action_index = int(basis["host_action_index"])
    last_seen = dict(state.attention_last_seen)
    runnable = goal_set.runnable_items
    ages = {
        item.criterion_id: max(
            0, action_index - last_seen.get(item.criterion_id,
                                            max(0, action_index - 1)))
        for item in runnable
    }
    fairness_bound = int(basis.get(
        "attention_fairness_bound_actions", fairness_bound_actions(len(runnable))))
    prior_lease = prior_state.attention_lease
    lease = state.attention_lease
    lease_started = bool(lease and (prior_lease is None
                                    or lease.lease_id != prior_lease.lease_id))
    rotated = bool(prior_lease and lease
                   and prior_lease.criterion_ids != lease.criterion_ids)
    prevented = tuple(str(item) for item in
                      basis.get("attention_starvation_prevented") or ())
    facts = dict(basis.get("behavioral_facts") or {})
    semantic_attempted = bool(basis.get("semantic_query_attempted"))
    semantic_result = bool(basis.get("semantic_judgment_returned"))
    semantic_failed = bool(basis.get("semantic_query_failed"))
    evidence_classes = set()
    if facts.get("evidence_refs"):
        evidence_classes.add("host_evidence_ref")
    if facts.get("progress_made"):
        evidence_classes.add("host_progress")
    if facts.get("regression_observed"):
        evidence_classes.add("host_regression")
    if facts.get("material_progress_observed"):
        evidence_classes.add("material_progress")
    if facts.get("ward_hard_safety"):
        evidence_classes.add("ward_hard_safety")
    if facts.get("active_trigger_codes"):
        evidence_classes.update("host_trigger:" + str(item)
                                for item in facts["active_trigger_codes"])
    if facts.get("cfw_signal_codes"):
        evidence_classes.update("cfw_signal:" + str(item)
                                for item in facts["cfw_signal_codes"])
    if semantic_result:
        evidence_classes.add("semantic_result")
    if facts.get("candidate_preflight_result"):
        evidence_classes.add("candidate_gate_rejection")
    if application_events:
        evidence_classes.add("intervention_application_receipt")
    projection_digest = basis.get("model_projection_digest")
    newly_applied = bool(application_events)
    from ..telemetry.material_progress import normalized_behavior_codes
    behavior_codes = normalized_behavior_codes(
        facts.get("active_trigger_codes") or (),
        facts.get("cfw_signal_codes") or ())
    goal_drift_detected = bool(behavior_codes & {
        "goal_drift", "semantic_goal_drift"})
    applied_ids = set(applied_directive_ids)
    goal_drift_applied = any(
        item.directive_id in applied_ids
        and item.cause in {"goal_drift", "semantic_goal_drift"}
        for item in decision.directives)
    candidate_stage = facts.get("candidate_rejection_stage")
    justification = classify_justification(
        hard_safety=bool(facts.get("ward_hard_safety")),
        csw_transition=(prior_state.active_stateworks != state.active_stateworks),
        deterministic_host_fact=bool(facts.get("regression_observed")),
        material_progress=bool(facts.get("material_progress_observed")),
        semantic_advisory=bool(basis.get("semantic_judgment_used")),
        heuristic=bool(behavior_codes or application_events),
    )
    preflight_decision = facts.get("cfw_preflight_decision")
    final_decision = facts.get("cfw_final_decision")
    if candidate_stage == "cfw_preflight" and not preflight_decision:
        preflight_decision = facts.get("candidate_preflight_result")
    if candidate_stage == "cfw_final_gate" and not final_decision:
        final_decision = facts.get("candidate_preflight_result")
    return ActionTrace(
        action_index=action_index,
        cognitive_mode=state.cognitive_mode,
        focus_criteria=tuple(state.focus_criteria),
        attention_ages=ages,
        fairness_bound=fairness_bound,
        max_starvation_age=max(ages.values(), default=0),
        mode_entered_action=(state.mode_lease.entered_action
                             if state.mode_lease else None),
        attention_lease_started=lease_started,
        attention_rotated=rotated,
        fairness_forced_rotation=bool(prevented),
        fairness_prevented_criteria=prevented,
        intervention_applied=newly_applied,
        abstained=decision.reason.startswith("abstain"),
        equivalent_retry=("equivalent_retry" in behavior_codes),
        goal_drift=goal_drift_applied,
        goal_drift_detected=goal_drift_detected,
        goal_drift_intervention_applied=goal_drift_applied,
        projection_digest=projection_digest,
        material_progress_criteria=tuple(
            str(item) for item in facts.get("material_progress_criteria") or ()),
        semantic_query_attempted=semantic_attempted,
        semantic_judgment_returned=semantic_result,
        semantic_judgment_used=bool(basis.get("semantic_judgment_used")),
        semantic_query_failed=semantic_failed,
        cfw_preflight_rejection=candidate_stage == "cfw_preflight",
        cfw_final_rejection=candidate_stage == "cfw_final_gate",
        cfw_preflight_decision=(str(preflight_decision)
                                if preflight_decision else None),
        cfw_final_decision=(str(final_decision)
                            if final_decision else None),
        authoritative_evidence_classes=tuple(sorted(evidence_classes)),
        justification_class=justification,
    )
