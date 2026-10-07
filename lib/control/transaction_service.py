"""Prepare, commit, abort, and reconcile DP control transactions."""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import replace
from functools import wraps
import time
from typing import Any, Mapping, Sequence

from ..behavior_state import (BehaviorDirective, BehaviorState, BehavioralDecision,
                              CSWBehaviorSnapshot, InterventionOutcome,
                              group_behavioral_causes)
from ..outcomes import InterventionEventContext
from ..runtime.projection import build_behavior_projection
from ..runtime.cfw_behavior_contract import validate_state_against_cfw_contract
from ..control_protocol import (PROTOCOL_VERSION, ControlAbortRequest,
                                ControlCommitRequest, ControlSessionDescriptor,
                                ControlTickRequest, bounded_control_mapping,
                                ACTION_INDEX_BOUNDARIES,
                                CANDIDATE_ACTION_BOUNDARIES,
                                parse_csw_snapshot, parse_goal_set)
from ..telemetry.trace_builder import (build_committed_action_trace,
                                       build_control_transition_trace)
from ..telemetry.production_metrics import QUESTION_FAMILIES
from .authority_receipts import validate_applied_authorities

_LOG = logging.getLogger(__name__)


def _record_metric(self: Any, operation: str, *args: Any) -> None:
    metrics = getattr(self, "production_metrics", None)
    method = getattr(metrics, operation, None)
    if callable(method):
        try:
            method(*args)
        except Exception:
            _LOG.warning("could not persist a production metric", exc_info=True)


def _record_laya_event(self: Any, event: str, *, amount: int = 1,
                       question_families: Sequence[str] = ()) -> None:
    metrics = getattr(self, "production_metrics", None)
    method = getattr(metrics, "record_laya_event", None)
    if callable(method):
        try:
            method(event, amount=amount)
            family_method = getattr(metrics, "record_laya_family_event", None)
            if callable(family_method):
                for family in sorted(set(question_families) & set(QUESTION_FAMILIES)):
                    family_method(event, question_family=family, amount=amount)
        except Exception:
            _LOG.warning("could not persist a Laya production metric", exc_info=True)


def _record_laya_preparation_metrics(self: Any, preparation: Any,
                                     prior_state: BehaviorState) -> None:
    """Record bounded provider, causal-influence, and intervention counters."""
    frame = dict(preparation.system_one_frame or {})
    families = tuple(sorted({
        str(item.get("question_id"))
        for item in frame.get("questions", ())
        if isinstance(item, Mapping) and item.get("question_id") in QUESTION_FAMILIES
    }))
    if preparation.system_one_query_attempted:
        _record_laya_event(self, "opportunity", question_families=families)
        _record_laya_event(self, "attempt", question_families=families)
        result = dict(preparation.system_one_result or {})
        status = result.get("status")
        if status in {"ok", "shadow"}:
            _record_laya_event(self, "valid", question_families=families)
        if status == "shadow":
            _record_laya_event(self, "shadow", question_families=families)
        elif status in {"unavailable", "rejected", "stale"}:
            _record_laya_event(self, "failure", question_families=families)
        reason = str(result.get("reason") or "").lower()
        if "timeout" in reason or "deadline" in reason:
            _record_laya_event(self, "timeout", question_families=families)
        if result.get("cache_hit") is True:
            _record_laya_event(self, "cache_hit", question_families=families)
        latency = result.get("latency_ms")
        if isinstance(latency, (int, float)) and not isinstance(latency, bool):
            _record_metric(self, "observe_duration", "laya_query_latency_ms",
                           float(latency))
        if preparation.semantic_judgment_used:
            _record_laya_event(self, "advisory", question_families=families)
        else:
            _record_metric(self, "increment", "laya_fallbacks")

    receipt = preparation.semantic_influence_receipt
    influence_families = (
        tuple(item for item in receipt.question_ids if item in QUESTION_FAMILIES)
        if receipt is not None else ())
    if receipt is not None and receipt.influenced:
        _record_laya_event(self, "influenced_decision",
                           question_families=influence_families)
        if receipt.changed_selection:
            _record_laya_event(self, "candidate_change",
                               question_families=influence_families)
        if receipt.changed_mode:
            _record_laya_event(self, "mode_change",
                               question_families=influence_families)
        if receipt.changed_frameworks:
            _record_laya_event(self, "framework_change",
                               amount=len(receipt.changed_frameworks),
                               question_families=influence_families)
        if receipt.changed_statework_preference:
            _record_laya_event(self, "statework_change",
                               question_families=influence_families)


def _record_laya_commit_metrics(self: Any, basis: Mapping[str, Any],
                                prior_state: BehaviorState,
                                decision: BehavioralDecision) -> None:
    """Count influenced interventions and their outcomes only after commit."""
    raw_receipt = basis.get("semantic_influence_receipt")
    if not isinstance(raw_receipt, Mapping):
        return
    receipt = dict(raw_receipt)
    receipt_id = receipt.get("receipt_id")
    if not isinstance(receipt_id, str) or not receipt_id:
        return
    families = tuple(
        item for item in receipt.get("question_ids", ())
        if isinstance(item, str) and item in QUESTION_FAMILIES)
    influenced = any(receipt.get(name) is True for name in (
        "changed_selection", "changed_mode", "changed_statework_preference"))
    influenced = influenced or bool(receipt.get("changed_frameworks"))
    if not influenced:
        return
    old_ids = {item.intervention_id for item in prior_state.active_interventions}
    new_influenced = tuple(
        item for item in decision.state.active_interventions
        if item.intervention_id not in old_ids
        and item.semantic_influence_receipt_id == receipt_id)
    if new_influenced:
        _record_laya_event(self, "intervention_created", amount=len(new_influenced),
                           question_families=families)
    for outcome in decision.intervention_outcomes:
        if outcome.semantic_influence_receipt_id != receipt_id:
            continue
        if outcome.outcome == "effective":
            _record_laya_event(self, "intervention_effective",
                               question_families=families)
        elif outcome.outcome in {"ineffective", "regressed"}:
            _record_laya_event(self, "intervention_ineffective",
                               question_families=families)


def _measure(metric_name: str):
    def decorate(function):
        @wraps(function)
        def measured(self, *args, **kwargs):
            started = time.perf_counter()
            try:
                return function(self, *args, **kwargs)
            finally:
                _record_metric(
                    self, "observe_duration", metric_name,
                    max(0.0, (time.perf_counter() - started) * 1000.0))
        return measured
    return decorate


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _decision_input_digest(descriptor: ControlSessionDescriptor,
                           request: ControlTickRequest, state: BehaviorState,
                           provider_identity: Mapping[str, Any], *,
                           cfw_lifecycle_revision: int) -> str:
    """Hash stable policy inputs while omitting transport-only tick sequencing."""
    normalized_request = request.to_dict()
    normalized_request.pop("tick_id", None)
    normalized_request.pop("boundary_sequence", None)
    normalized_request["expected_behavior_revision"] = state.revision
    normalized_request["cfw_lifecycle_revision"] = cfw_lifecycle_revision
    pressure = float(request.observation.context_pressure)
    return _digest({
        "version": 1,
        "behavior_revision": state.revision,
        "behavior_state": state.to_dict(),
        "goal_set_revision": request.goal_set.revision,
        "goal_set": request.goal_set.to_dict(),
        "cfw_signal_cursor": request.cfw_signal_cursor,
        "csw_snapshot_id": request.csw_snapshot.snapshot_id,
        "context_pressure_band": "high" if pressure >= 0.85 else "normal",
        "context_pressure": pressure,
        "current_attention_lease": (
            state.attention_lease.to_dict()
            if state.attention_lease is not None else None),
        "active_trigger_codes": list(request.observation.active_trigger_codes),
        "boundary_kind": request.boundary_kind,
        "semantic_policy_identity": {
            "descriptor": descriptor.to_dict(),
            "provider_identity": dict(provider_identity),
        },
        "request": normalized_request,
    })


def _lease_or_evaluation_due(state: BehaviorState, action_index: int) -> bool:
    attention = state.attention_lease
    if (attention is not None
            and action_index - attention.start_action >= attention.max_actions):
        return True
    mode = state.mode_lease
    if (mode is not None and mode.max_actions is not None
            and action_index - mode.entered_action >= mode.max_actions):
        return True
    for intervention in state.active_interventions:
        horizon = (intervention.evaluation_horizon_actions
                   or intervention.expires_after_actions)
        if (intervention.actions_elapsed >= horizon
                or action_index - intervention.baseline_action_index >= horizon
                or action_index > intervention.expires_at_action):
            return True
    return False


def _state_changes_only_by_revision(before: BehaviorState,
                                    after: BehaviorState) -> bool:
    before_values = before.to_dict()
    after_values = after.to_dict()
    before_values.pop("revision", None)
    after_values.pop("revision", None)
    return before_values == after_values


def _decision_from_mapping(value: Mapping[str, Any], *,
                           basis: Mapping[str, Any]) -> BehavioralDecision:
    data = dict(value)
    return BehavioralDecision(
        decision_id=str(data["decision_id"]), scope=str(data["scope"]),
        expected_revision=int(data["expected_revision"]),
        state=BehaviorState.from_mapping(data["state"]),
        directives=tuple(BehaviorDirective.from_legacy_mapping(
            item, host_action_index=int(basis["host_action_index"]),
            boundary_kind=str(basis["boundary_kind"]))
                         for item in data.get("directives") or ()),
        preferred_stateworks=tuple(data.get("preferred_stateworks") or ()),
        intervention_outcomes=tuple(InterventionOutcome.from_mapping(item)
                                    for item in data.get("intervention_outcomes") or ()),
        reason=str(data.get("reason") or "no behavioral change required"),
        selected_candidate_id=data.get("selected_candidate_id"),
    )


def _projection_capabilities(descriptor: ControlSessionDescriptor) -> dict[str, bool]:
    """Return the trusted authority flags shared by tick and recovery projections."""
    policy_context = descriptor.host_capabilities.get("policy_context") or {}
    return {
        "cfw_connected": bool(descriptor.available_frameworks),
        "csw_connected": bool(descriptor.csw_authority_identity),
        "outcome_evidence_connected": bool(
            descriptor.host_capabilities.get("outcome_evidence_connected")),
        "host_has_stable_objective_context": bool(
            policy_context.get("host_has_stable_objective_context")),
    }



class ControlTransactionMethods:
    @_measure("control_tick_prepare_duration_ms")
    def prepare_control_tick(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        request = ControlTickRequest.from_mapping(raw)
        descriptor = self._descriptor(request.control_session_id)
        scope = request.control_session_id
        record = self.state_store.load(scope)
        if self.state_store.control_reconciliation_required(scope):
            raise RuntimeError(
                "control session requires reconciliation before preparing a new tick")
        validate_state_against_cfw_contract(
            record.state, descriptor, label="persisted DP behavior state")
        digest = _digest(request.to_dict())
        existing = self.state_store.control_tick(scope, request.tick_id)
        if existing is not None:
            if existing["request_digest"] != digest:
                raise RuntimeError("control tick ID was reused with different input")
            return self._prepared_response(scope, request.tick_id, existing)
        if request.expected_behavior_revision != record.state.revision:
            raise RuntimeError("control tick behavior revision is stale")

        preparation = self.engine.prepare(descriptor, record, request)
        _record_laya_preparation_metrics(self, preparation, record.state)
        if preparation.starvation_prevented:
            _record_metric(self, "increment", "starvation_prevention_events",
                           len(preparation.starvation_prevented))
        decision = preparation.decision
        # Engine decisions describe a policy outcome and can repeat when a
        # transaction is retried at a later boundary. Persisted decisions are
        # transaction identities, so bind the ID to this tick to keep distinct
        # ticks independently addressable while same-tick retries stay stable.
        decision = replace(
            decision,
            decision_id="decision-" + _digest({
                "scope": scope,
                "tick_id": request.tick_id,
                "policy_decision_id": decision.decision_id,
            })[:24],
        )
        boundary_kind = str(request.boundary_kind)
        session = self.state_store.control_session(scope)
        provider_identity = (session.get("provider_identity")
                             if isinstance(session, Mapping) else None)
        if isinstance(provider_identity, Mapping):
            is_non_action = (
                boundary_kind != "post_action"
                and boundary_kind not in ACTION_INDEX_BOUNDARIES
                and boundary_kind not in CANDIDATE_ACTION_BOUNDARIES
                and not request.observation.action_completed
                and request.observation.candidate_action is None)
            has_outcome_input = bool(
                request.outcome_facts or request.observation.evidence_refs
                or request.observation.progress_made
                or request.observation.regression_observed
                or request.observation.artifact_changed)
            basis_matcher = getattr(
                self.state_store, "control_tick_input_basis_matches", None)
            can_consider_unchanged = (
                is_non_action and callable(basis_matcher)
                and not request.cfw_signals and not has_outcome_input
                and not preparation.system_one_query_attempted
                and not preparation.starvation_prevented
                and not decision.directives and not decision.preferred_stateworks
                and not decision.intervention_outcomes
                and not _lease_or_evaluation_due(
                    record.state, request.observation.action_index)
                and not _lease_or_evaluation_due(
                    decision.state, request.observation.action_index)
                and _state_changes_only_by_revision(record.state, decision.state))
            if can_consider_unchanged:
                current_input_digest = _decision_input_digest(
                    descriptor, request, record.state, provider_identity,
                    cfw_lifecycle_revision=request.cfw_lifecycle_revision)
                if basis_matcher(
                        scope, boundary_kind=boundary_kind,
                        expected_revision=record.state.revision,
                        expected_action_index=record.action_index,
                        boundary_sequence=request.boundary_sequence,
                        input_digest=current_input_digest):
                    return {
                        "protocol_version": PROTOCOL_VERSION,
                        "control_session_id": scope,
                        "status": "unchanged",
                        "behavior_revision": record.state.revision,
                        "behavior_state": record.state.to_dict(),
                        "boundary_kind": boundary_kind,
                        "decision_input_digest": current_input_digest,
                    }

        commit_action_index = (request.observation.action_index
                               if boundary_kind == "post_action" else record.action_index)
        model_projection = build_behavior_projection(
            decision.state, request.csw_snapshot, objective=descriptor.objective,
            goal_set=request.goal_set, **_projection_capabilities(descriptor),
        )
        previous_basis = self.state_store.latest_control_tick_basis(scope)
        prior_projection_index = max(
            record.action_index,
            int(previous_basis.get("cfw_projection_action_index", record.action_index))
            if previous_basis else record.action_index,
        )
        cfw_projection_action_index = max(
            prior_projection_index, request.observation.action_index)
        previous_goal_set = (parse_goal_set(previous_basis["goal_set"])
                             if previous_basis and previous_basis.get("goal_set")
                             else descriptor.initial_goal_set)
        from ..telemetry.material_progress import material_progress_delta
        material_criteria = material_progress_delta(previous_goal_set, request.goal_set)
        basis = {
            "expected_behavior_revision": record.state.revision,
            "expected_action_index": record.action_index,
            "host_action_index": request.observation.action_index,
            "cfw_projection_action_index": cfw_projection_action_index,
            "commit_action_index": commit_action_index,
            "boundary_phase": boundary_kind,
            "boundary_kind": boundary_kind,
            "boundary_sequence": request.boundary_sequence,
            "goal_set_revision": request.goal_set.revision,
            "cfw_lifecycle_revision": request.cfw_lifecycle_revision,
            "cfw_signal_cursor": request.cfw_signal_cursor,
            "cfw_signal_causes": [cause.to_dict()
                                  for cause in group_behavioral_causes(
                                      request.cfw_signals)],
            "csw_selection_revision": request.csw_snapshot.revision,
            "csw_snapshot_id": request.csw_snapshot.snapshot_id,
            "csw_registry_hash": request.csw_snapshot.registry_hash,
            "csw_active_stateworks": list(request.csw_snapshot.active_stateworks),
            "csw_eligible_stateworks": [
                item.statework_id for item in request.csw_snapshot.eligible_stateworks],
            "csw_workflow_revisions": dict(request.csw_snapshot.workflow_revisions),
            "csw_dependency_retention": {
                key: list(value)
                for key, value in request.csw_snapshot.dependency_retention.items()},
            "goal_set": request.goal_set.to_dict(),
            "attention_fairness_bound_actions": preparation.fairness_bound_actions,
            "attention_starvation_prevented": list(preparation.starvation_prevented),
            "semantic_query_attempted": preparation.system_one_query_attempted,
            "semantic_judgment_returned": preparation.system_one_judgment_returned,
            "semantic_judgment_used": preparation.semantic_judgment_used,
            "semantic_query_failed": preparation.system_one_query_failed,
            "semantic_influence_receipt": (
                preparation.semantic_influence_receipt.to_dict()
                if preparation.semantic_influence_receipt is not None else None),
            "model_projection_digest": model_projection.model.semantic_digest,
            "behavioral_facts": {
                "active_trigger_codes": list(request.observation.active_trigger_codes),
                "equivalent_retry_count": request.observation.equivalent_retry_count,
                "candidate_preflight_result": request.observation.candidate_preflight_result,
                "candidate_rejection_stage": request.observation.candidate_rejection_stage,
                "candidate_gate_receipts": (
                    {stage: {**dict(receipt), "reason_codes": list(receipt["reason_codes"])}
                     for stage, receipt in request.observation.candidate_gate_receipts.items()}
                    if request.observation.candidate_gate_receipts is not None else None),
                "cfw_preflight_decision": request.observation.cfw_preflight_decision,
                "cfw_final_decision": request.observation.cfw_final_decision,
                "evidence_refs": list(request.observation.evidence_refs),
                "progress_made": request.observation.progress_made,
                "regression_observed": request.observation.regression_observed,
                "material_progress_criteria": list(material_criteria),
                "material_progress_observed": bool(material_criteria),
                "ward_hard_safety": any(
                    signal.active and signal.source.lower() == "ward"
                    for signal in request.cfw_signals),
                "cfw_signal_codes": [signal.code for signal in request.cfw_signals
                                     if signal.active],
            },
        }
        if (isinstance(provider_identity, Mapping)
                and boundary_kind != "post_action"
                and boundary_kind not in ACTION_INDEX_BOUNDARIES
                and boundary_kind not in CANDIDATE_ACTION_BOUNDARIES
                and not request.observation.action_completed
                and request.observation.candidate_action is None
                and not request.cfw_signals
                and not request.outcome_facts
                and not request.observation.evidence_refs
                and not request.observation.progress_made
                and not request.observation.regression_observed
                and not request.observation.artifact_changed
                and not preparation.system_one_query_attempted
                and not preparation.starvation_prevented
                and not decision.directives
                and not decision.preferred_stateworks
                and not decision.intervention_outcomes
                and not _lease_or_evaluation_due(
                    decision.state, request.observation.action_index)):
            predicted_cfw_revision = (
                decision.state.revision
                if descriptor.host_capabilities.get("cfw_connected")
                else request.cfw_lifecycle_revision)
            basis["decision_input_digest"] = _decision_input_digest(
                descriptor, request, decision.state, provider_identity,
                cfw_lifecycle_revision=predicted_cfw_revision)
        if preparation.system_one_query_attempted:
            basis["system_one"] = {
                "frame": dict(preparation.system_one_frame),
                "result": (dict(preparation.system_one_result)
                           if preparation.system_one_result is not None else None),
            }
        basis_digest = _digest(basis)
        saved = self.state_store.prepare_control_tick(
            scope, tick_id=request.tick_id, request_digest=digest,
            basis=basis, decision=decision.to_dict())
        _record_metric(self, "increment", "control_ticks_prepared")
        response = self._prepared_response(scope, request.tick_id, saved)
        response["basis_digest"] = basis_digest
        return response

    def _prepared_response(self, scope: str, tick_id: str,
                           transaction: Mapping[str, Any]) -> dict[str, Any]:
        decision = transaction["decision"]
        basis = transaction["basis"]
        return {
            "protocol_version": PROTOCOL_VERSION,
            "control_session_id": scope,
            "tick_id": tick_id,
            "decision_id": decision["decision_id"],
            "status": transaction["status"],
            "expected_behavior_revision": decision["expected_revision"],
            "candidate_behavior_state": decision["state"],
            "directives": list(decision.get("directives") or ()),
            "cfw_directives": [item for item in decision.get("directives") or ()
                               if item.get("operation") not in {
                                   "prefer_statework", "release_statework"}],
            "csw_directives": [item for item in decision.get("directives") or ()
                               if item.get("operation") in {
                                   "prefer_statework", "release_statework"}],
            "reason_codes": ([decision.get("reason") or "unspecified"]
                             + (["attention_starvation_prevented"]
                                if basis.get("attention_starvation_prevented") else [])),
            "expected_effects": [{
                "intervention_id": item["intervention_id"],
                "effect": item["expected_effect"],
                "target_criteria": item.get("target_criteria") or [],
                "evaluation_horizon_actions": item.get("evaluation_horizon_actions"),
            } for item in (decision["state"].get("active_interventions") or ())],
            "basis": dict(basis),
            "basis_digest": _digest(basis),
            "committed_result": transaction.get("result"),
            "abort_detail": transaction.get("detail"),
        }

    @_measure("control_tick_commit_duration_ms")
    def commit_control_tick(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        request = ControlCommitRequest.from_mapping(raw)
        descriptor = self._descriptor(request.control_session_id)
        scope = request.control_session_id
        transaction = self.state_store.control_tick(scope, request.tick_id)
        if transaction is None or transaction["decision"]["decision_id"] != request.decision_id:
            raise RuntimeError("control commit references an unknown decision")
        basis = transaction["basis"]
        if request.basis_digest != _digest(basis):
            raise RuntimeError("control commit basis digest is stale or malformed")
        commit_digest = _digest(request.to_dict())
        if transaction["status"] == "committed":
            result = transaction["result"] or {}
            if result.get("commit_digest") != commit_digest:
                raise RuntimeError("committed control tick receipt changed")
            return dict(result)
        if transaction["status"] != "prepared":
            raise RuntimeError("control decision is not awaiting commit")

        decision = _decision_from_mapping(
            transaction["decision"], basis=transaction["basis"])
        validate_state_against_cfw_contract(
            decision.state, descriptor, label="prepared DP behavior candidate")
        expected_directives = {item.directive_id for item in decision.directives}
        if set(request.applied_directive_ids) != expected_directives:
            raise ValueError("host receipts do not cover the exact prepared directives")
        validate_applied_authorities(decision, basis, request, descriptor)
        record = self.state_store.load(scope)
        if (record.state.revision != decision.expected_revision
                or record.action_index != int(basis["expected_action_index"])):
            raise RuntimeError("behavior state changed before control commit")

        committed_state = BehaviorState.from_mapping({
            **decision.state.to_dict(),
            "active_stateworks": list(request.csw_snapshot.active_stateworks),
        })
        validate_state_against_cfw_contract(
            committed_state, descriptor, label="committed DP behavior state")
        application_events, outcome_events, receipts = self._application_records(
            descriptor, record.state, decision, request.applied_directive_ids)
        event_values = application_events + outcome_events
        prepare_system_one_event = getattr(
            self.decision_service, "prepare_system_one_decision_event", None)
        if callable(prepare_system_one_event):
            system_one_event = prepare_system_one_event(descriptor, basis)
            if system_one_event is not None:
                event_values.append(system_one_event)
        result = {
            "protocol_version": PROTOCOL_VERSION,
            "control_session_id": scope,
            "tick_id": request.tick_id,
            "decision_id": request.decision_id,
            "status": "committed",
            "behavior_revision": committed_state.revision,
            "host_action_index": int(basis.get(
                "commit_action_index", basis["host_action_index"])),
            "cfw_projection_action_index": int(basis["cfw_projection_action_index"]),
            "candidate_action_index": int(basis["host_action_index"]),
            "boundary_phase": basis.get("boundary_phase", "post_action"),
            "boundary_kind": basis.get("boundary_kind", basis.get("boundary_phase", "post_action")),
            "boundary_sequence": int(basis.get("boundary_sequence", 0)),
            "behavior_state": committed_state.to_dict(),
            "cfw_lifecycle_revision": request.cfw_lifecycle_revision,
            "csw_snapshot": request.csw_snapshot.to_dict(),
            "applied_directive_ids": list(request.applied_directive_ids),
            "application_event_ids": [str(item["event_id"])
                                       for item in application_events],
            "outcome_event_ids": [str(item["event_id"])
                                  for item in outcome_events],
            "system_one_decision_event_ids": [
                str(item["event_id"]) for item in event_values
                if item.get("event_type") == "system_one_decision"],
            "commit_digest": commit_digest,
        }
        action_trace = (build_committed_action_trace(
            descriptor, record.state, committed_state, decision, basis,
            application_events, request.applied_directive_ids)
            if basis.get("boundary_kind") == "post_action" else None)
        transition_trace = build_control_transition_trace(
            scope, record.state, committed_state, decision, basis,
            request.applied_directive_ids)
        previous_basis = self.state_store.latest_control_tick_basis(scope)
        self.state_store.save(
            scope,
            expected_revision=record.state.revision,
            expected_action_index=record.action_index,
            action_index=int(basis.get(
                "commit_action_index", basis["host_action_index"])),
            state=committed_state,
            application_receipts=receipts,
            outbox_events=event_values,
            control_tick_id=request.tick_id,
            control_tick_result=result,
            behavioral_action_trace=(action_trace.to_dict()
                                     if action_trace is not None else None),
            behavioral_control_transition=transition_trace,
        )
        _record_laya_commit_metrics(self, basis, record.state, decision)
        _record_metric(self, "increment", "control_ticks_committed")
        previous_lease = record.state.attention_lease
        current_lease = committed_state.attention_lease
        if (previous_lease is not None and current_lease is not None
                and previous_lease.criterion_ids != current_lease.criterion_ids):
            _record_metric(self, "increment", "attention_rotations")
        if (record.state.cognitive_mode != "BLOCKED"
                and committed_state.cognitive_mode == "BLOCKED"):
            _record_metric(self, "increment", "global_blocked_transitions")
        previous_goal_items = {
            item.get("criterion_id"): item.get("status")
            for item in ((previous_basis or {}).get("goal_set") or {}).get("items", ())
            if isinstance(item, Mapping)}
        current_goal_items = {
            item.get("criterion_id"): item.get("status")
            for item in (basis.get("goal_set") or {}).get("items", ())
            if isinstance(item, Mapping)}
        recoveries = sum(
            prior_status in {"BLOCKED", "DEPENDENCY_BLOCKED"}
            and current_goal_items.get(criterion_id) in {"READY", "ACTIVE", "VERIFIED"}
            for criterion_id, prior_status in previous_goal_items.items())
        if recoveries:
            _record_metric(self, "increment", "criterion_local_recoveries", recoveries)
        self._dispatch_outbox(scope)
        return result

    def _application_records(self, descriptor: ControlSessionDescriptor,
                             prior_state: BehaviorState,
                             decision: BehavioralDecision,
                             applied_directive_ids: tuple[str, ...]
                             ) -> tuple[list[Mapping[str, Any]],
                                        list[Mapping[str, Any]],
                                        dict[str, tuple[str, ...]]]:
        identity = descriptor.identity
        context = InterventionEventContext(
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
            goal_contract_hash=identity.goal_contract_hash,
        )
        old_ids = {item.intervention_id for item in prior_state.active_interventions}
        applications: list[Mapping[str, Any]] = []
        receipts: dict[str, tuple[str, ...]] = {}
        for intervention in decision.state.active_interventions:
            if intervention.intervention_id in old_ids:
                continue
            directives = tuple(item for item in decision.directives
                               if item.intervention_id == intervention.intervention_id)
            directive_ids = tuple(sorted(
                item.directive_id for item in directives
                if item.directive_id in applied_directive_ids))
            if len(directive_ids) != len(directives):
                raise RuntimeError("active intervention lacks a complete host receipt")
            receipts[intervention.intervention_id] = directive_ids
            applications.append(self.decision_service.prepare_intervention_application(
                intervention, directives, directive_ids, event_context=context))
        outcomes = [self.decision_service.prepare_intervention_outcome(
            outcome, event_context=context) for outcome in decision.intervention_outcomes]
        return applications, outcomes, receipts

    def abort_control_tick(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        data = bounded_control_mapping(raw, "control abort")
        request = ControlAbortRequest.from_mapping(data)
        descriptor = self._descriptor(request.control_session_id)
        transaction = self.state_store.control_tick(
            request.control_session_id, request.tick_id)
        if transaction is None or transaction["decision"]["decision_id"] != request.decision_id:
            raise RuntimeError("control abort references an unknown decision")
        if request.basis_digest != _digest(transaction["basis"]):
            raise RuntimeError("control abort basis digest is stale or malformed")
        detail = {
            "rollback_completed": request.rollback_completed,
            "failure_class": request.failure_class,
            "applied_directive_ids": list(request.applied_directive_ids),
            "cfw_lifecycle_revision": request.cfw_lifecycle_revision,
            "csw_snapshot": request.csw_snapshot.to_dict(),
            "requires_reconciliation": not request.rollback_completed,
        }
        aborted = self.state_store.abort_control_tick(
            request.control_session_id, request.tick_id,
            decision_id=request.decision_id, detail=detail)
        if transaction["status"] == "prepared":
            _record_metric(self, "increment", "control_ticks_aborted")
        state = self.state_store.load(request.control_session_id).state
        validate_state_against_cfw_contract(state, descriptor)
        return {"protocol_version": PROTOCOL_VERSION,
                **aborted, "behavior_revision": state.revision,
                "behavior_state": state.to_dict(),
                "requires_reconciliation": not request.rollback_completed}

    @_measure("reconciliation_duration_ms")
    def reconcile_control_session(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        data = bounded_control_mapping(raw, "control reconciliation")
        expected = {"protocol_version", "control_session_id", "cfw_lifecycle_revision",
                    "csw_snapshot"}
        if set(data) != expected or data["protocol_version"] != PROTOCOL_VERSION:
            raise ValueError("control reconciliation protocol version or fields are invalid")
        scope = str(data["control_session_id"])
        descriptor = self._descriptor(scope)
        if type(data["cfw_lifecycle_revision"]) is not int or data[
                "cfw_lifecycle_revision"] < -1:
            raise ValueError("CFW lifecycle revision must be at least -1")
        csw = parse_csw_snapshot(data["csw_snapshot"])
        pending = self.state_store.pending_control_ticks(scope)
        record = self.state_store.load(scope)
        validate_state_against_cfw_contract(
            record.state, descriptor, label="persisted DP behavior state")
        latest_basis = self.state_store.latest_control_tick_basis(scope)
        latest_goal_set = (parse_goal_set(latest_basis["goal_set"])
                           if latest_basis and latest_basis.get("goal_set")
                           else descriptor.initial_goal_set)
        if not pending:
            self.state_store.synchronize_csw_state(
                scope, expected_revision=record.state.revision,
                expected_action_index=record.action_index,
                active_stateworks=csw.active_stateworks)
            record = self.state_store.load(scope)
            validate_state_against_cfw_contract(
                record.state, descriptor, label="reconciled DP behavior state")
            self.state_store.acknowledge_control_reconciliation(scope)
        _record_metric(self, "increment", "reconciliations")
        return {
            "protocol_version": PROTOCOL_VERSION,
            "control_session_id": scope,
            "behavior_state": record.state.to_dict(),
            "behavior_revision": record.state.revision,
            "host_action_index": record.action_index,
            "cfw_projection_action_index": int(
                latest_basis.get("cfw_projection_action_index", max(0, record.action_index))
                if latest_basis else max(0, record.action_index)),
            "boundary_sequence": self.state_store.latest_boundary_sequence(scope),
            "committed_cfw_signal_cursor": int(
                latest_basis.get("cfw_signal_cursor", 0) if latest_basis else 0),
            "goal_set": latest_goal_set.to_dict(),
            "cfw_lifecycle_revision": data["cfw_lifecycle_revision"],
            "csw_snapshot": csw.to_dict(),
            "pending_decisions": [self._prepared_response(
                scope, item["tick_id"], item)
                for item in pending],
            "latest_control_tick": self.state_store.latest_control_tick(scope),
        }

    def _dispatch_outbox(self, scope: str) -> None:
        for item in self.state_store.pending_outbox(scope):
            try:
                delivered = self.decision_service.dispatch_runtime_event(item.event)
                if delivered != item.event_id:
                    raise RuntimeError("DP event sink acknowledged a different event")
                self.state_store.acknowledge_outbox_event(scope, item.event_id)
            except Exception:
                break
