"""Build bounded semantic frames for DP's optional System-1 provider."""
from __future__ import annotations

import hashlib
from typing import Any, Sequence

from ..behavior_state import (BehaviorState, CSWBehaviorSnapshot,
                              FrameworkSignal, GoalSetSnapshot)
from ..protocol_artifact import QUESTION_REGISTRY
from ..runtime.contracts import RuntimeIdentity, RuntimeObservation
from ..system_one_contracts import (ActivityFact, CriterionState,
                                    QuestionSpec, SemanticState, SystemOneFrame,
                                    SystemOneIdentity, WorkflowFact, canonical_hash)


def create_system_one_frame_factory(system_one_service: Any):
    """Return a provider-neutral frame factory bound to pinned provider metadata."""
    capability = system_one_service.capability()
    def field(name: str):
        return (getattr(capability, name) if hasattr(capability, name)
                else capability[name])
    enabled = set(system_one_service.enabled_question_families)
    supported = set(field("supported_question_families"))
    available_questions = tuple(item for item in (
        "action_relation", "progress_relation", "retry_relation", "goal_drift")
        if item in enabled and item in supported)
    if not available_questions:
        return None
    def build(identity: RuntimeIdentity, objective: str,
              attention_criteria: Sequence[str], behavior_state: BehaviorState,
              observation: RuntimeObservation, csw: CSWBehaviorSnapshot,
              cfw_signals: Sequence[FrameworkSignal]) -> SystemOneFrame:
        goals = observation.goal_set
        goal_items = ({item.criterion_id: item for item in goals.items}
                      if goals is not None else {})
        criterion_ids = tuple(dict.fromkeys(
            tuple(attention_criteria) + tuple(item.criterion_id for item in
                                              (goals.items if goals else ()))))[:8]
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
        candidate = observation.candidate_action
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
            result_class=(None if candidate else
                          "regression" if observation.regression_observed else "completed"),
            artifact_changed=(None if candidate else observation.artifact_changed),
            progress_class=None if candidate else progress_class,
        )
        # Laya's local checkpoint is a comparatively expensive CPU model. Ask
        # the single question with the highest value for this boundary instead
        # of batching every semantic family into every inference. This keeps
        # inference within its host deadline and makes the requested judgment
        # specific to the evidence currently available.
        if candidate is not None:
            preferred_question = "action_relation"
        elif observation.equivalent_retry_count > 0 or any(
                signal.active and any(token in signal.code.lower()
                                      for token in ("retry", "repeat", "loop"))
                for signal in cfw_signals):
            preferred_question = "retry_relation"
        elif (observation.progress_made or observation.regression_observed
              or observation.artifact_changed or observation.evidence_refs):
            preferred_question = "progress_relation"
        else:
            preferred_question = "goal_drift"
        question_id = (preferred_question if preferred_question in available_questions
                       else available_questions[0])
        question_ids = (question_id,)
        workflows = tuple(WorkflowFact(
            statework_id=statework_id,
            current_state=csw.workflow_states.get(statework_id, "UNKNOWN"),
            progress_class="LATERAL",
            revision=csw.workflow_revisions.get(statework_id),
        ) for statework_id in csw.active_stateworks[:8])
        work_mode = ("diagnose" if behavior_state.cognitive_mode in {
            "DIAGNOSE", "REINSPECT", "RECOVER", "REPLAN"} else "delivery")
        semantic_state = SemanticState(
            task={
                "objective": objective[:1024],
                "work_mode": work_mode,
                "deliverable_kind": "analysis",
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
                "actions_since_artifact_change": 0,
                "verification_actions_since_artifact_change": 0,
                "recovery_epochs": behavior_state.recovery_level,
                "repeated_branch_count": observation.equivalent_retry_count,
            },
            criteria=criteria,
            activity=(activity,),
            workflows=workflows,
            convergence=(),
            candidate=activity,
            legal_routes=(),
        )
        questions = tuple(QuestionSpec(
            question_id=question_id,
            family=QUESTION_REGISTRY[question_id]["family"],
            question_type=QUESTION_REGISTRY[question_id]["type"],
            instructions=QUESTION_REGISTRY[question_id]["instructions"],
            criteria=tuple(QUESTION_REGISTRY[question_id]["criteria"].items()),
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
