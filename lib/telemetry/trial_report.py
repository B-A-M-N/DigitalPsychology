"""Rebuild host-only trial qualification records from persisted traces."""
from __future__ import annotations

from typing import Any, Mapping

from ..control_protocol import (PROTOCOL_VERSION, ControlSessionDescriptor,
                                RecordSystemOneOutcomeRequest)


class TrialReportMethods:
    def record_system_one_outcome(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        """Persist an independent label through the private host control plane."""
        request = RecordSystemOneOutcomeRequest.from_mapping(raw)
        stored = self.state_store.control_session(request.control_session_id)
        if stored is None:
            raise RuntimeError("outcome request references an unknown control session")
        descriptor = ControlSessionDescriptor.from_mapping(stored["descriptor"])
        if descriptor.control_session_id != request.control_session_id:
            raise RuntimeError("persisted control session identity is inconsistent")
        recorder = getattr(self.decision_service, "record_system_one_outcome", None)
        if not callable(recorder):
            raise RuntimeError("independent System-1 outcome recording is unavailable")
        outcome_event_id = recorder(
            decision_event_id=request.decision_event_id,
            namespace_id=descriptor.identity.namespace_id,
            application_instance_id=descriptor.identity.application_instance_id,
            question_id=request.question_id, label=request.label,
            authority=request.authority, evidence_ref=request.evidence_ref,
            control_session_id=request.control_session_id)
        return {"protocol_version": PROTOCOL_VERSION,
                "control_session_id": request.control_session_id,
                "outcome_event_id": outcome_event_id}

    def behavioral_trial_record(self, control_session_id: str, *, trial_id: str,
                                task_outcome_score: float | None = None,
                                task_completed: bool | None = None) -> dict[str, Any]:
        """Return persisted action metrics plus separately stored control transitions."""
        from ..telemetry.behavioral_trace import (
            ActionTrace, BehavioralTrace, provider_status,
            runtime_configuration,
        )

        stored = self.state_store.control_session(control_session_id)
        if stored is None:
            raise RuntimeError("control session has no persisted descriptor")
        descriptor = ControlSessionDescriptor.from_mapping(stored["descriptor"])
        if descriptor.control_session_id != control_session_id:
            raise RuntimeError("persisted control session identity is inconsistent")
        provider_identity = stored.get("provider_identity")
        attestation = (provider_identity.get("attestation")
                       if isinstance(provider_identity, dict) else None)
        factory_configured = (bool(provider_identity.get("factory_configured"))
                              if isinstance(provider_identity, dict) else False)
        execution_identity = (provider_identity.get("execution_identity")
                              if isinstance(provider_identity, dict) else None)
        trace = BehavioralTrace(
            task_id=descriptor.identity.task_id,
            configuration=runtime_configuration(
                cfw_connected=bool(descriptor.available_frameworks),
                csw_connected=bool(descriptor.host_capabilities.get("csw_connected")),
                attestation=attestation),
            semantic_provider_status=provider_status(
                attestation,
                factory_configured=factory_configured),
            semantic_model_hash=(attestation.get("model_content_hash")
                                 if attestation else None),
            semantic_provider_attestation=attestation,
        )
        for row in self.state_store.behavioral_action_traces(control_session_id):
            trace.record(ActionTrace.from_dict(row))
        transitions = self.state_store.committed_control_transitions(control_session_id)
        for transition in transitions:
            trace.record_control_transition(transition)
        record = trace.to_dict(trial_id=trial_id,
                               task_outcome_score=task_outcome_score,
                               task_completed=task_completed)
        record["protocol_version"] = PROTOCOL_VERSION
        record["execution_identity"] = (
            dict(execution_identity) if isinstance(execution_identity, dict) else None)
        record["cfw_preflight_rejections"] = sum(
            1 for item in transitions
            if item.get("cfw_rejection_stage") == "cfw_preflight")
        record["cfw_final_gate_rejections"] = sum(
            1 for item in transitions
            if item.get("cfw_rejection_stage") == "cfw_final_gate")
        record["goal_drift_detections"] = sum(
            1 for item in transitions if item.get("goal_drift_detected"))
        record["goal_drift_interventions"] = sum(
            1 for item in transitions
            if item.get("goal_drift_intervention_applied"))
        record["semantic_queries"] = sum(
            1 for item in transitions if item.get("semantic_query_attempted"))
        record["semantic_results"] = sum(
            1 for item in transitions if item.get("semantic_judgment_returned"))
        record["semantic_uses"] = sum(
            1 for item in transitions if item.get("semantic_judgment_used"))
        record["semantic_failures"] = sum(
            1 for item in transitions if item.get("semantic_query_failed"))
        from ..telemetry.production_metrics import QUESTION_FAMILIES
        record["semantic_influenced_decisions"] = sum(
            bool(item.get("semantic_influenced_decision")) for item in transitions)
        record["semantic_candidate_changes"] = sum(
            bool(item.get("semantic_candidate_change")) for item in transitions)
        record["semantic_mode_changes"] = sum(
            bool(item.get("semantic_mode_change")) for item in transitions)
        record["semantic_framework_changes"] = sum(
            int(item.get("semantic_framework_changes", 0) or 0) for item in transitions)
        record["semantic_statework_changes"] = sum(
            bool(item.get("semantic_statework_change")) for item in transitions)
        record["semantic_influenced_interventions"] = sum(
            int(item.get("semantic_influenced_interventions", 0) or 0)
            for item in transitions)
        record["semantic_effective_interventions"] = sum(
            int(item.get("semantic_effective_interventions", 0) or 0)
            for item in transitions)
        record["semantic_ineffective_interventions"] = sum(
            int(item.get("semantic_ineffective_interventions", 0) or 0)
            for item in transitions)
        by_family: dict[str, dict[str, int]] = {}
        family_fields = {
            "queries": "semantic_query_attempted",
            "results": "semantic_judgment_returned",
            "influenced_decisions": "semantic_influenced_decision",
            "candidate_changes": "semantic_candidate_change",
            "mode_changes": "semantic_mode_change",
            "statework_changes": "semantic_statework_change",
        }
        for family in sorted(QUESTION_FAMILIES):
            relevant = [item for item in transitions
                        if family in item.get("semantic_question_ids", ())]
            metrics = {name: sum(bool(item.get(field)) for item in relevant)
                       for name, field in family_fields.items()}
            metrics["framework_changes"] = sum(
                int(item.get("semantic_framework_changes", 0) or 0)
                for item in relevant)
            metrics["influenced_interventions"] = sum(
                int(item.get("semantic_influenced_interventions", 0) or 0)
                for item in relevant)
            by_family[family] = metrics
        record["semantic_by_question_family"] = by_family
        pre_model_call = [item for item in transitions
                          if item.get("boundary_kind") == "pre_model_call"]
        model_digests = [str(item["model_projection_digest"])
                         for item in pre_model_call
                         if item.get("model_projection_digest")]
        model_updates = sum(1 for before, after in zip(model_digests, model_digests[1:])
                           if before != after)
        record["model_projection_events"] = len(pre_model_call)
        record["model_projection_updates"] = model_updates
        record["unique_model_projection_digests"] = len(set(model_digests))
        record["projection_updates"] = model_updates
        record["unique_projection_digests"] = len(set(model_digests))
        record["control_transition_count"] = len(transitions)
        return record
