"""Durable prepare/apply/commit control transactions for the host MCP bridge."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from ..behavior_state import (BehaviorDirective, BehaviorState, BehavioralDecision,
                              CSWBehaviorSnapshot, InterventionOutcome)
from ..outcomes import InterventionEventContext
from ..runtime.store import BehaviorStateStore
from ..runtime.projection import project_behavior_context
from ..system_one_contracts import SystemOneFrame
from ..control_protocol import (PROTOCOL_VERSION, ControlAbortRequest,
                                ControlCommitRequest, ControlSessionDescriptor,
                                ControlTickRequest, bounded_control_mapping,
                                parse_csw_snapshot, parse_goal_set)
from .engine import ControlDecisionEngine


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _decision_from_mapping(value: Mapping[str, Any]) -> BehavioralDecision:
    data = dict(value)
    return BehavioralDecision(
        decision_id=str(data["decision_id"]), scope=str(data["scope"]),
        expected_revision=int(data["expected_revision"]),
        state=BehaviorState.from_mapping(data["state"]),
        directives=tuple(BehaviorDirective(**dict(item))
                         for item in data.get("directives") or ()),
        preferred_stateworks=tuple(data.get("preferred_stateworks") or ()),
        intervention_outcomes=tuple(InterventionOutcome.from_mapping(item)
                                    for item in data.get("intervention_outcomes") or ()),
        reason=str(data.get("reason") or "no behavioral change required"),
    )


class ControlPlaneService:
    """DP policy service. Physical CFW/CSW application remains host-owned."""

    def __init__(self, *, decision_service: Any, state_store: BehaviorStateStore,
                 system_one_frame_factory: Any = None) -> None:
        self.decision_service = decision_service
        self.state_store = state_store
        if system_one_frame_factory is None:
            factory_provider = getattr(
                decision_service, "control_system_one_frame_factory", None)
            if callable(factory_provider):
                system_one_frame_factory = factory_provider()
        self.engine = ControlDecisionEngine(
            decision_service=decision_service,
            system_one_frame_factory=system_one_frame_factory)

    def close(self) -> None:
        close = getattr(self.decision_service, "close", None)
        if callable(close):
            close()

    def open_control_session(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        descriptor = ControlSessionDescriptor.from_mapping(raw)
        self._validate_session_capabilities(descriptor)
        scope = descriptor.control_session_id
        self.state_store.register_control_session(scope, descriptor.to_dict())
        record = self.state_store.load(scope)
        return {
            "protocol_version": PROTOCOL_VERSION,
            "control_session_id": scope,
            "behavior_revision": record.state.revision,
            "host_action_index": record.action_index,
            "boundary_sequence": self.state_store.latest_boundary_sequence(scope),
            "behavior_state": record.state.to_dict(),
            "capabilities": {
                "prepare_commit": True,
                "abort": True,
                "reconcile": True,
                "max_objective_criteria": 256,
                "max_cfw_signals": 32,
                "max_active_stateworks": 16,
            },
        }

    def _validate_session_capabilities(self,
                                       descriptor: ControlSessionDescriptor) -> None:
        """Fail closed on actuation versions and provider-required task semantics."""
        from ..runtime.cfw_projection import CFW_BEHAVIOR_PROJECTION_VERSION
        from ..runtime.adapters.csw import CSW_WORKFLOW_PORT_VERSION

        capabilities = descriptor.host_capabilities
        if capabilities["cfw_connected"]:
            if (descriptor.cfw_authority_identity.get("adapter_kind")
                    != "cfw-component-runtime"
                    or descriptor.cfw_authority_identity.get(
                        "adapter_protocol_version") != "1.0.0"):
                raise ValueError("CFW adapter identity or protocol version is incompatible")
            actual = descriptor.cfw_authority_identity.get(
                "behavior_projection_version")
            if actual != CFW_BEHAVIOR_PROJECTION_VERSION:
                raise ValueError(
                    f"CFW behavior projection version {actual!r} is incompatible "
                    f"with DP's {CFW_BEHAVIOR_PROJECTION_VERSION!r}")
        if capabilities["csw_connected"]:
            if (descriptor.csw_authority_identity.get("adapter_kind")
                    != "csw-workflow-authority"
                    or descriptor.csw_authority_identity.get(
                        "adapter_protocol_version") != "1.0.0"):
                raise ValueError("CSW adapter identity or protocol version is incompatible")
            actual = descriptor.csw_authority_identity.get("workflow_port_version")
            if actual != CSW_WORKFLOW_PORT_VERSION:
                raise ValueError(
                    f"CSW workflow port version {actual!r} is incompatible "
                    f"with DP's {CSW_WORKFLOW_PORT_VERSION!r}")
        if self.engine.system_one_frame_factory is not None:
            from .system_one import _deliverable_kind
            policy_context = capabilities.get("policy_context") or {}
            if not isinstance(policy_context, Mapping):
                raise ValueError("host semantic policy context must be a mapping")
            _deliverable_kind(policy_context, provider_enabled=True)

    def prepare_control_tick(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        request = ControlTickRequest.from_mapping(raw)
        descriptor = self._descriptor(request.control_session_id)
        scope = request.control_session_id
        record = self.state_store.load(scope)
        digest = _digest(request.to_dict())
        existing = self.state_store.control_tick(scope, request.tick_id)
        if existing is not None:
            if existing["request_digest"] != digest:
                raise RuntimeError("control tick ID was reused with different input")
            return self._prepared_response(scope, request.tick_id, existing)
        if request.expected_behavior_revision != record.state.revision:
            raise RuntimeError("control tick behavior revision is stale")

        preparation = self.engine.prepare(descriptor, record, request)
        decision = preparation.decision
        boundary_kind = str(request.boundary_kind)
        commit_action_index = (request.observation.action_index
                               if boundary_kind == "post_action" else record.action_index)
        basis = {
            "expected_behavior_revision": record.state.revision,
            "expected_action_index": record.action_index,
            "host_action_index": request.observation.action_index,
            "commit_action_index": commit_action_index,
            "boundary_phase": boundary_kind,
            "boundary_kind": boundary_kind,
            "boundary_sequence": request.boundary_sequence,
            "goal_set_revision": request.goal_set.revision,
            "cfw_lifecycle_revision": request.cfw_lifecycle_revision,
            "csw_selection_revision": request.csw_snapshot.revision,
            "csw_snapshot_id": request.csw_snapshot.snapshot_id,
            "csw_active_stateworks": list(request.csw_snapshot.active_stateworks),
            "csw_eligible_stateworks": [
                item.statework_id for item in request.csw_snapshot.eligible_stateworks],
            "csw_workflow_revisions": dict(request.csw_snapshot.workflow_revisions),
            "goal_set": request.goal_set.to_dict(),
            "attention_fairness_bound_actions": preparation.fairness_bound_actions,
            "attention_starvation_prevented": list(preparation.starvation_prevented),
            "semantic_query_attempted": preparation.system_one_frame is not None,
            "semantic_judgment_used": preparation.semantic_judgment_used,
            "behavioral_facts": {
                "active_trigger_codes": list(request.observation.active_trigger_codes),
                "equivalent_retry_count": request.observation.equivalent_retry_count,
                "candidate_preflight_result": request.observation.candidate_preflight_result,
                "candidate_rejection_stage": request.observation.candidate_rejection_stage,
                "evidence_refs": list(request.observation.evidence_refs),
                "progress_made": request.observation.progress_made,
                "regression_observed": request.observation.regression_observed,
                "cfw_signal_codes": [signal.code for signal in request.cfw_signals
                                     if signal.active],
            },
        }
        if preparation.system_one_frame is not None:
            basis["system_one"] = {
                "frame": dict(preparation.system_one_frame),
                "result": (dict(preparation.system_one_result)
                           if preparation.system_one_result is not None else None),
            }
        basis_digest = _digest(basis)
        saved = self.state_store.prepare_control_tick(
            scope, tick_id=request.tick_id, request_digest=digest,
            basis=basis, decision=decision.to_dict())
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

        decision = _decision_from_mapping(transaction["decision"])
        expected_directives = {item.directive_id for item in decision.directives}
        if set(request.applied_directive_ids) != expected_directives:
            raise ValueError("host receipts do not cover the exact prepared directives")
        self._validate_applied_authorities(decision, basis, request, descriptor)
        record = self.state_store.load(scope)
        if (record.state.revision != decision.expected_revision
                or record.action_index != int(basis["expected_action_index"])):
            raise RuntimeError("behavior state changed before control commit")

        committed_state = BehaviorState.from_mapping({
            **decision.state.to_dict(),
            "active_stateworks": list(request.csw_snapshot.active_stateworks),
        })
        application_events, outcome_events, receipts = self._application_records(
            descriptor, record.state, decision, request.applied_directive_ids)
        event_values = application_events + outcome_events
        result = {
            "protocol_version": PROTOCOL_VERSION,
            "control_session_id": scope,
            "tick_id": request.tick_id,
            "decision_id": request.decision_id,
            "status": "committed",
            "behavior_revision": committed_state.revision,
            "host_action_index": int(basis.get(
                "commit_action_index", basis["host_action_index"])),
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
            "commit_digest": commit_digest,
        }
        action_trace = (self._committed_action_trace(
            descriptor, record.state, committed_state, decision, basis,
            application_events)
            if basis.get("boundary_kind") == "post_action" else None)
        transition_trace = self._control_transition_trace(
            scope, record.state, committed_state, decision, basis)
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
        self._dispatch_outbox(scope)
        return result

    @staticmethod
    def _control_transition_trace(scope: str, prior_state: BehaviorState,
                                  state: BehaviorState,
                                  decision: BehavioralDecision,
                                  basis: Mapping[str, Any]) -> dict[str, Any]:
        from ..telemetry.control_trace import ControlTransitionTrace

        facts = dict(basis.get("behavioral_facts") or {})
        evidence_classes = []
        if facts.get("evidence_refs"):
            evidence_classes.append("host_evidence")
        if facts.get("progress_made"):
            evidence_classes.append("host_progress")
        if facts.get("regression_observed"):
            evidence_classes.append("host_regression")
        if facts.get("cfw_signal_codes"):
            evidence_classes.append("cfw_signal")
        if basis.get("semantic_judgment_used"):
            evidence_classes.append("semantic_judgment")
        if facts.get("candidate_rejection_stage"):
            evidence_classes.append("cfw_action_gate")
        if not evidence_classes:
            evidence_classes.append("policy_observation")
        causes = tuple(dict.fromkeys(
            [decision.reason] + [item.cause for item in decision.directives]))
        return ControlTransitionTrace(
            control_session_id=scope,
            boundary_sequence=int(basis["boundary_sequence"]),
            boundary_kind=str(basis["boundary_kind"]),
            behavior_revision=state.revision,
            prior_mode=prior_state.cognitive_mode,
            resulting_mode=state.cognitive_mode,
            transition_causes=causes,
            justification_class="+".join(evidence_classes),
            semantic_query_attempted=bool(basis.get("semantic_query_attempted")),
            semantic_judgment_used=bool(basis.get("semantic_judgment_used")),
            cfw_rejection_stage=(str(facts["candidate_rejection_stage"])
                                 if facts.get("candidate_rejection_stage") else None),
        ).to_dict()

    @staticmethod
    def _committed_action_trace(descriptor: ControlSessionDescriptor,
                                prior_state: BehaviorState,
                                state: BehaviorState,
                                decision: BehavioralDecision,
                                basis: Mapping[str, Any],
                                application_events: list[Mapping[str, Any]]) -> Any:
        """Build a committed-action record from private host/control evidence."""
        from ..attention import fairness_bound_actions
        from ..runtime.cfw_projection import build_cfw_behavior_projection
        from ..telemetry.behavioral_trace import ActionTrace

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
        system_one = dict(basis.get("system_one") or {})
        system_result = system_one.get("result")
        semantic_attempted = bool(system_one.get("frame"))
        semantic_result = bool(isinstance(system_result, Mapping)
                               and system_result.get("status") == "ok")
        evidence_classes = set()
        if facts.get("evidence_refs"):
            evidence_classes.add("host_evidence_ref")
        if facts.get("progress_made"):
            evidence_classes.add("host_progress")
        if facts.get("regression_observed"):
            evidence_classes.add("host_regression")
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
        projection = build_cfw_behavior_projection(
            state, host_action_index=action_index).to_dict()
        projection_digest = hashlib.sha256(_canonical(projection).encode()).hexdigest()
        newly_applied = bool(application_events)
        goal_drift = any(
            item.cause in {"goal_drift", "semantic_goal_drift"}
            for item in decision.directives)
        candidate_stage = facts.get("candidate_rejection_stage")
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
            fairness_forced_rotation=bool(rotated and prevented),
            fairness_prevented_criteria=prevented,
            intervention_applied=newly_applied,
            abstained=decision.reason.startswith("abstain"),
            equivalent_retry=("equivalent_retry" in
                              set(facts.get("active_trigger_codes") or ())),
            goal_drift=goal_drift,
            projection_digest=projection_digest,
            semantic_query=semantic_attempted,
            semantic_attempted=semantic_attempted,
            semantic_result=semantic_result,
            semantic_used=semantic_result,
            semantic_failed=semantic_attempted and not semantic_result,
            cfw_preflight_rejection=candidate_stage == "cfw_preflight",
            cfw_final_rejection=candidate_stage == "cfw_final_gate",
            authoritative_evidence_classes=tuple(sorted(evidence_classes)),
        )

    def behavioral_trial_record(self, control_session_id: str, *, trial_id: str,
                                task_outcome_score: float | None = None,
                                task_completed: bool | None = None) -> dict[str, Any]:
        """Return persisted action metrics plus separately stored control transitions."""
        from ..telemetry.behavioral_trace import (
            ActionTrace, BehavioralTrace, provider_attestation, provider_status,
            runtime_configuration,
        )

        descriptor = self._descriptor(control_session_id)
        attestation = provider_attestation(self.engine.system_one_frame_factory)
        trace = BehavioralTrace(
            task_id=descriptor.identity.task_id,
            configuration=runtime_configuration(
                cfw_connected=bool(descriptor.available_frameworks),
                csw_connected=bool(descriptor.host_capabilities.get("csw_connected")),
                attestation=attestation),
            semantic_provider_status=provider_status(
                attestation,
                factory_configured=self.engine.system_one_frame_factory is not None),
            semantic_model_hash=(attestation.get("model_content_hash")
                                 if attestation else None),
            semantic_provider_attestation=attestation,
        )
        for row in self.state_store.behavioral_action_traces(control_session_id):
            trace.record(ActionTrace.from_dict(row), mode_cause=(
                str(row.get("authoritative_evidence_classes") or ["mode_transition"])[0]
                if row.get("authoritative_evidence_classes") else "mode_transition"),
                         authoritative_evidence=bool(
                             row.get("authoritative_evidence_classes")))
        record = trace.to_dict(trial_id=trial_id,
                               task_outcome_score=task_outcome_score,
                               task_completed=task_completed)
        transitions = self.state_store.committed_control_transitions(control_session_id)
        record["cfw_preflight_rejections"] = sum(
            1 for item in transitions
            if item.get("cfw_rejection_stage") == "cfw_preflight")
        record["cfw_final_gate_rejections"] = sum(
            1 for item in transitions
            if item.get("cfw_rejection_stage") == "cfw_final_gate")
        record["control_transition_count"] = len(transitions)
        return record

    def _validate_applied_authorities(self, decision: BehavioralDecision,
                                      basis: Mapping[str, Any],
                                      request: ControlCommitRequest,
                                      descriptor: ControlSessionDescriptor) -> None:
        original_cfw = int(basis["cfw_lifecycle_revision"])
        expected_cfw = (decision.state.revision if descriptor.available_frameworks
                        else original_cfw)
        if request.cfw_lifecycle_revision != expected_cfw:
            raise RuntimeError("CFW lifecycle revision does not match the committed behavior state")

        original_active = set(basis["csw_active_stateworks"])
        expected_active = set(original_active)
        csw_changes = 0
        eligible = set(basis["csw_eligible_stateworks"])
        for directive in decision.directives:
            if directive.operation == "release_statework":
                if directive.target not in expected_active:
                    raise ValueError("CSW release target was not active in the basis")
                expected_active.remove(directive.target)
                csw_changes += 1
            elif directive.operation == "prefer_statework":
                if directive.target not in eligible:
                    raise ValueError("CSW activation target was not eligible in the basis")
                if directive.target in expected_active:
                    raise ValueError("CSW activation target was already active")
                expected_active.add(directive.target)
                csw_changes += 1
        if set(request.csw_snapshot.active_stateworks) != expected_active:
            raise ValueError("CSW post-application state does not match DP requests")
        expected_revision = int(basis["csw_selection_revision"]) + csw_changes
        if request.csw_snapshot.revision != expected_revision:
            raise RuntimeError("CSW selection revision changed during control application")
        if not csw_changes and request.csw_snapshot.snapshot_id != basis["csw_snapshot_id"]:
            raise RuntimeError("CSW snapshot changed without a prepared selection mutation")
        for statework_id, revision in basis["csw_workflow_revisions"].items():
            if (statework_id in request.csw_snapshot.active_stateworks
                    and request.csw_snapshot.workflow_revisions.get(statework_id) != revision):
                raise RuntimeError("CSW workflow revision changed during control application")

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
        self._descriptor(request.control_session_id)
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
        state = self.state_store.load(request.control_session_id).state
        return {"protocol_version": PROTOCOL_VERSION,
                **aborted, "behavior_revision": state.revision,
                "behavior_state": state.to_dict(),
                "requires_reconciliation": not request.rollback_completed}

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
        return {
            "protocol_version": PROTOCOL_VERSION,
            "control_session_id": scope,
            "behavior_state": record.state.to_dict(),
            "behavior_revision": record.state.revision,
            "host_action_index": record.action_index,
            "boundary_sequence": self.state_store.latest_boundary_sequence(scope),
            "goal_set": latest_goal_set.to_dict(),
            "cfw_lifecycle_revision": data["cfw_lifecycle_revision"],
            "csw_snapshot": csw.to_dict(),
            "pending_decisions": [self._prepared_response(
                scope, item["tick_id"], item)
                for item in pending],
            "objective": descriptor.objective,
            "projection": project_behavior_context(
                record.state, csw, objective=descriptor.objective,
                goal_set=latest_goal_set,
                cfw_connected=bool(descriptor.available_frameworks),
                csw_connected=bool(descriptor.csw_authority_identity),
                outcome_evidence_connected=True),
        }

    def close_control_session(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        data = bounded_control_mapping(raw, "control close")
        if set(data) != {"protocol_version", "control_session_id"} \
                or data["protocol_version"] != PROTOCOL_VERSION:
            raise ValueError("control close protocol version or fields are invalid")
        scope = str(data["control_session_id"])
        self._descriptor(scope)
        self.state_store.close_control_session(scope)
        return {"protocol_version": PROTOCOL_VERSION,
                "control_session_id": scope, "status": "closed"}

    def _descriptor(self, scope: str) -> ControlSessionDescriptor:
        stored = self.state_store.control_session(scope)
        if stored is None or stored["status"] != "open":
            raise RuntimeError("control session is not open")
        descriptor = ControlSessionDescriptor.from_mapping(stored["descriptor"])
        if descriptor.control_session_id != scope:
            raise RuntimeError("persisted control session identity is inconsistent")
        return descriptor

    def _dispatch_outbox(self, scope: str) -> None:
        for item in self.state_store.pending_outbox(scope):
            try:
                delivered = self.decision_service.dispatch_runtime_event(item.event)
                if delivered != item.event_id:
                    raise RuntimeError("DP event sink acknowledged a different event")
                self.state_store.acknowledge_outbox_event(scope, item.event_id)
            except Exception:
                break


__all__ = ["ControlPlaneService"]
