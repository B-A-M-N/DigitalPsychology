"""Rebuild host-only trial qualification records from persisted traces."""
from __future__ import annotations

import math
import statistics
from typing import Any, Mapping, Sequence

from ..control_protocol import (PROTOCOL_VERSION, ControlSessionDescriptor,
                                RecordSystemOneOutcomeRequest)


def aggregate_semantic_transitions(
        transitions: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate durable session transitions by the question that caused them.

    Intervention outcomes join through their original semantic influence
    receipt. This keeps a later outcome attached to the question that created
    the intervention rather than the question, if any, asked at resolution.
    """
    from .production_metrics import QUESTION_FAMILIES

    counter_fields = (
        "query_opportunities", "query_attempts", "valid_results",
        "advisory_results", "query_failures", "influenced_decisions",
        "candidate_changes", "mode_changes", "framework_changes",
        "statework_changes",
    )
    by_family: dict[str, dict[str, Any]] = {
        family: {**{field: 0 for field in counter_fields},
                 "latency_observations": 0, "mean_latency_ms": None,
                 "median_latency_ms": None,
                 "influenced_interventions": 0, "effective": 0,
                 "ineffective": 0, "unresolved": 0,
                 "_latencies": [], "_interventions": set(), "_outcomes": {}}
        for family in QUESTION_FAMILIES
    }
    receipt_families: dict[str, tuple[str, ...]] = {}
    global_interventions: set[tuple[str, str]] = set()
    global_outcomes: dict[tuple[str, str], tuple[int, str]] = {}
    global_latencies: list[float] = []
    instrumented_boundaries = sum(
        1 for item in transitions if "semantic_query_opportunity" in item)

    def family_names(values: Any) -> tuple[str, ...]:
        if not isinstance(values, (tuple, list)):
            return ()
        return tuple(dict.fromkeys(item for item in values
                                   if isinstance(item, str)
                                   and item in by_family))

    for transition in transitions:
        opportunity_id = transition.get("semantic_query_opportunity_question_id")
        question_ids = family_names(transition.get("semantic_question_ids"))
        if (isinstance(opportunity_id, str) and opportunity_id in by_family
                and bool(transition.get("semantic_query_opportunity"))):
            by_family[opportunity_id]["query_opportunities"] += 1
        for family in question_ids:
            metrics = by_family[family]
            metrics["query_attempts"] += int(
                transition.get("semantic_query_attempted") is True)
            metrics["valid_results"] += int(
                transition.get("semantic_judgment_returned") is True)
            metrics["advisory_results"] += int(
                transition.get("semantic_judgment_used") is True)
            metrics["query_failures"] += int(
                transition.get("semantic_query_failed") is True)
            metrics["influenced_decisions"] += int(
                transition.get("semantic_influenced_decision") is True)
            metrics["candidate_changes"] += int(
                transition.get("semantic_candidate_change") is True)
            metrics["mode_changes"] += int(
                transition.get("semantic_mode_change") is True)
            metrics["framework_changes"] += int(
                transition.get("semantic_framework_changes", 0) or 0)
            metrics["statework_changes"] += int(
                transition.get("semantic_statework_change") is True)
            latency = transition.get("semantic_latency_ms")
            if (transition.get("semantic_query_attempted") is True
                    and isinstance(latency, (int, float))
                    and not isinstance(latency, bool)
                    and math.isfinite(float(latency)) and latency >= 0):
                metrics["_latencies"].append(float(latency))
        latency = transition.get("semantic_latency_ms")
        if (transition.get("semantic_query_attempted") is True
                and isinstance(latency, (int, float))
                and not isinstance(latency, bool)
                and math.isfinite(float(latency)) and latency >= 0):
            global_latencies.append(float(latency))

        receipt_id = transition.get("semantic_influence_receipt_id")
        if (isinstance(receipt_id, str) and receipt_id
                and transition.get("semantic_influenced_decision") is True
                and question_ids):
            receipt_families[receipt_id] = question_ids
        if isinstance(receipt_id, str) and receipt_id:
            intervention_ids = transition.get("semantic_influenced_intervention_ids", ())
            if isinstance(intervention_ids, (tuple, list)):
                for intervention_id in intervention_ids:
                    if not isinstance(intervention_id, str) or not intervention_id:
                        continue
                    key = (receipt_id, intervention_id)
                    global_interventions.add(key)
                    for family in receipt_families.get(receipt_id, ()):
                        by_family[family]["_interventions"].add(key)

        outcome_links = transition.get("semantic_intervention_outcomes", ())
        if not isinstance(outcome_links, (tuple, list)):
            continue
        for link in outcome_links:
            if not isinstance(link, Mapping):
                continue
            linked_receipt = link.get("influence_receipt_id")
            intervention_id = link.get("intervention_id")
            outcome = link.get("outcome")
            revision = link.get("observed_revision")
            if (not isinstance(linked_receipt, str) or not linked_receipt
                    or not isinstance(intervention_id, str) or not intervention_id
                    or not isinstance(outcome, str) or type(revision) is not int):
                continue
            key = (linked_receipt, intervention_id)
            prior = global_outcomes.get(key)
            if prior is None or revision >= prior[0]:
                global_outcomes[key] = (revision, outcome)
            for family in receipt_families.get(linked_receipt, ()):
                prior_family = by_family[family]["_outcomes"].get(key)
                if prior_family is None or revision >= prior_family[0]:
                    by_family[family]["_outcomes"][key] = (revision, outcome)

    def outcome_counts(outcomes: Mapping[Any, tuple[int, str]]) -> tuple[int, int]:
        effective = sum(outcome == "effective" for _revision, outcome in outcomes.values())
        ineffective = sum(outcome in {"ineffective", "regressed"}
                          for _revision, outcome in outcomes.values())
        return effective, ineffective

    global_effective, global_ineffective = outcome_counts(global_outcomes)
    for family, metrics in by_family.items():
        outcomes = metrics.pop("_outcomes")
        interventions = metrics.pop("_interventions")
        metrics["influenced_interventions"] = len(interventions)
        metrics["effective"], metrics["ineffective"] = outcome_counts(outcomes)
        metrics["unresolved"] = max(
            0, len(interventions) - metrics["effective"] - metrics["ineffective"])
        latencies = metrics.pop("_latencies")
        metrics["latency_observations"] = len(latencies)
        metrics["mean_latency_ms"] = (round(statistics.fmean(latencies), 3)
                                      if latencies else None)
        metrics["median_latency_ms"] = (round(statistics.median(latencies), 3)
                                        if latencies else None)
        metrics["instrumented_boundaries"] = instrumented_boundaries
        metrics["status"] = "observed" if instrumented_boundaries else "not_observed"

    return {
        "instrumented_boundaries": instrumented_boundaries,
        "query_opportunities": sum(
            int(item.get("semantic_query_opportunity") is True)
            for item in transitions),
        "query_attempts": sum(
            int(item.get("semantic_query_attempted") is True)
            for item in transitions),
        "valid_results": sum(
            int(item.get("semantic_judgment_returned") is True)
            for item in transitions),
        "advisory_results": sum(
            int(item.get("semantic_judgment_used") is True)
            for item in transitions),
        "query_failures": sum(
            int(item.get("semantic_query_failed") is True)
            for item in transitions),
        "influenced_decisions": sum(
            int(item.get("semantic_influenced_decision") is True)
            for item in transitions),
        "candidate_changes": sum(
            int(item.get("semantic_candidate_change") is True)
            for item in transitions),
        "mode_changes": sum(
            int(item.get("semantic_mode_change") is True)
            for item in transitions),
        "framework_changes": sum(
            int(item.get("semantic_framework_changes", 0) or 0)
            for item in transitions),
        "statework_changes": sum(
            int(item.get("semantic_statework_change") is True)
            for item in transitions),
        "query_latency_observations": len(global_latencies),
        "query_latency_mean_ms": (round(statistics.fmean(global_latencies), 3)
                                  if global_latencies else None),
        "query_latency_median_ms": (round(statistics.median(global_latencies), 3)
                                    if global_latencies else None),
        "influenced_interventions": len(global_interventions),
        "effective": global_effective,
        "ineffective": global_ineffective,
        "unresolved": max(
            0, len(global_interventions) - global_effective - global_ineffective),
        "by_question_family": by_family,
    }


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
        semantic_metrics = aggregate_semantic_transitions(transitions)
        record["semantic_query_opportunities"] = semantic_metrics["query_opportunities"]
        record["semantic_queries"] = semantic_metrics["query_attempts"]
        record["semantic_results"] = semantic_metrics["valid_results"]
        record["semantic_uses"] = semantic_metrics["advisory_results"]
        record["semantic_failures"] = semantic_metrics["query_failures"]
        record["semantic_influenced_decisions"] = semantic_metrics[
            "influenced_decisions"]
        record["semantic_candidate_changes"] = semantic_metrics["candidate_changes"]
        record["semantic_mode_changes"] = semantic_metrics["mode_changes"]
        record["semantic_framework_changes"] = semantic_metrics[
            "framework_changes"]
        record["semantic_statework_changes"] = semantic_metrics["statework_changes"]
        record["semantic_influenced_interventions"] = semantic_metrics[
            "influenced_interventions"]
        record["semantic_effective_interventions"] = semantic_metrics["effective"]
        record["semantic_ineffective_interventions"] = semantic_metrics[
            "ineffective"]
        record["semantic_unresolved_interventions"] = semantic_metrics["unresolved"]
        record["semantic_query_latency_mean_ms"] = semantic_metrics[
            "query_latency_mean_ms"]
        record["semantic_query_latency_median_ms"] = semantic_metrics[
            "query_latency_median_ms"]
        record["semantic_by_question_family"] = semantic_metrics["by_question_family"]
        record["semantic_causal_metrics"] = semantic_metrics
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
        delivery_counts = self.state_store.outbox_delivery_status(control_session_id)
        authenticated_type_counts = None
        authenticate = getattr(
            self.decision_service, "authenticated_event_type_counts", None)
        if callable(authenticate):
            try:
                authenticated_type_counts = authenticate(
                    scope=control_session_id,
                    task_id=descriptor.identity.task_id,
                    namespace_id=descriptor.identity.namespace_id,
                    application_instance_id=(
                        descriptor.identity.application_instance_id),
                    event_types=tuple(delivery_counts["by_event_type"]))
            except Exception as exc:
                record["runtime_event_delivery_authentication_error_class"] = (
                    type(exc).__name__)
        delivery = self.state_store.outbox_delivery_status(
            control_session_id,
            authenticated_event_type_counts=authenticated_type_counts)
        event_db = getattr(self.decision_service, "event_db", None)
        if event_db is not None:
            from pathlib import Path
            delivery["authenticated_event_store_path"] = str(
                Path(event_db).expanduser().resolve())
        record["runtime_event_delivery"] = delivery
        return record
