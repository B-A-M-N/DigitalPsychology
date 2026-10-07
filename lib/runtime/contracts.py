"""Typed boundaries for the DigitalPsychology runtime host.

The runtime depends on ports supplied by CFW and CSW. Those repositories do
not import or inherit the DP host, and the host never modifies their objects
at runtime.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from types import MappingProxyType
from typing import Any, Callable, Mapping, Protocol, Sequence

from ..behavior_state import (BehaviorDirective, BehaviorState,
                              BehaviorSignalBatch, CSWBehaviorSnapshot,
                              FrameworkSignal, GoalSetSnapshot,
                              StateWorkReleaseResult)
from ..system_one_contracts import AttemptSemanticDescriptor, SystemOneFrame
from ..compatibility import CFW_ADAPTER_PROTOCOL_VERSION, MAX_GOAL_CRITERIA


@dataclass(frozen=True)
class ActionCandidate:
    """Sanitized host projection of an upcoming action for DP System-1."""

    action_class: str
    proposed_purpose: str
    affected_criterion_ids: tuple[str, ...]
    action_id: str
    tool_name: str
    operation: str
    target_ref_digests: tuple[str, ...] = ()
    mutating: bool = True
    reversible: bool = False
    external: bool = False
    proposed_reason: str = ""
    approach_epoch: str = ""
    target_kind: str = "workspace"
    target_ref_digest: str | None = None
    relation_certainty: str = "explicit"

    def __post_init__(self) -> None:
        for name, value, limit in (
                ("action_class", self.action_class, 64),
                ("proposed_purpose", self.proposed_purpose, 256),
                ("target_kind", self.target_kind, 64),
                ("tool_name", self.tool_name, 128),
                ("operation", self.operation, 128)):
            if not isinstance(value, str) or not value.strip() or len(value) > limit:
                raise ValueError(f"candidate {name} must be bounded non-empty text")
        if not isinstance(self.proposed_reason, str) or len(self.proposed_reason) > 256:
            raise ValueError("candidate proposed_reason must be bounded text")
        if not isinstance(self.approach_epoch, str) or len(self.approach_epoch) > 256:
            raise ValueError("candidate approach_epoch must be bounded text")
        if any(type(value) is not bool for value in
               (self.mutating, self.reversible, self.external)):
            raise ValueError("candidate effect flags must be booleans")
        if self.relation_certainty not in {"explicit", "inferred", "unknown"}:
            raise ValueError("candidate relation_certainty must be explicit, inferred, or unknown")
        criteria = tuple(self.affected_criterion_ids)
        if (len(criteria) > 8 or len(criteria) != len(set(criteria))
                or any(not isinstance(item, str) or not item or len(item) > 128
                       for item in criteria)):
            raise ValueError("candidate criteria must be unique bounded IDs")
        if not criteria and self.relation_certainty != "unknown":
            raise ValueError(
                "criterion-free candidates require unknown relation certainty")
        if criteria and self.relation_certainty == "unknown":
            raise ValueError("unknown relation certainty cannot claim affected criteria")
        target_refs = tuple(self.target_ref_digests)
        if (len(target_refs) > 16 or len(target_refs) != len(set(target_refs))
                or any(not isinstance(value, str) or len(value) != 64
                       or any(char not in "0123456789abcdef" for char in value)
                       for value in target_refs)):
            raise ValueError("candidate target references must be bounded SHA-256 digests")
        if self.target_ref_digest is not None and (
                not isinstance(self.target_ref_digest, str)
                or len(self.target_ref_digest) != 64
                or any(char not in "0123456789abcdef"
                       for char in self.target_ref_digest)):
            raise ValueError("candidate target reference must be a SHA-256 digest")
        if (not isinstance(self.action_id, str) or not self.action_id.strip()
                or len(self.action_id) > 128):
            raise ValueError("candidate action_id must be bounded non-empty text")
        if not self.proposed_reason:
            object.__setattr__(self, "proposed_reason", self.proposed_purpose)
        object.__setattr__(self, "affected_criterion_ids", criteria)
        object.__setattr__(self, "target_ref_digests", target_refs)

    def to_dict(self) -> dict[str, Any]:
        return {"action_id": self.action_id,
                "action_class": self.action_class,
                "tool_name": self.tool_name,
                "operation": self.operation,
                "proposed_purpose": self.proposed_purpose,
                "proposed_reason": self.proposed_reason,
                "affected_criterion_ids": list(self.affected_criterion_ids),
                "target_ref_digests": list(self.target_ref_digests),
                "mutating": self.mutating,
                "reversible": self.reversible,
                "external": self.external,
                "approach_epoch": self.approach_epoch,
                "target_kind": self.target_kind,
                "target_ref_digest": self.target_ref_digest,
                "relation_certainty": self.relation_certainty}


@dataclass(frozen=True)
class RuntimeIdentity:
    namespace_id: str
    application_id: str
    application_instance_id: str
    agent_instance_id: str
    session_id: str
    task_id: str
    attempt_id: str
    workflow_id: str
    subject_ref: str
    segment_id: str
    agent_id: str
    behavioral_subject: str
    goal_contract_hash: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "namespace_id", "application_id", "application_instance_id",
            "agent_instance_id", "session_id", "task_id", "attempt_id",
            "workflow_id", "subject_ref", "segment_id", "agent_id",
            "behavioral_subject",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or len(value) > 512:
                raise ValueError(f"runtime identity {name} must be bounded text")
        if self.goal_contract_hash is not None and (
                not isinstance(self.goal_contract_hash, str)
                or len(self.goal_contract_hash) > 128):
            raise ValueError("goal contract hash must be bounded text")

    def to_dict(self) -> dict[str, str | None]:
        return {name: getattr(self, name) for name in (
            "namespace_id", "application_id", "application_instance_id",
            "agent_instance_id", "session_id", "task_id", "attempt_id",
            "workflow_id", "subject_ref", "segment_id", "agent_id",
            "behavioral_subject", "goal_contract_hash")}

    @property
    def scope(self) -> str:
        body = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return "dp-runtime-" + hashlib.sha256(body.encode("utf-8")).hexdigest()

    def csw_scope(self) -> dict[str, str]:
        return {name: str(getattr(self, name)) for name in (
            "namespace_id", "application_id", "application_instance_id",
            "workflow_id", "subject_ref")}


@dataclass(frozen=True)
class RuntimeObservation:
    """One host-produced action boundary and its authoritative evidence refs."""

    action_index: int
    action_completed: bool = True
    progress_made: bool = False
    regression_observed: bool = False
    artifact_changed: bool = False
    actions_since_artifact_change: int | None = None
    verification_actions_since_artifact_change: int | None = None
    actions_since_goal_progress: int = 0
    equivalent_retry_count: int = 0
    context_pressure: float = 0.0
    goal_set: GoalSetSnapshot | None = None
    criterion_status: Mapping[str, str] = field(default_factory=dict)
    evidence_refs: tuple[str, ...] = ()
    active_trigger_codes: tuple[str, ...] = ()
    approach_epoch: str = ""
    failed_approaches: tuple[AttemptSemanticDescriptor, ...] = ()
    candidate_action: ActionCandidate | None = None
    candidate_gate_receipts: Mapping[str, Any] | None = None
    candidate_preflight_result: str | None = None
    candidate_rejection_stage: str | None = None
    cfw_preflight_decision: str | None = None
    cfw_final_decision: str | None = None
    criterion_evidence_refs: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.action_index, int) or self.action_index < 0:
            raise ValueError("host action index must be non-negative")
        if any(not isinstance(value, bool) for value in (
                self.action_completed, self.progress_made,
                self.regression_observed, self.artifact_changed)):
            raise ValueError("runtime observation flags must be boolean")
        if (self.actions_since_goal_progress < 0 or self.equivalent_retry_count < 0
                or not 0.0 <= float(self.context_pressure) <= 1.0):
            raise ValueError("runtime observation counters are out of bounds")
        for name in ("actions_since_artifact_change",
                     "verification_actions_since_artifact_change"):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError(f"{name} must be a non-negative integer or null")
        if self.goal_set is not None and not isinstance(self.goal_set, GoalSetSnapshot):
            raise ValueError("goal_set must be a host-validated GoalSetSnapshot")
        if (self.candidate_action is not None
                and not isinstance(self.candidate_action, ActionCandidate)):
            raise ValueError("candidate_action must be a validated ActionCandidate")
        if self.candidate_action is not None and self.action_completed:
            raise ValueError("candidate_action is valid only at a pre-action boundary")
        receipts = self.candidate_gate_receipts
        normalized_receipts = None
        if receipts is not None:
            if (self.candidate_action is None or not isinstance(receipts, Mapping)
                    or set(receipts) not in ({"preflight"}, {"preflight", "final"})):
                raise ValueError("candidate gate receipts must name preflight and optional final gates")
            normalized_receipts = {}
            gate_decisions = {
                "allow", "block", "require_confirmation", "require_reinspection",
                "require_recovery", "require_changed_hypothesis", "require_goal_reanchor",
            }
            digests = set()
            for stage, raw_receipt in receipts.items():
                if not isinstance(raw_receipt, Mapping):
                    raise ValueError("candidate gate receipt must be an object")
                receipt = dict(raw_receipt)
                expected = {"authority_id", "adapter_kind", "adapter_protocol_version",
                            "stage", "action_digest", "decision", "reason_codes"}
                if set(receipt) != expected or receipt["stage"] != stage:
                    raise ValueError("candidate gate receipt fields or stage are invalid")
                if (not isinstance(receipt["authority_id"], str)
                        or not receipt["authority_id"] or len(receipt["authority_id"]) > 256
                        or receipt["adapter_kind"] != "cfw-component-runtime"
                        or receipt["adapter_protocol_version"] != CFW_ADAPTER_PROTOCOL_VERSION
                        or receipt["decision"] not in gate_decisions):
                    raise ValueError("candidate gate receipt authority or decision is invalid")
                digest = receipt["action_digest"]
                if (not isinstance(digest, str) or len(digest) != 64
                        or any(char not in "0123456789abcdef" for char in digest)):
                    raise ValueError("candidate gate receipt action digest is invalid")
                reasons = receipt["reason_codes"]
                if (not isinstance(reasons, (tuple, list)) or not 1 <= len(reasons) <= 8
                        or len(reasons) != len(set(reasons))
                        or any(not isinstance(item, str) or not item or len(item) > 128
                               for item in reasons)):
                    raise ValueError("candidate gate receipt reason codes are invalid")
                digests.add(digest)
                normalized_receipts[stage] = MappingProxyType({
                    **receipt, "reason_codes": tuple(reasons)})
            if len(digests) != 1:
                raise ValueError("candidate gate receipts do not refer to the same action")
            normalized_receipts = MappingProxyType(normalized_receipts)
            object.__setattr__(self, "candidate_gate_receipts", normalized_receipts)
        if self.candidate_preflight_result is not None and (
                self.candidate_action is None
                or self.candidate_preflight_result not in {
                    "block", "require_confirmation", "require_reinspection",
                    "require_recovery", "require_changed_hypothesis",
                    "require_goal_reanchor"}):
            raise ValueError("candidate preflight result is invalid or has no candidate")
        if self.candidate_rejection_stage is not None and (
                self.candidate_preflight_result is None
                or self.candidate_rejection_stage not in {
                    "cfw_preflight", "cfw_final_gate"}):
            raise ValueError("candidate rejection stage is invalid or has no rejection")
        if (self.candidate_preflight_result is not None
                and self.candidate_rejection_stage is None):
            raise ValueError("candidate rejection must identify its authoritative CFW gate")
        if self.candidate_preflight_result is not None:
            required_receipts = ({"preflight"} if self.candidate_rejection_stage == "cfw_preflight"
                                 else {"preflight", "final"})
            if set(normalized_receipts or {}) != required_receipts:
                raise ValueError("candidate rejection gate receipts do not match the rejection stage")
            selected_stage = ("preflight" if self.candidate_rejection_stage == "cfw_preflight"
                              else "final")
            if normalized_receipts[selected_stage]["decision"] != self.candidate_preflight_result:
                raise ValueError("candidate rejection differs from the CFW gate receipt")
            if normalized_receipts["preflight"]["decision"] != self.cfw_preflight_decision:
                raise ValueError("candidate preflight decision differs from its gate receipt")
            if (selected_stage == "final"
                    and normalized_receipts["final"]["decision"] != self.cfw_final_decision):
                raise ValueError("candidate final decision differs from its gate receipt")
        elif normalized_receipts is not None:
            if set(normalized_receipts) != {"preflight"} or (
                    normalized_receipts["preflight"]["decision"] != "allow"):
                raise ValueError("accepted candidate must carry one allowing preflight receipt")
        gate_decisions = {
            "allow", "block", "require_confirmation", "require_reinspection",
            "require_recovery", "require_changed_hypothesis", "require_goal_reanchor",
        }
        for name in ("cfw_preflight_decision", "cfw_final_decision"):
            decision = getattr(self, name)
            if decision is not None and decision not in gate_decisions:
                raise ValueError(f"{name} is outside the CFW action-gate contract")
        # Candidate observations are pre-action records and cannot claim
        # outcomes. Non-action boundaries such as validator observations may
        # carry independently produced evidence references.
        if self.candidate_action is not None and (
                self.progress_made or self.regression_observed or self.artifact_changed
                or self.evidence_refs or self.criterion_evidence_refs):
            raise ValueError("candidate observations cannot claim action outcomes")
        statuses = dict(self.criterion_status)
        if len(statuses) > MAX_GOAL_CRITERIA or any(
                not isinstance(key, str) or not key or len(key) > 128
                or value not in {"unmet", "in_progress", "verified", "blocked", "unknown"}
                for key, value in statuses.items()):
            raise ValueError("host criterion statuses must be bounded to 256 items")
        if self.goal_set is not None:
            snapshot_statuses = {
                item.criterion_id: {
                    "READY": "unmet", "ACTIVE": "in_progress", "VERIFIED": "verified",
                    "BLOCKED": "blocked", "DEPENDENCY_BLOCKED": "blocked",
                    "DEFERRED_BY_OPERATOR": "blocked",
                }[item.status]
                for item in self.goal_set.items
            }
            if statuses and statuses != snapshot_statuses:
                raise ValueError("criterion_status must agree with the host goal-set snapshot")
            statuses = snapshot_statuses
        object.__setattr__(self, "criterion_status", statuses)
        if not isinstance(self.approach_epoch, str) or len(self.approach_epoch) > 256:
            raise ValueError("host approach epoch must be bounded text")
        failed_approaches = self.failed_approaches
        if (not isinstance(failed_approaches, (tuple, list))
                or len(failed_approaches) > 4
                or any(not isinstance(item, AttemptSemanticDescriptor)
                       for item in failed_approaches)):
            raise ValueError("host failed approaches must be bounded typed descriptors")
        object.__setattr__(self, "failed_approaches", tuple(failed_approaches))
        for name, limit in (("evidence_refs", 16), ("active_trigger_codes", 32)):
            values = getattr(self, name)
            if (not isinstance(values, (tuple, list)) or len(values) > limit
                    or len(values) != len(set(values))
                    or any(not isinstance(item, str) or not item or len(item) > 256
                           for item in values)):
                raise ValueError(f"{name} must be a bounded sequence of unique strings")
            object.__setattr__(self, name, tuple(values))
        scoped_refs = self.criterion_evidence_refs
        if not isinstance(scoped_refs, Mapping) or len(scoped_refs) > 256:
            raise ValueError("criterion evidence map must be bounded")
        known_criteria = ({item.criterion_id for item in self.goal_set.items}
                          if self.goal_set is not None else set(statuses))
        normalized_refs = {}
        for criterion_id, refs in scoped_refs.items():
            if criterion_id not in known_criteria:
                raise ValueError("criterion evidence references an unknown criterion")
            if (not isinstance(refs, (tuple, list)) or len(refs) > 8
                    or any(not isinstance(ref, str) or not ref or len(ref) > 256
                           for ref in refs)
                    or len(refs) != len(set(refs))):
                raise ValueError("criterion evidence references are malformed")
            if not set(refs).issubset(set(self.evidence_refs)):
                raise ValueError("criterion evidence must be included in host evidence refs")
            if refs:
                normalized_refs[criterion_id] = tuple(refs)
        object.__setattr__(self, "criterion_evidence_refs",
                           MappingProxyType(normalized_refs))


class CFWComponentPort(Protocol):
    """Explicit CFW capability boundary used by DP session orchestration."""

    def available_component_ids(self) -> Sequence[str]: ...

    def preflight_action(self, action: Mapping[str, Any]) -> Mapping[str, str]: ...

    def authorize_action(self, action: Mapping[str, Any]) -> Mapping[str, str]: ...

    def authority_identity(self) -> Mapping[str, str] | None: ...

    def lifecycle_revision(self) -> int: ...

    def peek_behavior_signals(self, *, after_cursor: int | None,
                              maximum: int,
                              observation: RuntimeObservation) -> BehaviorSignalBatch: ...

    def acknowledge_behavior_signals(self, *, through_cursor: int) -> None: ...

    def effective_behavior_projection(self) -> Mapping[str, Any] | None: ...

    def expire_local_behavior_leases(self, action_index: int) -> bool: ...

    def prepare_behavior_directives(self, directives: Sequence[Mapping[str, Any]]) -> Any: ...

    def apply_behavior_projection(self, prepared: Any,
                                  projection: Mapping[str, Any]) -> Sequence[str]: ...

    def synchronize_behavior_projection(self, projection: Mapping[str, Any]) -> None: ...

    def restore_behavior_projection(self, projection: Mapping[str, Any], *,
                                    expected_current_revision: int) -> None: ...

    def applied_behavior_projection(self) -> Mapping[str, Any] | None: ...

    def rollback_behavior_directives(self, prepared: Any) -> None: ...

    def render_behavior_context(self, context: str, *,
                                active_statework_ids: Sequence[str] = ()) -> str: ...

    def before_model_call(self, context: str, *, tool_capable: bool = False,
                          action_hint: Mapping[str, Any] | None = None,
                          active_statework_ids: Sequence[str] = ()) -> str: ...

    def before_tool_planning(self, context: str,
                             action_hint: Mapping[str, Any] | None = None,
                             active_statework_ids: Sequence[str] = ()) -> str: ...


class CSWWorkflowPort(Protocol):
    """CSW-owned workflow selection and transition operations."""

    def behavioral_snapshot(self, *, candidate_statework_ids: Sequence[str] | None = None
                            ) -> CSWBehaviorSnapshot:
        """Read the current snapshot. This never expires leases as a side effect."""

    def available_statework_ids(self) -> Sequence[str]: ...

    def authority_identity(self) -> Mapping[str, str]: ...

    def activation_matches(self, statework_id: str, *,
                           intervention_id: str) -> bool: ...

    def activate_statework(self, statework_id: str, *,
                           expected_selection_revision: int,
                           candidate_statework_ids: Sequence[str],
                           intervention_id: str,
                           expires_at_action: int) -> CSWBehaviorSnapshot: ...

    def deactivate_statework(self, statework_id: str, *,
                             expected_selection_revision: int,
                             candidate_statework_ids: Sequence[str],
                             intervention_id: str) -> StateWorkReleaseResult: ...

    def checkpoint_selection(self, *, tick_id: str,
                             expected_selection_revision: int) -> None: ...

    def restore_selection_checkpoint(self, *, tick_id: str,
                                     expected_selection_revision: int
                                     ) -> CSWBehaviorSnapshot | None: ...

    def finalize_selection_checkpoint(self, *, tick_id: str) -> None: ...

    def expire_leases(self, *, host_action_index: int) -> CSWBehaviorSnapshot:
        """Explicitly expire DP-owned leases at a host action boundary."""

    def apply_step(self, statework_id: str, step_id: str, *,
                   evidence_refs: Sequence[str], expected_workflow_revision: int) -> Any: ...


SystemOneFrameFactory = Callable[[RuntimeIdentity, str, Sequence[str], BehaviorState,
                                  RuntimeObservation, CSWBehaviorSnapshot,
                                  Sequence[FrameworkSignal],
                                  Mapping[str, Any], Sequence[str]], SystemOneFrame]


class BehavioralDecisionService(Protocol):
    def judge(self, frame: SystemOneFrame, *, mode: str | None = None) -> Any: ...

    def decide_behavior(self, context: Any, *, judgment: Any = None) -> Mapping[str, Any]: ...

    def assess_intervention_outcome(self, intervention: Any, frame: Any, *,
                                    evidence_authority: Any) -> Mapping[str, Any]: ...

    def record_intervention_application(self, intervention: Any, directives: tuple,
                                        applied_directive_ids: tuple[str, ...], *,
                                        event_context: Any) -> str: ...

    def record_intervention_outcome(self, outcome: Any, *, event_context: Any) -> str: ...

    def prepare_intervention_application(self, intervention: Any, directives: tuple,
                                         applied_directive_ids: tuple[str, ...], *,
                                         event_context: Any) -> Mapping[str, Any]: ...

    def prepare_intervention_outcome(self, outcome: Any, *,
                                     event_context: Any) -> Mapping[str, Any]: ...

    def dispatch_runtime_event(self, event: Mapping[str, Any]) -> str: ...


__all__ = ["RuntimeIdentity", "RuntimeObservation", "CFWComponentPort",
           "CSWWorkflowPort", "BehavioralDecisionService"]
