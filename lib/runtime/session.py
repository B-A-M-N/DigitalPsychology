"""DP-owned host session and behavioral event loop."""
from __future__ import annotations

from dataclasses import dataclass
import json
from threading import RLock
from typing import Any, Mapping, Sequence

from ..behavior_state import (BehaviorDirective, BehaviorIntervention,
                              BehaviorState, BehavioralDecision,
                              CSWBehaviorSnapshot, InterventionOutcome)
from ..fast_loop import FastLoopInput
from ..outcomes import InterventionEvaluationFrame, InterventionEventContext
from ..system_one_contracts import SystemOneFrame, SystemOneIdentity
from .contracts import (BehavioralDecisionService, CFWComponentPort,
                        CSWWorkflowPort, RuntimeIdentity, RuntimeObservation,
                        SystemOneFrameFactory)
from .store import BehaviorStateStore, RuntimeStateRecord


@dataclass(frozen=True)
class RuntimeTurnResult:
    decision: BehavioralDecision
    state: BehaviorState
    csw_snapshot: CSWBehaviorSnapshot
    applied_directive_ids: tuple[str, ...]
    outcome_event_ids: tuple[str, ...]
    application_event_ids: tuple[str, ...]
    behavior_context: str


class RuntimeSession:
    """Coordinate DP policy, CFW components, and authoritative CSW state.

    This is host-only orchestration. Agent-facing MCP tools do not expose the
    session, its state store, directive application, or outcome authority.
    """

    def __init__(self, *, identity: RuntimeIdentity,
                 objective_criteria: Sequence[str],
                 controller: BehavioralDecisionService,
                 cfw: CFWComponentPort,
                 csw: CSWWorkflowPort,
                 outcome_evidence_authority: Any,
                 state_store: BehaviorStateStore,
                 system_one_frame_factory: SystemOneFrameFactory | None = None,
                 objective: str = "") -> None:
        self.identity = identity
        self.scope = identity.scope
        self.objective_criteria = tuple(objective_criteria)
        if (len(self.objective_criteria) > 8
                or len(set(self.objective_criteria)) != len(self.objective_criteria)
                or any(not isinstance(item, str) or not item or len(item) > 128
                       for item in self.objective_criteria)):
            raise ValueError("runtime objective criteria are malformed")
        if not isinstance(objective, str) or len(objective) > 1024:
            raise ValueError("runtime objective must be bounded text")
        self.objective = objective
        self.controller = controller
        self.cfw = cfw
        self.csw = csw
        self.outcome_evidence_authority = outcome_evidence_authority
        self.state_store = state_store
        self.system_one_frame_factory = system_one_frame_factory
        if system_one_frame_factory is not None and not callable(
                getattr(controller, "judge", None)):
            raise TypeError("System-1 frame construction requires a DP host judge")
        self._lock = RLock()

    def process_action(self, observation: RuntimeObservation, *,
                       judgment: Any = None) -> RuntimeTurnResult:
        """Observe one host action, decide, apply, and durably record DP changes."""
        with self._lock:
            record = self.state_store.load(self.scope)
            if observation.action_index <= record.action_index:
                raise ValueError("host action index must advance the runtime session")
            unknown = set(observation.criterion_status) - set(self.objective_criteria)
            if unknown:
                raise ValueError(f"host criteria escape the runtime objective: {sorted(unknown)}")

            snapshot = self._csw_snapshot()
            candidate_statework_ids = self._candidate_statework_ids(snapshot)
            signals = tuple(self.cfw.collect_behavior_signals(observation))
            available_frameworks = tuple(self.cfw.available_component_ids())
            if len(signals) > 32 or any(not hasattr(item, "code") for item in signals):
                raise ValueError("CFW adapter returned malformed behavioral signals")
            if judgment is None and self.system_one_frame_factory is not None:
                frame = self.system_one_frame_factory(
                    self.identity, self.objective, self.objective_criteria,
                    record.state, observation, snapshot, signals)
                if not isinstance(frame, SystemOneFrame):
                    raise TypeError("System-1 frame factory must return a validated frame")
                if frame.identity.to_dict() != self._system_one_identity().to_dict():
                    raise ValueError("System-1 frame identity escapes the runtime session")
                judgment = self.controller.judge(frame)
            outcome_assessments = self._assess_ready_interventions(
                record, observation)
            decision_input = FastLoopInput(
                scope=self.scope,
                state=record.state,
                csw_snapshot=snapshot,
                objective_criteria=self.objective_criteria,
                cfw_signals=signals,
                active_trigger_codes=observation.active_trigger_codes,
                actions_since_goal_progress=observation.actions_since_goal_progress,
                equivalent_retry_count=observation.equivalent_retry_count,
                host_action_index=observation.action_index,
                criterion_status=observation.criterion_status,
                action_completed=observation.action_completed,
                progress_made=observation.progress_made,
                regression_observed=observation.regression_observed,
                artifact_changed=observation.artifact_changed,
                context_pressure=observation.context_pressure,
                evidence_refs=observation.evidence_refs,
                intervention_assessments=tuple(outcome_assessments),
                available_frameworks=available_frameworks,
            )
            decision = self._parse_decision(
                self.controller.decide_behavior(decision_input, judgment=judgment))
            if decision.expected_revision != record.state.revision:
                raise RuntimeError("DP controller returned a stale behavior revision")

            cfw_directives = tuple(item for item in decision.directives
                                   if item.operation != "prefer_statework")
            prepared = self.cfw.prepare_behavior_directives(
                tuple(item.to_dict() for item in cfw_directives))
            applied: set[str] = set()
            activated_statework: str | None = None
            updated_snapshot = snapshot
            try:
                cfw_applied = tuple(self.cfw.apply_behavior_directives(
                    prepared, decision.state.to_dict()))
                if len(cfw_applied) != len(set(cfw_applied)):
                    raise RuntimeError("CFW returned duplicate directive receipts")
                expected_cfw = {item.directive_id for item in cfw_directives}
                if set(cfw_applied) != expected_cfw:
                    raise RuntimeError("CFW did not apply the exact prepared directives")
                applied.update(cfw_applied)

                preferences = [item for item in decision.directives
                               if item.operation == "prefer_statework"]
                if len(preferences) > 1 or len(decision.preferred_stateworks) > 1:
                    raise RuntimeError("DP may request at most one CSW activation per action")
                if preferences:
                    directive = preferences[0]
                    statework_id = directive.target
                    eligible = {item.statework_id
                                for item in snapshot.eligible_stateworks}
                    if (statework_id not in eligible
                            or decision.preferred_stateworks != (statework_id,)):
                        raise RuntimeError("DP selected a StateWork outside CSW eligibility")
                    updated_snapshot = self.csw.activate_statework(
                        statework_id,
                        expected_selection_revision=snapshot.revision,
                        candidate_statework_ids=candidate_statework_ids)
                    if statework_id not in updated_snapshot.active_stateworks:
                        raise RuntimeError("CSW did not confirm StateWork activation")
                    activated_statework = statework_id
                    applied.add(directive.directive_id)

                confirmed_snapshot = self._csw_snapshot(
                    candidate_statework_ids=candidate_statework_ids)
                if confirmed_snapshot != updated_snapshot:
                    raise RuntimeError(
                        "CSW selection changed during DP decision application; re-inspect required")
                updated_snapshot = confirmed_snapshot
                decision = self._with_authoritative_stateworks(decision, updated_snapshot)
                receipts = self._new_intervention_receipts(
                    record.state, decision, applied)
                # Commit only the DP projection here. CFW materializes it at
                # the next model boundary through its explicit adapter API.
                context = self.project_behavior_context(decision.state, updated_snapshot)
                self.state_store.save(
                    self.scope,
                    expected_revision=record.state.revision,
                    expected_action_index=record.action_index,
                    action_index=observation.action_index,
                    state=decision.state,
                    application_receipts=receipts,
                )
            except Exception as failure:
                compensation_errors = []
                try:
                    self.cfw.rollback_behavior_directives(prepared)
                except Exception as exc:
                    compensation_errors.append(f"CFW rollback failed: {exc}")
                if activated_statework is not None:
                    try:
                        self.csw.deactivate_statework(
                            activated_statework,
                            expected_selection_revision=updated_snapshot.revision,
                            candidate_statework_ids=candidate_statework_ids)
                    except Exception as exc:
                        compensation_errors.append(f"CSW activation rollback failed: {exc}")
                if compensation_errors:
                    raise RuntimeError(
                        "DP decision application failed and compensation was incomplete: "
                        + "; ".join(compensation_errors)) from failure
                raise

            application_events = []
            outcome_events = []
            new_interventions = {item.intervention_id: item
                                 for item in decision.state.active_interventions
                                 if item.intervention_id not in {
                                     old.intervention_id
                                     for old in record.state.active_interventions}}
            for intervention_id, intervention in new_interventions.items():
                directive_group = tuple(item for item in decision.directives
                                        if item.intervention_id == intervention_id)
                directive_ids = tuple(sorted(
                    item.directive_id for item in directive_group
                    if item.directive_id in applied))
                if len(directive_ids) != len(directive_group):
                    raise RuntimeError("active intervention lacks a complete application receipt")
                application_events.append(self.controller.record_intervention_application(
                    intervention, directive_group, directive_ids,
                    event_context=self._event_context()))
            for outcome in decision.intervention_outcomes:
                outcome_events.append(self.controller.record_intervention_outcome(
                    outcome, event_context=self._event_context()))

            return RuntimeTurnResult(
                decision=decision, state=decision.state,
                csw_snapshot=updated_snapshot,
                applied_directive_ids=tuple(sorted(applied)),
                outcome_event_ids=tuple(outcome_events),
                application_event_ids=tuple(application_events),
                behavior_context=context,
            )

    def apply_workflow_step(self, statework_id: str, step_id: str, *,
                            evidence_refs: Sequence[str],
                            expected_workflow_revision: int) -> Any:
        """Submit a legal-step request through CSW's current snapshot boundary."""
        snapshot = self._csw_snapshot(candidate_statework_ids=(statework_id,))
        if statework_id not in snapshot.active_stateworks:
            raise ValueError("StateWork is not active in the CSW-owned selection")
        if step_id not in snapshot.eligible_steps.get(statework_id, ()):
            raise ValueError("step is not in CSW's current legal-step snapshot")
        return self.csw.apply_step(
            statework_id, step_id, evidence_refs=tuple(evidence_refs),
            expected_workflow_revision=expected_workflow_revision)

    def before_model_call(self, *, tool_capable: bool = False,
                          action_hint: Mapping[str, Any] | None = None) -> str:
        """Ask the CFW component port to inject the committed DP projection."""
        record = self.state_store.load(self.scope)
        snapshot = self._csw_snapshot()
        projection = self.project_behavior_context(record.state, snapshot)
        self.cfw.synchronize_behavior_state(record.state.to_dict())
        return self.cfw.before_model_call(
            projection, tool_capable=tool_capable, action_hint=action_hint,
            active_statework_ids=snapshot.active_stateworks)

    def before_tool_planning(self, *,
                             action_hint: Mapping[str, Any] | None = None) -> str:
        record = self.state_store.load(self.scope)
        snapshot = self._csw_snapshot()
        projection = self.project_behavior_context(record.state, snapshot)
        self.cfw.synchronize_behavior_state(record.state.to_dict())
        return self.cfw.before_tool_planning(
            projection, action_hint=action_hint,
            active_statework_ids=snapshot.active_stateworks)

    def _csw_snapshot(self, *, candidate_statework_ids: Sequence[str] | None = None
                      ) -> CSWBehaviorSnapshot:
        return self.csw.behavioral_snapshot(
            candidate_statework_ids=(None if candidate_statework_ids is None
                                     else tuple(candidate_statework_ids)))

    @staticmethod
    def _candidate_statework_ids(snapshot: CSWBehaviorSnapshot) -> tuple[str, ...]:
        """Keep mutations inside CSW's own authoritative snapshot boundary."""
        values = {item.statework_id for item in snapshot.eligible_stateworks}
        values.update(snapshot.active_stateworks)
        return tuple(sorted(values))

    def project_behavior_context(self, state: BehaviorState | None = None,
                                 snapshot: CSWBehaviorSnapshot | None = None) -> str:
        state = state or self.state_store.load(self.scope).state
        snapshot = snapshot or self._csw_snapshot()
        payload = {
            "revision": state.revision,
            "objective": self.objective,
            "objective_criteria": list(self.objective_criteria),
            "focus_criteria": list(state.focus_criteria or self.objective_criteria),
            "cognitive_mode": state.cognitive_mode,
            "framework_modes": dict(state.framework_modes),
            "controls": {
                "verification_intensity": state.verification_intensity,
                "exploration_budget": state.exploration_budget,
                "retry_policy": state.retry_policy,
                "recovery_level": state.recovery_level,
                "context_strategy": state.context_strategy,
                "response_strategy": state.response_strategy,
            },
            "csw_active_stateworks": list(snapshot.active_stateworks),
            "csw_selection_revision": snapshot.revision,
            "csw_snapshot_id": snapshot.snapshot_id,
            "csw_workflow_states": dict(snapshot.workflow_states),
            "csw_workflow_revisions": dict(snapshot.workflow_revisions),
            "csw_eligible_steps": {
                key: list(value) for key, value in snapshot.eligible_steps.items()},
            "csw_eligible_stateworks": [item.to_dict()
                                         for item in snapshot.eligible_stateworks],
            "active_interventions": [item.to_dict()
                                     for item in state.active_interventions],
        }
        return ("===== DIGITAL PSYCHOLOGY BEHAVIOR STATE =====\n"
                "Host-selected behavioral guidance; CFW/WARD/CSW authority gates remain active.\n"
                + json.dumps(payload, sort_keys=True, separators=(",", ":")))

    def _assess_ready_interventions(
            self, record: RuntimeStateRecord,
            observation: RuntimeObservation) -> list[InterventionOutcome]:
        results = []
        applied_ids = tuple(sorted(record.application_receipts))
        competing_ids = tuple(sorted(item.intervention_id
                                      for item in record.state.active_interventions))
        next_revision = record.state.revision + 1
        for intervention in record.state.active_interventions:
            elapsed = observation.action_index - intervention.baseline_action_index
            complete = elapsed >= int(intervention.evaluation_horizon_actions or
                                      intervention.expires_after_actions)
            if not observation.evidence_refs and not complete:
                continue
            frame = InterventionEvaluationFrame(
                scope=self.scope,
                observed_revision=next_revision,
                action_index=observation.action_index,
                evaluation_complete=complete,
                applied_intervention_ids=applied_ids,
                competing_intervention_ids=tuple(
                    item for item in competing_ids
                    if item != intervention.intervention_id),
                evidence_refs=observation.evidence_refs,
            )
            assessed = self.controller.assess_intervention_outcome(
                intervention, frame,
                evidence_authority=self.outcome_evidence_authority)
            result = InterventionOutcome.from_mapping(assessed)
            # The outcome evaluator may see the same evidence for multiple
            # active interventions. IDs and scope stay tied to each record.
            results.append(result)
        return results

    @staticmethod
    def _parse_decision(raw: Mapping[str, Any]) -> BehavioralDecision:
        state = BehaviorState.from_mapping(raw["state"])
        directives = tuple(BehaviorDirective(**dict(item))
                           for item in raw.get("directives") or ())
        outcomes = tuple(InterventionOutcome.from_mapping(item)
                         for item in raw.get("intervention_outcomes") or ())
        return BehavioralDecision(
            decision_id=str(raw["decision_id"]), scope=str(raw["scope"]),
            expected_revision=int(raw["expected_revision"]), state=state,
            directives=directives,
            preferred_stateworks=tuple(raw.get("preferred_stateworks") or ()),
            intervention_outcomes=outcomes, reason=str(raw.get("reason") or ""),
        )

    @staticmethod
    def _with_authoritative_stateworks(decision: BehavioralDecision,
                                       snapshot: CSWBehaviorSnapshot
                                       ) -> BehavioralDecision:
        state = BehaviorState.from_mapping({
            **decision.state.to_dict(),
            "active_stateworks": list(snapshot.active_stateworks),
        })
        return BehavioralDecision(
            decision_id=decision.decision_id, scope=decision.scope,
            expected_revision=decision.expected_revision, state=state,
            directives=decision.directives,
            preferred_stateworks=decision.preferred_stateworks,
            intervention_outcomes=decision.intervention_outcomes,
            reason=decision.reason,
        )

    @staticmethod
    def _new_intervention_receipts(
            prior: BehaviorState, decision: BehavioralDecision,
            applied_directive_ids: set[str]) -> dict[str, tuple[str, ...]]:
        old_ids = {item.intervention_id for item in prior.active_interventions}
        receipts = {}
        for intervention in decision.state.active_interventions:
            if intervention.intervention_id in old_ids:
                continue
            directives = [item for item in decision.directives
                          if item.intervention_id == intervention.intervention_id]
            ids = tuple(sorted(item.directive_id for item in directives
                               if item.directive_id in applied_directive_ids))
            if not directives or len(ids) != len(directives):
                raise RuntimeError("new intervention lacks a complete directive receipt")
            receipts[intervention.intervention_id] = ids
        return receipts

    def _event_context(self) -> InterventionEventContext:
        identity = self.identity
        return InterventionEventContext(
            namespace_id=identity.namespace_id,
            application_id=identity.application_id,
            application_instance_id=identity.application_instance_id,
            agent_instance_id=identity.agent_instance_id,
            session_id=identity.session_id, task_id=identity.task_id,
            attempt_id=identity.attempt_id, segment_id=identity.segment_id,
            agent_id=identity.agent_id,
            behavioral_subject=identity.behavioral_subject,
            goal_contract_hash=identity.goal_contract_hash,
        )

    def _system_one_identity(self) -> SystemOneIdentity:
        identity = self.identity
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


__all__ = ["RuntimeSession", "RuntimeTurnResult"]
