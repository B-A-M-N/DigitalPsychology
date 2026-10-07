"""Construct and persist host-authenticated intervention events."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Mapping

from ..behavior_state import BehaviorDirective, BehaviorIntervention, InterventionOutcome
from ..feedback_loop import Event, EventFactory, SQLiteSink
from ..outcomes import InterventionEventContext


class InterventionEventService:
    """Own intervention receipts and their trusted event-store boundary."""

    def __init__(self, sink: SQLiteSink) -> None:
        self._sink = sink

    def record_application(self, intervention: BehaviorIntervention,
                           directives: tuple[BehaviorDirective, ...],
                           applied_directive_ids: tuple[str, ...], *,
                           event_context: InterventionEventContext) -> str:
        event = self.prepare_application(
            intervention, directives, applied_directive_ids,
            event_context=event_context)
        return self.dispatch(event)

    def prepare_application(self, intervention: BehaviorIntervention,
                            directives: tuple[BehaviorDirective, ...],
                            applied_directive_ids: tuple[str, ...], *,
                            event_context: InterventionEventContext) -> dict[str, Any]:
        required = {item.directive_id for item in directives
                    if item.intervention_id == intervention.intervention_id}
        applied = set(applied_directive_ids)
        if not required or required != applied or len(applied) != len(applied_directive_ids):
            raise ValueError("applied intervention receipt does not cover its exact directives")
        payload = {
            "behavior_intervention_id": intervention.intervention_id,
            "behavior_intervention_cause": intervention.cause,
            "behavior_intervention_expected_effect": intervention.expected_effect,
            "behavior_intervention_status": "applied",
            "behavior_intervention_attribution": "insufficient",
            "behavior_intervention_target_changes": [target.to_dict()
                                                       for target in intervention.targets],
            "behavior_intervention_target_criteria": list(intervention.target_criteria),
            "behavior_intervention_start_revision": intervention.start_revision,
            "behavior_intervention_baseline_action_index": intervention.baseline_action_index,
            "behavior_intervention_evaluation_horizon_actions":
                intervention.evaluation_horizon_actions,
            "behavior_intervention_competing_ids":
                list(intervention.concurrent_intervention_ids),
            "behavior_intervention_directive_ids": sorted(applied),
            "behavior_condition_id": intervention.condition_id,
            "behavior_condition_basis_digest": intervention.condition_basis_digest,
            "behavior_recovery_epoch_id": intervention.recovery_epoch_id,
            "semantic_decision_refs": list(intervention.semantic_decision_refs),
            "semantic_influence_receipt_id": (
                intervention.semantic_influence_receipt_id),
        }
        return self._build_event(
            event_context, category="decision", event_type="behavior_intervention_applied",
            scope=intervention.scope, evidence_refs=(), payload=payload).to_dict()

    def record_outcome(self, outcome: InterventionOutcome, *,
                       event_context: InterventionEventContext) -> str:
        return self.dispatch(self.prepare_outcome(outcome, event_context=event_context))

    def prepare_outcome(self, outcome: InterventionOutcome, *,
                        event_context: InterventionEventContext) -> dict[str, Any]:
        payload = {
            "behavior_intervention_id": outcome.intervention_id,
            "behavior_intervention_cause": outcome.cause,
            "behavior_intervention_status": outcome.outcome,
            "behavior_intervention_attribution": outcome.attribution,
            "behavior_intervention_criterion_progress": list(outcome.criterion_ids_progressed),
            "behavior_intervention_target_criteria": list(outcome.target_criteria),
            "behavior_intervention_task_set_outcome": (
                outcome.task_set_outcome.to_dict()
                if outcome.task_set_outcome is not None else None),
            "behavior_intervention_goal_progress": outcome.goal_progress,
            "behavior_intervention_comparison_id": outcome.comparison_id,
            "behavior_intervention_preventive_utility": list(
                outcome.preventive_utility),
            "behavior_intervention_artifact_changed": outcome.artifact_changed,
            "behavior_intervention_repeated_approach": outcome.repeated_approach,
            "behavior_intervention_verification_quality": outcome.verification_quality,
            "behavior_condition_id": outcome.condition_id,
            "behavior_condition_basis_digest": outcome.condition_basis_digest,
            "semantic_decision_refs": list(outcome.semantic_decision_refs),
            "semantic_influence_receipt_id": (
                outcome.semantic_influence_receipt_id),
        }
        return self._build_event(
            event_context, category="validation", event_type="behavior_intervention_assessed",
            scope=outcome.scope, evidence_refs=outcome.evidence_refs,
            payload=payload).to_dict()

    def dispatch(self, value: Mapping[str, Any]) -> str:
        if not isinstance(value, Mapping):
            raise ValueError("runtime outbox event must be an object")
        event = EventFactory.from_dict(dict(value))
        allowed = {
            ("decision", "behavior_intervention_applied",
             "digitalpsychology_runtime", "digitalpsychology_runtime"),
            ("decision", "system_one_decision",
             "digitalpsychology_runtime", "digitalpsychology_system_one"),
            ("validation", "behavior_intervention_assessed",
             "digitalpsychology_runtime", "host_outcome_evaluator"),
        }
        identity = (event.category, event.event_type, event.actor_id,
                    event.authority_source)
        if identity not in allowed:
            raise ValueError("runtime outbox event is outside the trusted DP host contract")
        self._sink.emit_authenticated(event, authenticated=True)
        return event.event_id

    def record_event(self, event_context: InterventionEventContext, *, category: str,
                     event_type: str, scope: str, evidence_refs: tuple[str, ...],
                     payload: Mapping[str, Any]) -> str:
        return self.dispatch(self._build_event(
            event_context, category=category, event_type=event_type,
            scope=scope, evidence_refs=evidence_refs, payload=payload).to_dict())

    @staticmethod
    def _build_event(event_context: InterventionEventContext, *, category: str,
                     event_type: str, scope: str, evidence_refs: tuple[str, ...],
                     payload: Mapping[str, Any]) -> Event:
        event_id = f"dp-intv-{uuid.uuid4().hex}"
        return Event(
            event_id=event_id,
            timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            namespace_id=event_context.namespace_id,
            application_id=event_context.application_id,
            application_instance_id=event_context.application_instance_id,
            agent_instance_id=event_context.agent_instance_id,
            session_id=event_context.session_id,
            task_id=event_context.task_id,
            attempt_id=event_context.attempt_id,
            segment_id=event_context.segment_id,
            agent_id=event_context.agent_id,
            behavioral_subject=event_context.behavioral_subject,
            category=category,
            event_type=event_type,
            actor_id="digitalpsychology_runtime",
            authority_source="host_outcome_evaluator" if category == "validation"
                             else "digitalpsychology_runtime",
            scope=scope,
            goal_contract_hash=event_context.goal_contract_hash,
            evidence_refs=list(evidence_refs),
            payload=dict(payload),
        )
