"""Side-effect-free preparation of host control decisions."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping

from ..attention import schedule_attention
from ..behavior_state import (BehaviorIntervention, BehavioralDecision,
                              CSWBehaviorSnapshot, FrameworkSignal,
                              GoalSetSnapshot, InterventionOutcome)
from ..fast_loop import FastLoopInput
from ..outcomes import (HostOutcomeFact, InterventionEvaluationFrame,
                        OutcomeEvidenceAuthority)
from ..runtime.contracts import RuntimeIdentity
from ..runtime.store import RuntimeStateRecord
from ..system_one_contracts import SystemOneFrame, SystemOneIdentity
from ..control_protocol import (ControlSessionDescriptor, ControlTickRequest)


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
    attention_last_seen: Mapping[str, int]
    goal_set: GoalSetSnapshot
    fairness_bound_actions: int
    starvation_prevented: tuple[str, ...]


class ControlDecisionEngine:
    """Prepare DP policy output without touching CFW, CSW, or durable state."""

    def __init__(self, *, decision_service: Any,
                 system_one_frame_factory: Any = None) -> None:
        self.decision_service = decision_service
        self.system_one_frame_factory = system_one_frame_factory

    def prepare(self, descriptor: ControlSessionDescriptor,
                record: RuntimeStateRecord,
                request: ControlTickRequest) -> DecisionPreparation:
        identity = descriptor.identity
        goal_set = request.goal_set
        goal_ids = {item.criterion_id for item in goal_set.items}
        if goal_ids != set(descriptor.objective_criteria):
            raise ValueError("host goal set must cover the complete control objective")
        if goal_set.revision < descriptor.initial_goal_set.revision:
            raise ValueError("host goal-set revision moved backwards")
        boundary_kind = request.boundary_kind
        if boundary_kind in {"pre_action", "post_action"}:
            if request.observation.action_index <= record.action_index:
                raise ValueError("action boundaries must advance the completed action index")
        elif boundary_kind == "pre_action_rejected":
            if request.observation.action_index != max(0, record.action_index):
                raise ValueError("rejected candidates must retain the completed action index")
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
        if (boundary_kind not in {"pre_action", "pre_action_rejected"}
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
        if len(request.cfw_signals) > 32:
            raise ValueError("CFW signal count exceeds the protocol bound")

        attention = schedule_attention(
            goal_set, record.state, action_index=request.observation.action_index)
        judgment = None
        system_one_frame = None
        system_one_result = None
        policy_context = {
            **dict(descriptor.host_capabilities.get("policy_context") or {}),
            **identity.to_dict(),
        }
        if self.system_one_frame_factory is not None:
            frame = self.system_one_frame_factory(
                identity, descriptor.objective,
                attention.lease.criterion_ids if attention.lease else (),
                record.state, request.observation, request.csw_snapshot,
                request.cfw_signals, policy_context)
            if frame is not None and not isinstance(frame, SystemOneFrame):
                raise TypeError("System-1 frame factory must return a validated frame")
            if frame is not None:
                if frame.identity.to_dict() != self._system_one_identity(identity).to_dict():
                    raise ValueError("System-1 frame identity escapes the control session")
                system_one_frame = frame.canonical_payload()
                judgment_result = self.decision_service.judge(frame)
                if hasattr(judgment_result, "judgment"):
                    # Only calibrated/advisory output may influence executive
                    # selection. The typed result is retained in the trusted host
                    # transaction basis even when it remains in shadow mode.
                    serialize = getattr(judgment_result, "to_dict", None)
                    if callable(serialize):
                        system_one_result = serialize()
                    judgment = (judgment_result.judgment
                                if getattr(judgment_result, "status", None) == "ok"
                                else None)
                else:
                    judgment = judgment_result

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
        )
        decision = self._parse_decision(
            self.decision_service.decide_behavior(input_value, judgment=judgment))
        if decision.scope != identity.scope:
            raise RuntimeError("DP decision scope differs from the control session")
        if decision.expected_revision != record.state.revision:
            raise RuntimeError("DP controller returned a stale behavior revision")
        if decision.state.attention_lease != attention.lease:
            raise RuntimeError("DP controller did not apply the host attention lease")
        self._validate_directives(descriptor, request.csw_snapshot, decision)
        return DecisionPreparation(
            decision, system_one_frame, system_one_result,
            attention.last_seen, goal_set,
            fairness_bound_actions=attention.fairness_bound_actions,
            starvation_prevented=attention.starvation_prevented,
        )

    def _assess_interventions(self, descriptor: ControlSessionDescriptor,
                              record: RuntimeStateRecord,
                              request: ControlTickRequest,
                              goal_set: GoalSetSnapshot,
                              attention_last_seen: Mapping[str, int]
                              ) -> list[InterventionOutcome]:
        evidence_refs = request.observation.evidence_refs
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
                             decision: BehavioralDecision) -> None:
        if len(decision.preferred_stateworks) > 1:
            raise ValueError("DP may request at most one CSW StateWork activation")
        eligible = {item.statework_id for item in snapshot.eligible_stateworks}
        active = set(snapshot.active_stateworks)
        framework_ids = set(descriptor.available_frameworks)
        for directive in decision.directives:
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
