"""Durable decision observations and independent System-1 outcome labels."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from typing import Any, Mapping

from .feedback_loop import Event, EventFactory, SQLiteSink
from .protocol_artifact import QUESTION_REGISTRY
from .system_one_calibration import VALID_OUTCOME_AUTHORITIES
from .system_one_contracts import SystemOneFrame, SystemOneJudgment


def prepare_system_one_decision_event(
        descriptor: Any, basis: Mapping[str, Any]) -> dict[str, Any] | None:
    """Build an outbox event for a valid provider judgment in a committed tick."""
    system_one = basis.get("system_one")
    if not isinstance(system_one, Mapping):
        return None
    raw_result = system_one.get("result")
    if not isinstance(raw_result, Mapping):
        return None
    status = raw_result.get("status")
    if status not in {"ok", "shadow"}:
        return None
    raw_frame = system_one.get("frame")
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

    identity = descriptor.identity
    receipt = basis.get("semantic_influence_receipt")
    semantic_influenced = bool(
        isinstance(receipt, Mapping)
        and (receipt.get("changed_selection") or receipt.get("changed_mode")
             or receipt.get("changed_frameworks")
             or receipt.get("changed_statework_preference")))
    payload = {
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
    }
    event_id = "dp-s1-" + hashlib.sha256(
        f"{descriptor.scope}|{judgment.request_id}".encode("utf-8")).hexdigest()[:40]
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
        scope=descriptor.scope,
        goal_contract_hash=identity.goal_contract_hash,
        interaction_id=frame.identity.interaction_id,
        provider_id=judgment.backend_id,
        model_id=judgment.model_id,
        model_revision=judgment.model_revision,
        model_capability_hash=judgment.provider_protocol_hash,
        payload=payload,
    )
    return EventFactory.from_dict(event.to_dict()).to_dict()


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
               authority: str, evidence_ref: str) -> str:
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
                    and prior.get("system_one_outcome_evidence_ref") == evidence_ref):
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
                "system_one_independent_outcome": True,
            },
        )
        validated = EventFactory.from_dict(event.to_dict())
        self._sink.emit_authenticated(validated, authenticated=True)
        return event_id


__all__ = ["SystemOneOutcomeLabeler", "prepare_system_one_decision_event"]
