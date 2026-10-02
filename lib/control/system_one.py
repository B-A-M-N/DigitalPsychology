"""Build bounded semantic frames for DP's optional System-1 provider."""
from __future__ import annotations

import hashlib
from typing import Any, Mapping, Sequence

from ..behavior_state import (BehaviorState, CSWBehaviorSnapshot,
                              FrameworkSignal, GoalSetSnapshot)
from .candidate_generator import BehaviorCandidateGenerator, CandidateConditions
from .semantic_query_planner import SemanticQueryPlanner
from .statework_selector import (eligible_statework_descriptions,
                                 statework_ambiguity)
from ..protocol_artifact import QUESTION_REGISTRY
from ..runtime.contracts import RuntimeIdentity, RuntimeObservation
from ..system_one_contracts import (ActivityFact, CriterionState,
                                    QuestionSpec, SemanticState, SystemOneFrame,
                                    SystemOneIdentity, WorkflowFact, canonical_hash)


def _semantic_criterion_ids(attention: Sequence[str], candidate: Any,
                            goals: GoalSetSnapshot | None,
                            signals: Sequence[FrameworkSignal],
                            state: BehaviorState) -> tuple[str, ...]:
    """Pick an eight-item semantic view by decision relevance and starvation age."""
    selected: list[str] = []

    def add(values: Sequence[str], *, limit: int | None = None) -> None:
        for value in values[:limit] if limit is not None else values:
            if value and value not in selected and len(selected) < 8:
                selected.append(value)

    attention_ids = tuple(attention)
    candidate_ids = tuple(candidate.affected_criterion_ids if candidate else ())
    add(attention_ids)
    if goals is None:
        add(candidate_ids)
        return tuple(selected)

    runnable = goals.runnable_items
    ages = state.attention_last_seen
    oldest = sorted(
        (item for item in runnable if item.criterion_id not in selected),
        key=lambda item: (ages.get(item.criterion_id, -1), -item.priority,
                          item.criterion_id),
    )
    relevant = [signal.criterion_id for signal in signals
                if signal.criterion_id is not None]
    relevant.extend(item.criterion_id for item in sorted(
        (item for item in goals.items
         if item.status in {"BLOCKED", "DEPENDENCY_BLOCKED"}),
        key=lambda item: (ages.get(item.criterion_id, -1), -item.priority,
                          item.criterion_id)))
    relevant = list(dict.fromkeys(item for item in relevant if item not in selected))
    reserve = min(1, len(oldest)) + min(1, len(relevant))
    add(candidate_ids, limit=max(0, 8 - len(selected) - reserve))
    add(tuple(item.criterion_id for item in oldest), limit=1)
    add(tuple(relevant), limit=1)
    add(candidate_ids)
    add(tuple(item.criterion_id for item in oldest))
    return tuple(selected)


def _deliverable_kind(policy_context: Mapping[str, Any]) -> str:
    allowed = {"visual_ui", "code_behavior", "document", "architecture",
               "test_suite", "infrastructure", "analysis"}
    aliases = {
        "code_change": "code_behavior", "code": "code_behavior",
        "configuration": "code_behavior", "deployment": "infrastructure",
        "research": "analysis", "ui": "visual_ui",
    }
    for key in ("deliverable_kind", "task_kind"):
        value = policy_context.get(key)
        if isinstance(value, str):
            normalized = aliases.get(value.strip().lower(), value.strip().lower())
            if normalized in allowed:
                return normalized
    return "analysis"


def create_system_one_frame_factory(system_one_service: Any):
    """Return a provider-neutral frame factory bound to pinned provider metadata."""
    if getattr(system_one_service, "mode", "disabled") == "disabled":
        return None
    capability = system_one_service.capability()
    def field(name: str):
        return (getattr(capability, name) if hasattr(capability, name)
                else capability[name])
    enabled = set(system_one_service.enabled_question_families)
    supported = set(field("supported_question_families"))
    available_questions = tuple(item for item in (
        "action_relation", "progress_relation", "retry_relation", "goal_drift",
        "route_preference")
        if item in enabled and item in supported)
    if not available_questions:
        return None
    query_planner = SemanticQueryPlanner()
    candidate_generator = BehaviorCandidateGenerator()
    def build(identity: RuntimeIdentity, objective: str,
              attention_criteria: Sequence[str], behavior_state: BehaviorState,
              observation: RuntimeObservation, csw: CSWBehaviorSnapshot,
              cfw_signals: Sequence[FrameworkSignal],
              policy_context: Mapping[str, Any] | None = None) -> SystemOneFrame | None:
        signals = tuple(signal for signal in cfw_signals if signal.active)
        signal_codes = tuple((signal.source + "." + signal.code).lower()
                             for signal in signals)
        retry_signal = any(any(token in code for token in (
            "retry", "repeat", "same_approach", "approach_failed", "equivalent"))
                               for code in signal_codes)
        drift_signal = any(any(token in code for token in (
            "drift", "unrelated", "support_work", "criterion_mismatch", "busywork"))
                               for code in signal_codes)
        candidate = observation.candidate_action
        retry_count = observation.equivalent_retry_count
        progress_gap = observation.actions_since_goal_progress
        if observation.goal_set is not None:
            criterion_ids = (candidate.affected_criterion_ids if candidate is not None
                             else tuple(item.criterion_id
                                        for item in observation.goal_set.runnable_items))
            relevant = tuple(item for item in observation.goal_set.items
                             if item.criterion_id in criterion_ids)
            retry_count = max(retry_count, *(item.equivalent_retry_count for item in relevant), 0)
            progress_gap = max(progress_gap, *(item.actions_since_progress for item in relevant), 0)
        uncertain_dimensions: list[str] = []
        candidate_action_uncertain = bool(
            candidate is not None
            and (candidate.relation_certainty == "unknown" or candidate.external
                 or (candidate.mutating and not candidate.reversible)))
        retry_ambiguous = (retry_count == 1 or (retry_signal and retry_count < 2))
        progress_ambiguous = bool(
            observation.action_completed and not observation.progress_made
            and not observation.regression_observed
            and (observation.artifact_changed or observation.evidence_refs))
        if candidate_action_uncertain or drift_signal:
            uncertain_dimensions.append("action_relation")
        if retry_ambiguous:
            uncertain_dimensions.append("retry_relation")
        if progress_ambiguous:
            uncertain_dimensions.append("progress_relation")
        if drift_signal and progress_gap >= query_planner.support_action_threshold:
            uncertain_dimensions.append("goal_drift")
        legal_stateworks = eligible_statework_descriptions(
            csw.eligible_stateworks, csw.active_stateworks)
        statework_policy_ambiguity = statework_ambiguity(
            behavior_state.cognitive_mode, csw.eligible_stateworks,
            csw.active_stateworks)
        pool = candidate_generator.generate(
            behavior_state,
            next_focus=tuple(attention_criteria[:8]),
            conditions=CandidateConditions(
                retry_ambiguity=retry_ambiguous,
                action_relation_uncertain=candidate_action_uncertain,
                progress_relation_uncertain=progress_ambiguous,
                trigger_codes=tuple(signal.code for signal in signals),
            ),
            csw_snapshot=csw,
            available_frameworks=("owl", "anchor", "fuse", "ward", "flow",
                                 "sispis", "dox"),
        )
        ambiguity = (pool.ambiguity(tuple(dict.fromkeys(uncertain_dimensions)))
                     if uncertain_dimensions else statework_policy_ambiguity)
        query = query_planner.plan(
            observation, cfw_signals=cfw_signals,
            policy_ambiguity=ambiguity)
        if not query.should_query or query.question_id not in available_questions:
            return None
        goals = observation.goal_set
        goal_items = ({item.criterion_id: item for item in goals.items}
                      if goals is not None else {})
        criterion_ids = _semantic_criterion_ids(
            attention_criteria, candidate, goals, signals, behavior_state)
        status_map = {
            "READY": "unmet", "ACTIVE": "active", "VERIFIED": "satisfied",
            "BLOCKED": "blocked", "DEPENDENCY_BLOCKED": "blocked",
            "DEFERRED_BY_OPERATOR": "blocked",
        }
        criteria = tuple(CriterionState(
            criterion_id=criterion_id,
            claim=criterion_id[:512],
            kind="objective",
            status=status_map.get(goal_items[criterion_id].status, "unmet")
            if criterion_id in goal_items else "unmet",
            evidence_status=("available" if observation.evidence_refs else "none"),
            evidence_ref_digests=tuple(hashlib.sha256(ref.encode()).hexdigest()
                                       for ref in observation.evidence_refs[:8]),
            last_progress_sequence=(goal_items[criterion_id].last_progress_action
                                    if criterion_id in goal_items else None),
        ) for criterion_id in criterion_ids)
        progress_class = ("GOAL_PROGRESS" if observation.progress_made else
                          "VALIDATION_EVIDENCE" if observation.evidence_refs else None)
        sequence = (observation.action_index * 2
                    + (1 if not observation.action_completed else 2)
                    + behavior_state.revision * 2)
        activity = ActivityFact(
            sequence=sequence,
            activity_type="action",
            source="model_proposed" if candidate else "host_observed",
            action_class=(candidate.action_class if candidate else "host_action"),
            host_declared_relation=(
                "ambiguous" if candidate else
                "advances_primary_goal" if observation.progress_made else "ambiguous"),
            target_kind=candidate.target_kind if candidate else "workspace",
            target_ref_digest=(candidate.target_ref_digest if candidate else None),
            proposed_purpose=(candidate.proposed_purpose if candidate
                              else objective[:256]),
            proposed_purpose_source="model_proposed" if candidate else "derived",
            affected_criterion_ids=(
                candidate.affected_criterion_ids if candidate
                else tuple(attention_criteria[:8])),
            relation_certainty=(candidate.relation_certainty if candidate else "inferred"),
            result_class=(None if candidate else
                          "regression" if observation.regression_observed else "completed"),
            artifact_changed=(None if candidate else observation.artifact_changed),
            progress_class=None if candidate else progress_class,
        )
        # Never substitute a different enabled question for a planned query:
        # calibration and operator selection remain specific to each family.
        question_ids = (query.question_id,)
        workflows = tuple(WorkflowFact(
            statework_id=statework_id,
            current_state=csw.workflow_states.get(statework_id, "UNKNOWN"),
            progress_class="LATERAL",
            revision=csw.workflow_revisions.get(statework_id),
        ) for statework_id in csw.active_stateworks[:8])
        work_mode = ("diagnose" if behavior_state.cognitive_mode in {
            "DIAGNOSE", "REINSPECT", "RECOVER", "REPLAN"} else "delivery")
        deliverable_kind = _deliverable_kind(policy_context or {})
        semantic_state = SemanticState(
            task={
                "objective": objective[:1024],
                "work_mode": work_mode,
                "deliverable_kind": deliverable_kind,
                "task_complexity": "complex" if len(goal_items) > 8 else "standard",
                "phase": behavior_state.cognitive_mode.lower(),
                "active_segment": identity.segment_id[:128],
                "active_statework": csw.active_stateworks[0][:64]
                if csw.active_stateworks else None,
                "active_flow": None,
                "non_goals": [],
            },
            situation={
                "current_state": behavior_state.cognitive_mode,
                "blockers": [signal.code for signal in cfw_signals
                             if signal.active and signal.severity == "critical"][:8],
                "actions_since_goal_progress": observation.actions_since_goal_progress,
                "actions_since_artifact_change": observation.actions_since_artifact_change,
                "verification_actions_since_artifact_change": (
                    observation.verification_actions_since_artifact_change),
                "recovery_epochs": behavior_state.recovery_level,
                "repeated_branch_count": observation.equivalent_retry_count,
            },
            criteria=criteria,
            activity=(activity,),
            workflows=workflows,
            convergence=(),
            candidate=activity,
            legal_routes=tuple(statework_id for statework_id, _description
                               in legal_stateworks),
        )
        questions = tuple(QuestionSpec(
            question_id=question_id,
            family=QUESTION_REGISTRY[question_id]["family"],
            question_type=QUESTION_REGISTRY[question_id]["type"],
            instructions=QUESTION_REGISTRY[question_id]["instructions"],
            criteria=(legal_stateworks if question_id == "route_preference"
                      else tuple(QUESTION_REGISTRY[question_id]["criteria"].items())),
        ) for question_id in question_ids)
        state_digest = canonical_hash(semantic_state.to_dict())
        question_digest = canonical_hash([item.to_dict() for item in questions])
        supplied_policy_hash = identity.goal_contract_hash or ""
        policy_hash = (supplied_policy_hash if len(supplied_policy_hash) == 64
                       and all(char in "0123456789abcdef"
                               for char in supplied_policy_hash)
                       else canonical_hash({"identity": identity.to_dict(),
                                            "objective": objective,
                                            "criteria": list(criterion_ids)}))
        system_identity = SystemOneIdentity(
            namespace_id=identity.namespace_id,
            application_id=identity.application_id,
            application_instance_id=identity.application_instance_id,
            agent_id=identity.agent_id,
            agent_instance_id=identity.agent_instance_id,
            session_id=identity.session_id,
            task_id=identity.task_id,
            attempt_id=identity.attempt_id,
        )
        request_id = "sys1-" + canonical_hash({
            "identity": system_identity.to_dict(), "sequence": sequence,
            "state_digest": state_digest, "question_digest": question_digest,
        })[:32]
        request_digest = canonical_hash({
            "identity": system_identity.to_dict(),
            "sequence": sequence,
            "state_digest": state_digest,
            "question_bundle_digest": question_digest,
            "policy_hash": policy_hash,
        })
        return SystemOneFrame(
            identity=system_identity,
            request_id=request_id,
            sequence=sequence,
            state_digest=state_digest,
            question_bundle_digest=question_digest,
            request_digest=request_digest,
            policy_hash=policy_hash,
            backend_id=field("backend_id"),
            model_id=field("model_id"),
            model_revision=field("model_revision"),
            model_content_hash=field("model_content_hash"),
            provider_protocol_hash=field("provider_protocol_hash"),
            calibration_bundle_hash=system_one_service.calibration_bundle_hash,
            state=semantic_state,
            questions=questions,
        )

    return build


__all__ = ["create_system_one_frame_factory"]
