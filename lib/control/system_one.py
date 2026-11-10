"""Build bounded semantic frames for DP's optional System-1 provider."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence

from ..behavior_state import (BehaviorState, CSWBehaviorSnapshot,
                              FrameworkSignal, GoalSetSnapshot,
                              group_behavioral_causes)
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


DELIVERABLE_KINDS = ("visual_ui", "code_behavior", "document", "architecture",
                     "test_suite", "infrastructure", "analysis")

_DELIVERABLE_ALIASES = {
    "code_change": "code_behavior", "code": "code_behavior",
    "configuration": "code_behavior", "deployment": "infrastructure",
    "research": "analysis", "ui": "visual_ui",
}


class DeliverableKindRequired(ValueError):
    """The semantic protocol has no ``unknown`` task kind, and the host omitted it.

    Defaulting to ``analysis`` silently mislabels a coding task and degrades the
    provider's classification. The protocol offers no unknown value, so the only
    honest options are to supply the real kind or to not consult a provider.
    """


def _deliverable_kind(policy_context: Mapping[str, Any], *,
                      provider_enabled: bool = False) -> str | None:
    """Return the host-declared deliverable kind.

    With a semantic provider enabled this is mandatory. Silently labelling a
    coding task as ``analysis`` is worse than not asking: the provider would
    classify against task semantics the host never claimed.
    """
    allowed = set(DELIVERABLE_KINDS)
    for key in ("deliverable_kind", "task_kind"):
        value = policy_context.get(key)
        if isinstance(value, str):
            normalized = _DELIVERABLE_ALIASES.get(value.strip().lower(),
                                                   value.strip().lower())
            if normalized in allowed:
                return normalized
    if provider_enabled:
        raise DeliverableKindRequired(
            "a semantic provider requires the host to declare deliverable_kind "
            "or task_kind; DP will not inject a guessed task kind")
    return None


def create_system_one_frame_factory(system_one_service: Any):
    """Return host query planning plus optional pinned-provider frame factories."""
    portfolio = (system_one_service is not None
                 and bool(getattr(system_one_service, "is_portfolio", False)))
    provider_enabled = (system_one_service is not None
                        and getattr(system_one_service, "mode", "disabled") != "disabled")
    capabilities = None
    if portfolio and provider_enabled:
        portfolio_object = getattr(system_one_service, "portfolio", None)
        capabilities = [runtime.capability() for runtime in
                        getattr(portfolio_object, "runtimes", ())]
    capability = (system_one_service.capability() if provider_enabled
                  and not portfolio else None)
    def field(name: str):
        if capabilities is not None:
            return [getattr(item, name) if item is not None and hasattr(item, name)
                    else item[name] for item in capabilities]
        if capability is None:
            return "disabled"
        return (getattr(capability, name) if hasattr(capability, name)
                else capability[name])
    enabled = (set(system_one_service.enabled_question_families)
               if provider_enabled else set())
    supported = ({family for item in capabilities
                  for family in (item.supported_question_families
                                 if hasattr(item, "supported_question_families")
                                 else item["supported_question_families"])}
                 if capabilities is not None else
                 set(field("supported_question_families")) if provider_enabled else set())
    effective_enabled_questions = sorted(enabled & supported)
    available_questions = tuple(item for item in (
        "action_relation", "progress_relation", "retry_relation", "goal_drift",
        "route_preference", "decomposition_preference",
        "integration_preference", "test_strategy_preference")
        if item in effective_enabled_questions)
    capability_payload = (
        None if capability is None else
        capability.to_dict() if callable(getattr(capability, "to_dict", None))
        else dict(capability))
    if capabilities is not None:
        capability_payload = [item.to_dict() if hasattr(item, "to_dict") else item
                              for item in capabilities]
    query_planner = SemanticQueryPlanner()
    candidate_generator = BehaviorCandidateGenerator()
    def build(identity: RuntimeIdentity, objective: str,
              attention_criteria: Sequence[str], behavior_state: BehaviorState,
              observation: RuntimeObservation, csw: CSWBehaviorSnapshot,
              cfw_signals: Sequence[FrameworkSignal],
              policy_context: Mapping[str, Any] | None = None,
              available_frameworks: Sequence[str] = (),
              previous_goal_set: GoalSetSnapshot | None = None
              ) -> SystemOneFrame | None:
        signals = tuple(signal for signal in cfw_signals if signal.active)
        causes = group_behavioral_causes(signals)
        signal_codes = tuple((source + "." + code).lower()
                             for cause in causes for source in cause.sources
                             for code in cause.codes)
        retry_signal = any(any(token in code for token in (
            "retry", "repeat", "same_approach", "approach_failed", "equivalent"))
                               for code in signal_codes)
        retry_signal = retry_signal or any(any(token in code.lower() for token in (
            "retry", "repeat", "same_approach", "approach_failed", "equivalent"))
            for code in observation.active_trigger_codes)
        drift_signal = any(any(token in code for token in (
            "drift", "unrelated", "support_work", "criterion_mismatch", "busywork"))
                               for code in signal_codes)
        drift_signal = drift_signal or any(any(token in code.lower() for token in (
            "drift", "unrelated", "support_work", "criterion_mismatch", "busywork"))
            for code in observation.active_trigger_codes)
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
            and (candidate.external
                 or (candidate.mutating and (
                     candidate.relation_certainty == "unknown"
                     or not candidate.reversible))))
        retry_ambiguous = (retry_count == 1 or (retry_signal and retry_count < 2))
        progress_ambiguous = bool(
            observation.action_completed and not observation.progress_made
            and not observation.regression_observed
            and (observation.artifact_changed or observation.evidence_refs))
        candidate_drift_uncertain = bool(
            drift_signal and (candidate is None or candidate.mutating or candidate.external))
        if candidate_action_uncertain or candidate_drift_uncertain:
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
                trigger_codes=tuple(dict.fromkeys(
                    code for cause in causes for code in cause.codes)),
            ),
            csw_snapshot=csw,
            available_frameworks=tuple(available_frameworks),
        )
        ambiguity = (pool.ambiguity(tuple(dict.fromkeys(uncertain_dimensions)))
                     if uncertain_dimensions else statework_policy_ambiguity)
        query = query_planner.plan(
            observation, cfw_signals=cfw_signals,
            policy_ambiguity=ambiguity)
        # The host control engine reads this bounded planner result even when
        # no provider frame is emitted. This separates a real query opportunity
        # from a provider call and from a usable answer in per-session telemetry.
        build.last_query_plan = query
        if not query.should_query or query.question_id not in available_questions:
            return None
        goals = observation.goal_set
        goal_items = ({item.criterion_id: item for item in goals.items}
                      if goals is not None else {})
        criterion_ids = _semantic_criterion_ids(
            attention_criteria, candidate, goals, signals, behavior_state)
        raw_claims = (policy_context or {}).get("criterion_claims", {})
        if (not isinstance(raw_claims, Mapping) or len(raw_claims) > 256
                or any(not isinstance(key, str) or not key or len(key) > 128
                       or not isinstance(value, str) or not value.strip()
                       or len(value) > 512 for key, value in raw_claims.items())):
            raise ValueError("host criterion claims must be a bounded mapping")

        def criterion_claim(criterion_id: str) -> str | None:
            goal = goal_items.get(criterion_id)
            claim = goal.claim if goal is not None else ""
            claim = claim or raw_claims.get(criterion_id, "")
            return claim.strip() or None

        if criterion_ids and any(criterion_claim(item) is None
                                 for item in criterion_ids):
            # Criterion IDs alone are not claim text. Skip semantic inference
            # when the trusted host did not supply the criterion descriptions.
            return None
        status_map = {
            "READY": "unmet", "ACTIVE": "active", "VERIFIED": "satisfied",
            "BLOCKED": "blocked", "DEPENDENCY_BLOCKED": "blocked",
            "DEFERRED_BY_OPERATOR": "blocked",
        }

        def criterion_evidence_refs(criterion_id: str) -> tuple[str, ...]:
            return (observation.criterion_evidence_refs.get(criterion_id)
                    or (goal_items[criterion_id].evidence_refs
                        if criterion_id in goal_items else ()))

        criteria = tuple(CriterionState(
            criterion_id=criterion_id,
            claim=(criterion_claim(criterion_id) or "")[:512],
            kind="objective",
            status=status_map.get(goal_items[criterion_id].status, "unmet")
            if criterion_id in goal_items else "unmet",
            evidence_status=(
                "verified" if (
                    goal_items.get(criterion_id) is not None
                    and goal_items[criterion_id].status == "VERIFIED"
                    and criterion_evidence_refs(criterion_id))
                else "available" if criterion_evidence_refs(criterion_id)
                else "none"),
            evidence_ref_digests=tuple(hashlib.sha256(ref.encode()).hexdigest()
                                       for ref in criterion_evidence_refs(
                                           criterion_id)[:8]),
            last_progress_sequence=(goal_items[criterion_id].last_progress_action
                                    if criterion_id in goal_items else None),
        ) for criterion_id in criterion_ids)
        criteria_before = None
        if previous_goal_set is not None:
            previous_items = {item.criterion_id: item for item in previous_goal_set.items}
            missing_prior = set(criterion_ids) - set(previous_items)
            if missing_prior:
                raise ValueError(
                    "previous host goal set omits projected criteria: "
                    + ", ".join(sorted(missing_prior)))
            if all(previous_items[item].claim for item in criterion_ids):
                criteria_before = tuple(CriterionState(
                    criterion_id=criterion_id,
                    claim=previous_items[criterion_id].claim[:512],
                    kind="objective",
                    status=status_map.get(previous_items[criterion_id].status, "unmet"),
                    evidence_status=(
                        "verified" if (
                            previous_items[criterion_id].status == "VERIFIED"
                            and previous_items[criterion_id].evidence_refs)
                        else "available" if previous_items[criterion_id].evidence_refs
                        else "none"),
                    evidence_ref_digests=tuple(
                        hashlib.sha256(ref.encode()).hexdigest()
                        for ref in previous_items[criterion_id].evidence_refs[:8]),
                    last_progress_sequence=(
                        previous_items[criterion_id].last_progress_action),
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
                candidate.host_declared_relation if candidate else
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
        options = {item.statework_id: item for item in csw.eligible_stateworks}
        workflow_ids = tuple(dict.fromkeys((
            *csw.active_stateworks,
            *(item.statework_id for item in csw.eligible_stateworks))))[:8]
        workflows = tuple(WorkflowFact(
            statework_id=statework_id,
            domain=(options[statework_id].domain or None
                    if statework_id in options else None),
            purpose=(options[statework_id].purpose or None
                     if statework_id in options else None),
            current_state=csw.workflow_states.get(statework_id, "UNKNOWN"),
            previous_state=csw.workflow_previous_states.get(statework_id),
            progress_class=csw.workflow_progress_classes.get(statework_id),
            revision=csw.workflow_revisions.get(statework_id),
            legal_next_states=csw.legal_next_states.get(statework_id, ()),
            active=statework_id in csw.active_stateworks,
            eligible=statework_id in options,
        ) for statework_id in workflow_ids)
        declared_work_mode = (policy_context or {}).get("work_mode")
        work_mode = (declared_work_mode if declared_work_mode in {
            "delivery", "diagnose", "review", "research"} else
            "diagnose" if behavior_state.cognitive_mode in {
                "DIAGNOSE", "REINSPECT", "RECOVER", "REPLAN"} else "delivery")
        deliverable_kind = _deliverable_kind(
            policy_context or {}, provider_enabled=True)
        raw_non_goals = (policy_context or {}).get("non_goals", ())
        if (not isinstance(raw_non_goals, (tuple, list)) or len(raw_non_goals) > 8
                or any(not isinstance(item, str) or not item.strip() or len(item) > 256
                       for item in raw_non_goals)
                or len(set(raw_non_goals)) != len(raw_non_goals)):
            raise ValueError("host non-goals must be unique bounded text")
        raw_domain_tags = (policy_context or {}).get("domain_tags", ())
        if (not isinstance(raw_domain_tags, (tuple, list)) or len(raw_domain_tags) > 16
                or any(not isinstance(item, str) or not item.strip() or len(item) > 128
                       for item in raw_domain_tags)
                or len(set(raw_domain_tags)) != len(raw_domain_tags)):
            raise ValueError("host domain tags must be unique bounded text")
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
                "non_goals": list(raw_non_goals),
                "domain_tags": list(raw_domain_tags),
            },
            situation={
                "current_state": behavior_state.cognitive_mode,
                "blockers": ([
                    (f"cfw_cause={cause.cause_id[:48]};"
                     f"src={','.join(cause.sources)[:40]};"
                     f"codes={','.join(cause.codes)[:56]};"
                     f"actions={','.join(cause.required_actions)[:32]};"
                     f"criteria={','.join(cause.criterion_ids)[:32]};"
                     f"refs={','.join(cause.evidence_refs)[:24]};"
                     f"scope={','.join(cause.scopes)[:20]};"
                     f"subject={','.join(cause.subject_refs)[:20]}")[:256]
                    for cause in causes[:6]
                ] + [
                    (f"host_semantic_trigger={code[:128]};"
                     f"refs={','.join(observation.evidence_refs[:2])[:64]}")[:256]
                    for code in observation.active_trigger_codes
                    if any(token in code.lower() for token in (
                        "retry", "repeat", "approach_failed", "same_approach",
                        "drift", "unrelated", "support_work", "busywork"))
                ])[:8],
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
            failed_approaches=observation.failed_approaches,
            legal_routes=tuple(statework_id for statework_id, _description
                               in legal_stateworks),
            criteria_before=criteria_before,
        )
        questions = tuple(QuestionSpec(
            question_id=question_id,
            family=QUESTION_REGISTRY[question_id]["family"],
            question_type=QUESTION_REGISTRY[question_id]["type"],
            instructions=QUESTION_REGISTRY[question_id]["instructions"],
            criteria=(legal_stateworks if question_id in {
                "route_preference", "decomposition_preference",
                "integration_preference", "test_strategy_preference"}
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
        provider_count = 1 if not isinstance(field("backend_id"), list) else len(
            field("backend_id"))
        frames = []
        for provider_index in range(provider_count):
            backend_id = (field("backend_id") if provider_count == 1
                          else field("backend_id")[provider_index])
            model_id = (field("model_id") if provider_count == 1
                        else field("model_id")[provider_index])
            model_revision = (field("model_revision") if provider_count == 1
                              else field("model_revision")[provider_index])
            model_content_hash = (field("model_content_hash") if provider_count == 1
                                  else field("model_content_hash")[provider_index])
            provider_protocol_hash = (field("provider_protocol_hash") if provider_count == 1
                                      else field("provider_protocol_hash")[provider_index])
            frames.append(SystemOneFrame(
                identity=system_identity,
                request_id=request_id,
                sequence=sequence,
                state_digest=state_digest,
                question_bundle_digest=question_digest,
                request_digest=request_digest,
                policy_hash=policy_hash,
                backend_id=backend_id,
                model_id=model_id,
                model_revision=model_revision,
                model_content_hash=model_content_hash,
                provider_protocol_hash=provider_protocol_hash,
                calibration_bundle_hash=system_one_service.calibration_bundle_hash,
                state=semantic_state,
                questions=questions,
            ))
        return frames[0] if provider_count == 1 else tuple(frames)

    attestation = {
        "backend_id": (str(field("backend_id")) if provider_enabled and not portfolio
                       else json.dumps([str(item) for item in field("backend_id")])),
        "model_id": (str(field("model_id")) if provider_enabled and not portfolio
                     else json.dumps([str(item) for item in field("model_id")])),
        "model_revision": (str(field("model_revision")) if provider_enabled and not portfolio
                           else json.dumps([str(item) for item in field("model_revision")])),
        "model_content_hash": (str(field("model_content_hash")) if provider_enabled and not portfolio
                               else json.dumps([str(item) for item in field("model_content_hash")])),
        "provider_protocol_hash": (str(field("provider_protocol_hash")) if provider_enabled and not portfolio
                                   else json.dumps([str(item) for item in field("provider_protocol_hash")])),
        "calibration_bundle_hash": str(
            getattr(system_one_service, "calibration_bundle_hash", "")),
        "enabled_question_families": effective_enabled_questions,
        "provider_mode": str(getattr(system_one_service, "mode", "disabled")),
        "runtime_warmed": bool(getattr(system_one_service, "runtime_warmed", False)),
        "warmup_latency_ms": getattr(system_one_service, "warmup_latency_ms", None),
        "capability_digest": canonical_hash({
            "capability": capability_payload,
            "provider_mode": getattr(system_one_service, "mode", "disabled"),
            "enabled_question_families": effective_enabled_questions,
            "calibration_bundle_hash": getattr(
                system_one_service, "calibration_bundle_hash", ""),
        }),
    }
    build.system_one_attestation = attestation
    build.last_query_plan = None
    build.system_one_provider_enabled = bool(
        provider_enabled and effective_enabled_questions)
    return build


__all__ = ["create_system_one_frame_factory"]
