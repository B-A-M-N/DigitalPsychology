"""Side-effect-free preparation of host control decisions."""
from __future__ import annotations

from dataclasses import dataclass, replace
import time
from typing import Any, Mapping

from ..attention import schedule_attention
from ..behavior_state import (BehaviorIntervention, BehaviorState, BehavioralDecision,
                              CSWBehaviorSnapshot, FrameworkSignal,
                              GoalSetSnapshot, InterventionOutcome)
from ..fast_loop import FastLoopInput
from ..outcomes import (HostOutcomeFact, InterventionEvaluationFrame,
                        OutcomeEvidenceAuthority)
from ..runtime.contracts import RuntimeIdentity
from ..runtime.store import RuntimeStateRecord
from ..system_one_contracts import SystemOneFrame, SystemOneIdentity
from .semantic_influence import SemanticInfluenceReceipt, compare_semantic_selection
from ..control_protocol import (ACTION_INDEX_BOUNDARIES,
                                CANDIDATE_ACTION_BOUNDARIES,
                                ControlSessionDescriptor, ControlTickRequest)
from ..compatibility import MAX_CFW_SIGNALS
from ..runtime.cfw_behavior_contract import (
    cfw_suppression_modes, validate_directives_against_cfw_contract,
    validate_state_against_cfw_contract,
)


class _ProvidedOutcomeFacts(OutcomeEvidenceAuthority):
    """Resolve evidence facts supplied by the trusted host control channel."""

    def __init__(self, facts: tuple[HostOutcomeFact, ...], *, scope: str) -> None:
        self._facts = {item.evidence_ref: item for item in facts}
        self._scope = scope

    def resolve_outcome_fact(self, evidence_ref: str, *, scope: str) -> HostOutcomeFact:
        if scope != self._scope:
            raise ValueError("outcome evidence scope escaped the control session")
        try:
            fact = self._facts[evidence_ref]
        except KeyError as exc:
            raise ValueError("outcome evidence reference was not supplied by the host") from exc
        if fact.scope != scope:
            raise ValueError("outcome fact scope escaped the control session")
        return fact


@dataclass(frozen=True)
class DecisionPreparation:
    decision: BehavioralDecision
    system_one_frame: Mapping[str, Any] | None
    system_one_result: Mapping[str, Any] | None
    system_one_query_attempted: bool
    system_one_query_opportunity: bool
    system_one_opportunity_question_id: str | None
    system_one_judgment_returned: bool
    system_one_query_failed: bool
    semantic_judgment_used: bool
    attention_last_seen: Mapping[str, int]
    goal_set: GoalSetSnapshot
    fairness_bound_actions: int
    starvation_prevented: tuple[str, ...]
    semantic_influence_receipt: SemanticInfluenceReceipt | None = None


class ControlDecisionEngine:
    """Prepare DP policy output without touching CFW, CSW, or durable state."""

    def __init__(self, *, decision_service: Any,
                 system_one_frame_factory: Any = None) -> None:
        self.decision_service = decision_service
        self.system_one_frame_factory = system_one_frame_factory

    def prepare(self, descriptor: ControlSessionDescriptor,
                record: RuntimeStateRecord,
                request: ControlTickRequest, *,
                previous_goal_set: GoalSetSnapshot | None = None
                ) -> DecisionPreparation:
        identity = descriptor.identity
        goal_set = request.goal_set
        goal_ids = {item.criterion_id for item in goal_set.items}
        if goal_ids != set(descriptor.objective_criteria):
            raise ValueError("host goal set must cover the complete control objective")
        if goal_set.revision < descriptor.initial_goal_set.revision:
            raise ValueError("host goal-set revision moved backwards")
        validate_state_against_cfw_contract(record.state, descriptor,
                                            label="persisted DP behavior state")
        boundary_kind = request.boundary_kind
        if boundary_kind in ACTION_INDEX_BOUNDARIES:
            expected_candidate_index = max(1, record.action_index + 1)
            if request.observation.action_index != expected_candidate_index:
                raise ValueError("action boundary must use the next sequential candidate index")
        elif request.observation.action_index != max(0, record.action_index):
            raise ValueError("non-action boundary must refer to the current action index")
        if boundary_kind == "pre_action" and (
                request.observation.candidate_action is None or request.outcome_facts):
            raise ValueError("pre-action control requires a candidate without outcome facts")
        if boundary_kind == "pre_action_rejected" and (
                request.observation.candidate_action is None
                or request.observation.candidate_preflight_result is None
                or request.outcome_facts):
            raise ValueError("rejected-candidate control requires its CFW gate result")
        if (request.observation.candidate_action is not None
                and descriptor.host_capabilities.get("cfw_connected")
                and request.observation.candidate_gate_receipts is None):
            raise ValueError("CFW-connected candidate boundary requires its gate receipt")
        if (boundary_kind not in CANDIDATE_ACTION_BOUNDARIES
                and request.observation.candidate_action is not None):
            raise ValueError("candidate actions are accepted only at pre-action boundaries")
        if not set(request.observation.criterion_status).issubset(goal_ids):
            raise ValueError("host criterion status escapes the control objective")
        if (request.observation.candidate_action is not None
                and not set(request.observation.candidate_action.affected_criterion_ids)
                .issubset(goal_ids)):
            raise ValueError("candidate action criterion escapes the control objective")
        if tuple(record.state.active_stateworks) != tuple(request.csw_snapshot.active_stateworks):
            raise RuntimeError("CSW state must be reconciled before preparing a control tick")
        for signal in request.cfw_signals:
            if signal.criterion_id is not None and signal.criterion_id not in goal_ids:
                raise ValueError("CFW signal criterion escapes the control objective")
        if any(fact.scope != identity.scope for fact in request.outcome_facts):
            raise ValueError("host outcome facts escape the control session")
        if len(request.cfw_signals) > MAX_CFW_SIGNALS:
            raise ValueError("CFW signal count exceeds the protocol bound")

        attention = schedule_attention(
            goal_set, record.state, action_index=request.observation.action_index)
        judgment = None
        system_one_frame = None
        system_one_result = None
        system_one_query_attempted = False
        system_one_query_opportunity = False
        system_one_opportunity_question_id = None
        system_one_judgment_returned = False
        system_one_query_failed = False
        planned_question = None
        policy_context = {
            **dict(descriptor.host_capabilities.get("policy_context") or {}),
            **identity.to_dict(),
            "non_goals": descriptor.non_goals,
        }
        if self.system_one_frame_factory is not None:
            frame = self.system_one_frame_factory(
                identity, descriptor.objective,
                attention.lease.criterion_ids if attention.lease else (),
                record.state, request.observation, request.csw_snapshot,
                request.cfw_signals, policy_context,
                descriptor.available_frameworks, previous_goal_set)
            query_plan = getattr(self.system_one_frame_factory,
                                 "last_query_plan", None)
            planned_question = getattr(query_plan, "question_id", None)
            if isinstance(planned_question, str) and planned_question:
                system_one_query_opportunity = True
                system_one_opportunity_question_id = planned_question
            if frame is not None and not isinstance(frame, SystemOneFrame):
                raise TypeError("System-1 frame factory must return a validated frame")
            if frame is not None:
                if not system_one_query_opportunity and frame.questions:
                    system_one_query_opportunity = True
                    system_one_opportunity_question_id = frame.questions[0].question_id
                if frame.identity.to_dict() != self._system_one_identity(identity).to_dict():
                    raise ValueError("System-1 frame identity escapes the control session")
                system_one_query_attempted = True
                system_one_frame = frame.canonical_payload()
                started = time.perf_counter()
                try:
                    judgment_result = self.decision_service.judge(frame)
                except Exception:
                    system_one_result = {
                        "status": "unavailable", "reason": "provider_exception",
                        "latency_ms": max(0.0, (time.perf_counter() - started) * 1000.0),
                    }
                    system_one_query_failed = True
                else:
                    latency_ms = max(0.0, (time.perf_counter() - started) * 1000.0)
                    if hasattr(judgment_result, "judgment"):
                        # Only calibrated/advisory output may influence executive
                        # selection. Shadow results remain in the trusted audit basis.
                        serialize = getattr(judgment_result, "to_dict", None)
                        if callable(serialize):
                            serialized = serialize()
                            if isinstance(serialized, Mapping):
                                system_one_result = {**dict(serialized),
                                                     "latency_ms": latency_ms}
                        system_one_judgment_returned = (
                            getattr(judgment_result, "judgment", None) is not None)
                        system_one_query_failed = getattr(
                            judgment_result, "status", None) in {
                                "unavailable", "rejected", "stale"}
                        judgment = (judgment_result.judgment
                                    if getattr(judgment_result, "status", None) == "ok"
                                    else None)
                    else:
                        judgment = judgment_result
                        system_one_judgment_returned = judgment_result is not None
                        system_one_query_failed = judgment_result is None

        assessments = (self._assess_interventions(
            descriptor, record, request, goal_set, attention.last_seen)
            if request.observation.action_completed else [])
        input_value = FastLoopInput(
            scope=identity.scope,
            state=record.state,
            csw_snapshot=request.csw_snapshot,
            objective_criteria=descriptor.objective_criteria,
            goal_set=goal_set,
            attention_lease=attention.lease,
            attention_last_seen=attention.last_seen,
            cfw_signals=request.cfw_signals,
            active_trigger_codes=request.observation.active_trigger_codes,
            semantic_opportunity_question_id=(
                planned_question if planned_question in {
                    "action_relation", "progress_relation", "retry_relation",
                    "goal_drift", "route_preference"} else None),
            actions_since_goal_progress=request.observation.actions_since_goal_progress,
            equivalent_retry_count=request.observation.equivalent_retry_count,
            host_action_index=request.observation.action_index,
            criterion_status=request.observation.criterion_status,
            action_completed=boundary_kind == "post_action",
            pre_action=boundary_kind in {"pre_action", "pre_action_rejected"},
            progress_made=request.observation.progress_made,
            regression_observed=request.observation.regression_observed,
            artifact_changed=request.observation.artifact_changed,
            context_pressure=request.observation.context_pressure,
            evidence_refs=request.observation.evidence_refs,
            intervention_assessments=tuple(assessments),
            available_frameworks=descriptor.available_frameworks,
            approach_epoch=request.observation.approach_epoch,
            policy_context=policy_context,
            cfw_suppression_modes=cfw_suppression_modes(descriptor),
        )
        decision = self._parse_decision(
            self.decision_service.decide_behavior(input_value, judgment=judgment))
        semantic_influence_receipt = None
        if judgment is not None:
            # This service call is pure: both decisions use the same host snapshot,
            # state revision, priors, and legal candidate set. The counterfactual
            # is retained for diagnosis and is never applied to CFW or CSW.
            counterfactual = self._parse_decision(
                self.decision_service.decide_behavior(input_value, judgment=None))
            semantic_influence_receipt = compare_semantic_selection(
                judgment, counterfactual, decision)
            if (semantic_influence_receipt is not None
                    and semantic_influence_receipt.influenced):
                prior_intervention_ids = {
                    item.intervention_id for item in record.state.active_interventions}
                linked_interventions = tuple(
                    replace(item,
                            semantic_decision_refs=(
                                semantic_influence_receipt.provider_decision_id,),
                            semantic_influence_receipt_id=semantic_influence_receipt.receipt_id)
                    if item.intervention_id not in prior_intervention_ids else item
                    for item in decision.state.active_interventions)
                decision = replace(
                    decision,
                    state=replace(decision.state,
                                  active_interventions=linked_interventions))
        validate_state_against_cfw_contract(
            decision.state, descriptor, label="DP policy candidate")
        validate_directives_against_cfw_contract(decision.directives, descriptor)
        if decision.scope != identity.scope:
            raise RuntimeError("DP decision scope differs from the control session")
        if decision.expected_revision != record.state.revision:
            raise RuntimeError("DP controller returned a stale behavior revision")
        if decision.state.attention_lease != attention.lease:
            raise RuntimeError("DP controller did not apply the host attention lease")
        self._validate_directives(descriptor, request.csw_snapshot, decision,
                                  prior_state=record.state)
        return DecisionPreparation(
            decision=decision,
            system_one_frame=system_one_frame,
            system_one_result=system_one_result,
            system_one_query_attempted=system_one_query_attempted,
            system_one_query_opportunity=system_one_query_opportunity,
            system_one_opportunity_question_id=system_one_opportunity_question_id,
            system_one_judgment_returned=system_one_judgment_returned,
            system_one_query_failed=system_one_query_failed,
            semantic_judgment_used=judgment is not None,
            attention_last_seen=attention.last_seen,
            goal_set=goal_set,
            fairness_bound_actions=attention.fairness_bound_actions,
            starvation_prevented=attention.starvation_prevented,
            semantic_influence_receipt=semantic_influence_receipt,
        )

    def _assess_interventions(self, descriptor: ControlSessionDescriptor,
                              record: RuntimeStateRecord,
                              request: ControlTickRequest,
                              goal_set: GoalSetSnapshot,
                              attention_last_seen: Mapping[str, int]
                              ) -> list[InterventionOutcome]:
        # An observation may carry ordinary host evidence for task context or
        # criterion tracking. Intervention evaluation accepts only references
        # that the host explicitly typed as outcome facts in this control tick.
        evidence_refs = tuple(fact.evidence_ref for fact in request.outcome_facts)
        supplied = _ProvidedOutcomeFacts(request.outcome_facts,
                                         scope=descriptor.identity.scope)
        active_ids = {item.intervention_id for item in record.state.active_interventions}
        applied_ids = tuple(sorted(active_ids & set(record.application_receipts)))
        competing = tuple(sorted(active_ids))
        results = []
        for intervention in record.state.active_interventions:
            elapsed = request.observation.action_index - intervention.baseline_action_index
            complete = elapsed >= int(intervention.evaluation_horizon_actions or
                                      intervention.expires_after_actions)
            if not evidence_refs and not complete:
                continue
            frame = InterventionEvaluationFrame(
                scope=descriptor.identity.scope,
                observed_revision=record.state.revision + 1,
                action_index=request.observation.action_index,
                evaluation_complete=complete,
                applied_intervention_ids=applied_ids,
                competing_intervention_ids=tuple(
                    item for item in competing if item != intervention.intervention_id),
                evidence_refs=evidence_refs,
                goal_set=goal_set,
                attention_last_seen=attention_last_seen,
            )
            raw = self.decision_service.assess_intervention_outcome(
                intervention, frame, evidence_authority=supplied)
            results.append(replace(
                InterventionOutcome.from_mapping(raw),
                condition_id=intervention.condition_id,
                condition_basis_digest=intervention.condition_basis_digest,
            ))
        return results

    @staticmethod
    def _parse_decision(raw: Mapping[str, Any]) -> BehavioralDecision:
        from ..behavior_state import BehaviorDirective, BehaviorState
        return BehavioralDecision(
            decision_id=str(raw["decision_id"]), scope=str(raw["scope"]),
            expected_revision=int(raw["expected_revision"]),
            state=BehaviorState.from_mapping(raw["state"]),
            directives=tuple(BehaviorDirective(**dict(item))
                             for item in raw.get("directives") or ()),
            preferred_stateworks=tuple(raw.get("preferred_stateworks") or ()),
            intervention_outcomes=tuple(
                InterventionOutcome.from_mapping(item)
                for item in raw.get("intervention_outcomes") or ()),
            reason=str(raw.get("reason") or "no behavioral change required"),
            selected_candidate_id=raw.get("selected_candidate_id"),
        )

    @staticmethod
    def _system_one_identity(identity: RuntimeIdentity) -> SystemOneIdentity:
        return SystemOneIdentity(
            namespace_id=identity.namespace_id,
            application_id=identity.application_id,
            application_instance_id=identity.application_instance_id,
            agent_id=identity.agent_id,
            agent_instance_id=identity.agent_instance_id,
            session_id=identity.session_id,
            task_id=identity.task_id,
            attempt_id=identity.attempt_id,
        )

    @staticmethod
    def _validate_directives(descriptor: ControlSessionDescriptor,
                             snapshot: CSWBehaviorSnapshot,
                             decision: BehavioralDecision, *,
                             prior_state: BehaviorState | None = None) -> None:
        if len(decision.preferred_stateworks) > 1:
            raise ValueError("DP may request at most one CSW StateWork activation")
        eligible = {item.statework_id for item in snapshot.eligible_stateworks}
        active = set(snapshot.active_stateworks)
        framework_ids = set(descriptor.available_frameworks)
        owners = {
            item.intervention_id: item
            for state in (prior_state, decision.state) if state is not None
            for item in state.active_interventions
        }
        for directive in decision.directives:
            owner = owners.get(directive.intervention_id)
            if owner is None:
                raise ValueError("DP directive has no live or closing intervention owner")
            if directive.expires_at_action != owner.expires_at_action:
                raise ValueError("DP directive expiry differs from its intervention owner")
            if directive.operation in {"prefer_statework", "release_statework"}:
                if directive.operation == "prefer_statework":
                    if directive.target not in eligible or directive.value != "preferred":
                        raise ValueError("DP selected an unavailable CSW StateWork")
                elif directive.target not in active or directive.value != "unselected":
                    raise ValueError("DP requested an illegal CSW StateWork release")
            elif directive.operation == "set_framework":
                if directive.target.lower() not in framework_ids:
                    raise ValueError("DP directive targets an unavailable CFW component")
                if directive.target.lower() == "ward" and directive.value != "mandatory":
                    raise ValueError("DP directive cannot weaken the WARD hard floor")
            elif directive.operation not in {"set_mode", "set_control"}:
                raise ValueError("DP directive operation is outside the control protocol")


__all__ = ["ControlDecisionEngine", "DecisionPreparation"]
