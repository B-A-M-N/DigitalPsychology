"""Durable decision observations and independent System-1 outcome labels."""
from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from .feedback_loop import Event, EventFactory, SQLiteSink
from .protocol_artifact import QUESTION_REGISTRY
from .system_one_calibration import (
    VALID_OUTCOME_AUTHORITIES,
    validate_host_evaluator_receipt,
)
from .system_one_contracts import SystemOneFrame, SystemOneJudgment
from .system_one_diagnostics import validate_inference_diagnostics

_HOST_BASELINE_VERSION = "deterministic-host-baseline/v2"
_PROGRESS_BASELINES = {
    "GOAL_PROGRESS": "goal_progress",
    "BLOCKER_REMOVED": "blocker_removed",
    "DIAGNOSTIC_PROGRESS": "diagnostic_progress",
    "VALIDATION_EVIDENCE": "verification_progress",
    "VERIFICATION_PROGRESS": "verification_progress",
    "RECOVERY_PROGRESS": "recovery_progress",
}


def _host_baseline_labels(frame: SystemOneFrame) -> dict[str, str]:
    """Emit only baselines directly derivable from the immutable host frame."""
    result: dict[str, str] = {}
    legal_labels = {question.question_id: {label for label, _ in question.criteria}
                    for question in frame.questions}
    candidate = frame.state.candidate
    if ("action_relation" in legal_labels
            and candidate.host_declared_relation in legal_labels["action_relation"]):
        result["action_relation"] = str(candidate.host_declared_relation)
    if "progress_relation" in legal_labels:
        observed = [item for item in frame.state.activity
                    if item.source == "host_observed"]
        if observed:
            latest = max(observed, key=lambda item: item.sequence)
            baseline = _PROGRESS_BASELINES.get(latest.progress_class or "", "no_progress")
            if baseline in legal_labels["progress_relation"]:
                result["progress_relation"] = baseline
    if "retry_relation" in legal_labels and frame.state.failed_approaches:
        prior_actions = {item.planned_action_class
                         for item in frame.state.failed_approaches}
        if candidate.action_class in prior_actions:
            result["retry_relation"] = "equivalent_retry"
        elif candidate.action_class and None not in prior_actions:
            result["retry_relation"] = "materially_new"
    if "goal_drift" in legal_labels and candidate.affected_criterion_ids:
        statuses = {item.criterion_id: item.status for item in frame.state.criteria}
        affected = [statuses[item] for item in candidate.affected_criterion_ids
                    if item in statuses]
        if affected and len(affected) == len(candidate.affected_criterion_ids):
            if any(status in {"unmet", "active", "partially_observed"}
                   for status in affected):
                result["goal_drift"] = "no"
            elif all(status == "satisfied" for status in affected):
                result["goal_drift"] = "yes"
    if "route_preference" in legal_labels:
        legal_routes = tuple(frame.state.legal_routes)
        if legal_routes and legal_routes[0] in legal_labels["route_preference"]:
            result["route_preference"] = legal_routes[0]
    return result


def _system_one_observation(raw_frame: Mapping[str, Any],
                            raw_result: Mapping[str, Any]) -> tuple[Any, Any]:
    status = raw_result.get("status")
    if status not in {"ok", "shadow"}:
        return None
    if not isinstance(raw_frame, Mapping):
        raise ValueError("successful System-1 observation has no prepared frame")
    raw_judgment = raw_result.get("judgment")
    if not isinstance(raw_judgment, Mapping):
        raise ValueError("valid System-1 result has no judgment")

    frame = SystemOneFrame.from_mapping(raw_frame)
    judgment = SystemOneJudgment.from_mapping(raw_judgment)
    if (frame.identity != judgment.identity
            or frame.request_id != judgment.request_id
            or frame.sequence != judgment.sequence
            or frame.state_digest != judgment.state_digest
            or frame.question_bundle_digest != judgment.question_bundle_digest
            or frame.request_digest != judgment.request_digest
            or frame.policy_hash != judgment.policy_hash
            or frame.question_ids != tuple(item.question_id for item in judgment.answers)):
        raise ValueError("System-1 judgment does not match its prepared frame")
    return frame, judgment


def _observation_payload(frame: Any, judgment: Any, *, status: str,
                         receipt: Mapping[str, Any], basis: Mapping[str, Any]) -> dict[str, Any]:
    semantic_influenced = bool(
        receipt.get("changed_selection") or receipt.get("changed_mode")
        or receipt.get("changed_frameworks")
        or receipt.get("changed_statework_preference"))
    return {
        "system_one_decision_id": judgment.request_id,
        "system_one_mode": "shadow" if status == "shadow" else "advisory",
        "system_one_backend_id": judgment.backend_id,
        "system_one_model_id": judgment.model_id,
        "system_one_model_revision": judgment.model_revision,
        "system_one_model_content_hash": judgment.model_content_hash,
        "system_one_provider_protocol_hash": judgment.provider_protocol_hash,
        "system_one_question_registry_hash": frame.canonical_payload()[
            "question_registry_hash"],
        "system_one_state_projection_schema_hash": frame.canonical_payload()[
            "state_projection_schema_hash"],
        "system_one_work_mode": frame.state.task["work_mode"],
        "system_one_deliverable_kind": frame.state.task["deliverable_kind"],
        "system_one_protocol_version": frame.protocol_version,
        "system_one_protocol_hash": frame.protocol_hash,
        "system_one_calibration_hash": judgment.calibration_bundle_hash,
        "system_one_sequence": judgment.sequence,
        "system_one_state_digest": judgment.state_digest,
        "system_one_question_bundle_hash": judgment.question_bundle_digest,
        "system_one_status": status,
        "system_one_legal_routes": list(frame.state.legal_routes),
        "system_one_domain_tags": list(frame.state.task["domain_tags"]),
        "system_one_answers": {
            answer.question_id: answer.to_dict() for answer in judgment.answers},
        "system_one_semantic_influenced": semantic_influenced,
        "system_one_tick_id": str(basis.get("tick_id") or ""),
        "system_one_boundary_kind": str(basis.get("boundary_kind") or ""),
        "system_one_boundary_sequence": int(basis.get("boundary_sequence", 0)),
        "system_one_action_index": int(basis.get("host_action_index", 0)),
        "system_one_question_ids": [answer.question_id for answer in judgment.answers],
        "system_one_criterion_ids": [item.criterion_id for item in frame.state.criteria],
        "system_one_candidate_relation_certainty": frame.state.candidate.relation_certainty,
        "system_one_candidate_host_relation": (
            frame.state.candidate.host_declared_relation),
        "system_one_candidate_criterion_ids": list(
            frame.state.candidate.affected_criterion_ids),
        "system_one_semantic_influence_receipt_id": receipt.get("receipt_id"),
        "system_one_counterfactual_candidate_id": receipt.get(
            "counterfactual_candidate_id"),
        "system_one_selected_candidate_id": receipt.get("selected_candidate_id"),
        "system_one_changed_selection": bool(receipt.get("changed_selection", False)),
        "system_one_changed_mode": bool(receipt.get("changed_mode", False)),
        "system_one_changed_frameworks": list(
            receipt.get("changed_frameworks") or ()),
        "system_one_changed_statework_preference": bool(
            receipt.get("changed_statework_preference", False)),
        "system_one_host_baseline_version": _HOST_BASELINE_VERSION,
        "system_one_host_baseline_labels": _host_baseline_labels(frame),
    }


def _observation_event(identity: Any, frame: Any, judgment: Any,
                       payload: Mapping[str, Any]) -> dict[str, Any]:
    event_id = "dp-s1-" + hashlib.sha256(
        f"{identity.scope}|{judgment.request_id}".encode("utf-8")).hexdigest()[:40]
    event = Event(
        event_id=event_id,
        timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        namespace_id=identity.namespace_id,
        application_id=identity.application_id,
        application_instance_id=identity.application_instance_id,
        agent_instance_id=identity.agent_instance_id,
        session_id=identity.session_id,
        task_id=identity.task_id,
        attempt_id=identity.attempt_id,
        segment_id=identity.segment_id,
        agent_id=identity.agent_id,
        behavioral_subject=identity.behavioral_subject,
        category="decision",
        event_type="system_one_decision",
        actor_id="digitalpsychology_runtime",
        authority_source="digitalpsychology_system_one",
        scope=identity.scope,
        goal_contract_hash=identity.goal_contract_hash,
        interaction_id=frame.identity.interaction_id,
        provider_id=judgment.backend_id,
        model_id=judgment.model_id,
        model_revision=judgment.model_revision,
        model_capability_hash=judgment.provider_protocol_hash,
        payload=payload,
    )
    return EventFactory.from_dict(event.to_dict()).to_dict()


def prepare_system_one_decision_event(
        descriptor: Any, basis: Mapping[str, Any]) -> dict[str, Any] | None:
    """Build durable events for each valid provider judgment in a committed tick."""
    system_one = basis.get("system_one")
    if not isinstance(system_one, Mapping):
        return None
    raw_result = system_one.get("result")
    if not isinstance(raw_result, Mapping):
        return None
    receipt = basis.get("semantic_influence_receipt")
    receipt = receipt if isinstance(receipt, Mapping) else {}
    raw_frame = system_one.get("frame")
    if raw_result.get("portfolio") is True:
        observations = raw_result.get("observations")
        if not isinstance(observations, list):
            return None
        events = []
        for observation in observations:
            if not isinstance(observation, Mapping):
                continue
            observation_result = observation.get("result")
            if not isinstance(observation_result, Mapping):
                continue
            observation_frame = raw_frame.get(observation.get("provider_id"))
            if observation_frame is None:
                continue
            parsed = _system_one_observation(observation_frame, observation_result)
            if parsed is None:
                continue
            frame, judgment = parsed
            payload = _observation_payload(
                frame, judgment, status=observation_result.get("status"),
                receipt=receipt, basis=basis)
            raw_diagnostics = observation_result.get("inference_diagnostics")
            if isinstance(raw_diagnostics, Mapping):
                payload["system_one_inference_diagnostics"] = (
                    validate_inference_diagnostics(
                        raw_diagnostics, judgment.backend_id))
            events.append(_observation_event(descriptor.identity, frame, judgment, payload))
        return events[0] if events else None
    parsed = _system_one_observation(raw_frame, raw_result)
    if parsed is None:
        return None
    frame, judgment = parsed
    payload = _observation_payload(
        frame, judgment, status=raw_result.get("status"), receipt=receipt,
        basis=basis)
    raw_diagnostics = raw_result.get("inference_diagnostics")
    if isinstance(raw_diagnostics, Mapping):
        payload["system_one_inference_diagnostics"] = validate_inference_diagnostics(
            raw_diagnostics, judgment.backend_id)
    return _observation_event(descriptor.identity, frame, judgment, payload)


class SystemOneOutcomeLabeler:
    """Persist host-derived labels tied to authenticated later evidence.

    The input deliberately contains no provider answer, chosen DP policy, or
    agent-authored claim. The trusted host or validator selects a family label
    from later evidence and supplies its durable evidence reference.
    """

    def __init__(self, sink: SQLiteSink) -> None:
        self._sink = sink

    def record(self, *, decision_event_id: str, namespace_id: str,
               application_instance_id: str, question_id: str, label: str,
               authority: str, evidence_ref: str,
               evidence_receipt: Mapping[str, Any] | None = None,
               expected_scope: str | None = None) -> str:
        if question_id not in QUESTION_REGISTRY:
            raise ValueError("unsupported System-1 outcome question family")
        if authority not in VALID_OUTCOME_AUTHORITIES:
            raise ValueError("System-1 outcome requires an independent evidence authority")
        if not isinstance(evidence_ref, str) or not evidence_ref.strip() or len(evidence_ref) > 256:
            raise ValueError("System-1 outcome requires a durable evidence reference")

        decision = self._sink.load_authenticated_event(
            decision_event_id, namespace_id=namespace_id,
            application_instance_id=application_instance_id)
        if (decision is None or decision.category != "decision"
                or decision.event_type != "system_one_decision"
                or decision.actor_id != "digitalpsychology_runtime"
                or decision.authority_source != "digitalpsychology_system_one"):
            raise ValueError("outcome must reference an authenticated System-1 decision")
        if expected_scope is not None and decision.scope != expected_scope:
            raise ValueError("outcome decision escaped its host control session")
        payload = decision.payload or {}
        decision_id = payload.get("system_one_decision_id")
        answers = payload.get("system_one_answers")
        if (not isinstance(decision_id, str) or not decision_id
                or not isinstance(answers, Mapping) or question_id not in answers):
            raise ValueError("decision event did not ask this question family")
        if payload.get("system_one_status") not in {"ok", "shadow"}:
            raise ValueError("decision event does not contain a valid provider result")
        allowed_labels = set(QUESTION_REGISTRY[question_id]["criteria"])
        if question_id == "route_preference":
            legal_routes = payload.get("system_one_legal_routes")
            if (not isinstance(legal_routes, list)
                    or any(not isinstance(route, str) or not route for route in legal_routes)):
                raise ValueError("route outcome requires the decision's legal candidate set")
            allowed_labels = set(legal_routes)
        if label not in allowed_labels:
            raise ValueError("System-1 outcome label is outside its question family")

        now = datetime.now(timezone.utc)
        try:
            decision_time = datetime.fromisoformat(decision.timestamp.replace("Z", "+00:00"))
        except (AttributeError, ValueError) as exc:
            raise ValueError("decision event timestamp is invalid") from exc
        if decision_time.tzinfo is None or now <= decision_time:
            raise ValueError("independent outcome must follow its decision")
        validated_receipt = validate_host_evaluator_receipt(
            evidence_receipt, decision_event=decision, question_id=question_id,
            label=label, authority=authority, evidence_ref=evidence_ref,
            control_scope=expected_scope, evaluated_at=now)

        event_id = "dp-s1o-" + hashlib.sha256(
            f"{namespace_id}|{application_instance_id}|{decision_event_id}|{question_id}"
            .encode("utf-8")).hexdigest()[:40]
        existing = self._sink.load_authenticated_event(
            event_id, namespace_id=namespace_id,
            application_instance_id=application_instance_id)
        outcome_key = f"system_one_outcome_{question_id}"
        if existing is not None:
            prior = existing.payload or {}
            if (existing.event_type == "system_one_outcome"
                    and prior.get("system_one_outcome_for") == decision_id
                    and prior.get(outcome_key) == label
                    and prior.get("system_one_outcome_authority") == authority
                    and prior.get("system_one_outcome_evidence_ref") == evidence_ref
                    and prior.get("system_one_outcome_evidence_receipt")
                    == validated_receipt):
                return event_id
            raise ValueError("a different independent outcome is already recorded")

        event = Event(
            event_id=event_id,
            timestamp=now.isoformat().replace("+00:00", "Z"),
            namespace_id=decision.namespace_id,
            application_id=decision.application_id,
            application_instance_id=decision.application_instance_id,
            agent_instance_id=decision.agent_instance_id,
            session_id=decision.session_id,
            task_id=decision.task_id,
            attempt_id=decision.attempt_id,
            segment_id=decision.segment_id,
            agent_id=decision.agent_id,
            behavioral_subject=decision.behavioral_subject,
            category="validation",
            event_type="system_one_outcome",
            actor_id="digitalpsychology_runtime",
            authority_source=authority,
            scope=decision.scope,
            goal_contract_hash=decision.goal_contract_hash,
            interaction_id=decision.interaction_id,
            evidence_refs=[evidence_ref],
            payload={
                "system_one_outcome_for": decision_id,
                outcome_key: label,
                "system_one_outcome_authority": authority,
                "system_one_outcome_evidence_ref": evidence_ref,
                "system_one_outcome_evidence_receipt": validated_receipt,
                "system_one_independent_outcome": True,
            },
        )
        validated = EventFactory.from_dict(event.to_dict())
        self._sink.emit_authenticated(validated, authenticated=True)
        return event_id


__all__ = ["SystemOneOutcomeLabeler", "prepare_system_one_decision_event"]
