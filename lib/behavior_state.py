"""DigitalPsychology-owned behavioral state and decision wire contracts.

The runtime host persists and applies these values.  This module defines what
they mean; it has no dependency on CFW, CSW, or an MCP implementation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping


BEHAVIOR_MODES = frozenset({
    "DELIVER", "DIAGNOSE", "VERIFY", "RECOVER", "REPLAN", "BLOCKED",
    "REINSPECT", "READY_TO_COMPLETE",
})
FRAMEWORK_MODES = frozenset({
    "off", "passive", "normal", "elevated", "mandatory", "constrained",
    "reopen", "recovery", "reset",
})
INTERVENTION_OUTCOMES = frozenset({
    "effective", "ineffective", "regressed", "unattributed",
    "inconclusive", "expired", "withdrawn",
})
INTERVENTION_ATTRIBUTIONS = frozenset({
    "supported", "confounded", "not_attributed", "none", "insufficient",
})
MAX_ACTIVE_INTERVENTIONS = 16
MAX_TARGETS_PER_INTERVENTION = 12


def _text(value: Any, name: str, *, limit: int = 256) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{name} must be non-empty bounded text")
    return value


def _ids(values: Any, name: str, *, limit: int, item_limit: int = 128) -> tuple[str, ...]:
    if not isinstance(values, (tuple, list)) or len(values) > limit:
        raise ValueError(f"{name} must be a bounded sequence")
    result = tuple(_text(item, name, limit=item_limit) for item in values)
    if len(result) != len(set(result)):
        raise ValueError(f"{name} must not contain duplicates")
    return result


def _nonnegative_int(value: Any, name: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


@dataclass(frozen=True)
class GoalItemSnapshot:
    """Host-owned status and progress facts for one required or optional goal."""

    criterion_id: str
    required: bool = True
    priority: int = 0
    dependencies: tuple[str, ...] = ()
    status: str = "READY"
    actions_since_progress: int = 0
    equivalent_retry_count: int = 0
    last_progress_action: int | None = None
    material_progress_revision: int = 0

    def __post_init__(self) -> None:
        _text(self.criterion_id, "criterion_id", limit=128)
        if not isinstance(self.required, bool):
            raise ValueError("goal item required flag must be boolean")
        if type(self.priority) is not int or not 0 <= self.priority <= 100:
            raise ValueError("goal item priority must be between zero and 100")
        object.__setattr__(self, "dependencies",
                           _ids(self.dependencies, "goal dependencies", limit=256))
        if self.criterion_id in self.dependencies:
            raise ValueError("goal item cannot depend on itself")
        if self.status not in {"READY", "ACTIVE", "VERIFIED", "BLOCKED",
                               "DEPENDENCY_BLOCKED", "DEFERRED_BY_OPERATOR"}:
            raise ValueError("unsupported host goal status")
        if (type(self.actions_since_progress) is not int
                or self.actions_since_progress < 0
                or type(self.equivalent_retry_count) is not int
                or self.equivalent_retry_count < 0):
            raise ValueError("goal progress counters must be non-negative integers")
        if self.last_progress_action is not None and (
                type(self.last_progress_action) is not int
                or self.last_progress_action < 0):
            raise ValueError("last progress action must be non-negative or null")
        _nonnegative_int(self.material_progress_revision,
                         "material progress revision")

    def to_dict(self) -> dict[str, Any]:
        return {"criterion_id": self.criterion_id, "required": self.required,
                "priority": self.priority, "dependencies": list(self.dependencies),
                "status": self.status,
                "actions_since_progress": self.actions_since_progress,
                "equivalent_retry_count": self.equivalent_retry_count,
                "last_progress_action": self.last_progress_action,
                "material_progress_revision": self.material_progress_revision}


@dataclass(frozen=True)
class GoalSetSnapshot:
    """Complete host-authoritative task set; never a prompt-sized projection."""

    revision: int
    items: tuple[GoalItemSnapshot, ...]

    def __post_init__(self) -> None:
        if type(self.revision) is not int or self.revision < 0:
            raise ValueError("goal set revision must be non-negative")
        if not isinstance(self.items, (tuple, list)) or not 1 <= len(self.items) <= 256:
            raise ValueError("goal set must contain between one and 256 items")
        items = tuple(item if isinstance(item, GoalItemSnapshot)
                      else GoalItemSnapshot(**dict(item)) for item in self.items)
        ids = [item.criterion_id for item in items]
        if len(ids) != len(set(ids)):
            raise ValueError("goal set criterion IDs must be unique")
        known = set(ids)
        if any(not set(item.dependencies).issubset(known) for item in items):
            raise ValueError("goal set contains an unknown dependency")
        graph = {item.criterion_id: item.dependencies for item in items}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(criterion_id: str) -> None:
            if criterion_id in visiting:
                raise ValueError("goal set dependencies contain a cycle")
            if criterion_id in visited:
                return
            visiting.add(criterion_id)
            for dependency in graph[criterion_id]:
                visit(dependency)
            visiting.remove(criterion_id)
            visited.add(criterion_id)

        for criterion_id in ids:
            visit(criterion_id)
        object.__setattr__(self, "items", items)

    def to_dict(self) -> dict[str, Any]:
        return {"revision": self.revision,
                "items": [item.to_dict() for item in self.items]}

    @property
    def runnable_items(self) -> tuple[GoalItemSnapshot, ...]:
        verified = {item.criterion_id for item in self.items
                    if item.status == "VERIFIED"}
        return tuple(item for item in self.items
                     if item.required and item.status in {"READY", "ACTIVE"}
                     and set(item.dependencies).issubset(verified))

    @property
    def required_counts(self) -> dict[str, int]:
        required = [item for item in self.items if item.required]
        return {
            "total": len(required),
            "verified": sum(item.status == "VERIFIED" for item in required),
            "ready": sum(item in self.runnable_items for item in required),
            "blocked": sum(item.status == "BLOCKED" for item in required),
            "dependency_blocked": sum(item.status == "DEPENDENCY_BLOCKED"
                                       for item in required),
            "deferred": sum(item.status == "DEFERRED_BY_OPERATOR" for item in required),
            "remaining_required": sum(item.status != "VERIFIED" for item in required),
        }


@dataclass(frozen=True)
class AttentionLease:
    """Bounded DP attention window over the full host-owned goal set."""

    lease_id: str
    criterion_ids: tuple[str, ...]
    issued_revision: int
    goal_set_revision: int
    start_action: int
    max_actions: int = 3
    reason: str = "priority_and_starvation"
    material_progress_revisions: Mapping[str, int] = field(default_factory=dict)
    continuous_attention_leases: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _text(self.lease_id, "attention lease ID", limit=128)
        object.__setattr__(self, "criterion_ids",
                           _ids(self.criterion_ids, "attention criteria", limit=3))
        if not self.criterion_ids:
            raise ValueError("attention lease must select at least one criterion")
        for name in ("issued_revision", "goal_set_revision", "start_action"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if type(self.max_actions) is not int or not 1 <= self.max_actions <= 8:
            raise ValueError("attention lease duration must be between one and eight actions")
        _text(self.reason, "attention lease reason", limit=128)
        focus = set(self.criterion_ids)
        for name in ("material_progress_revisions", "continuous_attention_leases"):
            values = getattr(self, name)
            if not isinstance(values, Mapping) or len(values) > 3:
                raise ValueError(f"{name} must be a bounded focus mapping")
            parsed: dict[str, int] = {}
            for key, value in values.items():
                if key not in focus:
                    raise ValueError(f"{name} key escapes the attention lease")
                _nonnegative_int(value, name)
                if name == "continuous_attention_leases" and value > 2:
                    raise ValueError("continuous attention lease count exceeds two")
                parsed[key] = value
            object.__setattr__(self, name, MappingProxyType(parsed))

    def to_dict(self) -> dict[str, Any]:
        return {"lease_id": self.lease_id, "criterion_ids": list(self.criterion_ids),
                "issued_revision": self.issued_revision,
                "goal_set_revision": self.goal_set_revision,
                "start_action": self.start_action,
                "max_actions": self.max_actions, "reason": self.reason,
                "material_progress_revisions": dict(self.material_progress_revisions),
                "continuous_attention_leases": dict(self.continuous_attention_leases)}

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "AttentionLease":
        data = dict(value)
        data["criterion_ids"] = tuple(data.get("criterion_ids", ()))
        data["material_progress_revisions"] = dict(
            data.get("material_progress_revisions") or {})
        data["continuous_attention_leases"] = dict(
            data.get("continuous_attention_leases") or {})
        return cls(**data)


@dataclass(frozen=True)
class ModeLease:
    """Bounded hysteresis for the current cognitive mode.

    ``minimum_actions`` damps soft mode changes.  ``exit_condition`` names the
    mode-specific evidence that can end the lease early; hard host/safety
    interrupts are evaluated separately by the controller.
    """

    mode: str
    entered_action: int
    minimum_actions: int = 2
    max_actions: int | None = 6
    cause: str = "mode_transition"
    exit_condition: str = "minimum_duration"
    baseline_approach_epoch: str | None = None

    def __post_init__(self) -> None:
        if self.mode not in BEHAVIOR_MODES:
            raise ValueError("mode lease requires a supported cognitive mode")
        _nonnegative_int(self.entered_action, "mode lease entered_action")
        if type(self.minimum_actions) is not int or not 1 <= self.minimum_actions <= 8:
            raise ValueError("mode lease minimum_actions must be between one and eight")
        if self.max_actions is not None and (
                type(self.max_actions) is not int
                or not self.minimum_actions <= self.max_actions <= 32):
            raise ValueError("mode lease max_actions must be null or a bounded value")
        _text(self.cause, "mode lease cause", limit=128)
        if self.exit_condition not in {
                "minimum_duration", "progress_or_new_approach", "verification_result",
                "fresh_evidence", "plan_committed", "diagnostic_evidence"}:
            raise ValueError("unsupported mode lease exit condition")
        if self.baseline_approach_epoch is not None and (
                not isinstance(self.baseline_approach_epoch, str)
                or len(self.baseline_approach_epoch) > 256):
            raise ValueError("baseline approach epoch must be bounded text or null")

    def to_dict(self) -> dict[str, Any]:
        return {"mode": self.mode, "entered_action": self.entered_action,
                "minimum_actions": self.minimum_actions,
                "max_actions": self.max_actions, "cause": self.cause,
                "exit_condition": self.exit_condition,
                "baseline_approach_epoch": self.baseline_approach_epoch}

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ModeLease":
        return cls(**dict(value))


@dataclass(frozen=True)
class InterventionTarget:
    """One reversible setting change owned by an intervention."""

    target: str
    previous_value: str
    applied_value: str

    def __post_init__(self) -> None:
        _text(self.target, "intervention target", limit=128)
        _text(self.previous_value, "previous value", limit=2048)
        _text(self.applied_value, "applied value", limit=2048)

    def to_dict(self) -> dict[str, str]:
        return {"target": self.target, "previous_value": self.previous_value,
                "applied_value": self.applied_value}


@dataclass(frozen=True)
class BehaviorIntervention:
    """An active, bounded behavior change whose result must be observed."""

    intervention_id: str
    cause: str
    expected_effect: str
    start_revision: int
    scope: str
    expires_after_actions: int
    actions_elapsed: int
    targets: tuple[InterventionTarget, ...]
    expires_at_action: int | None = None
    target_criteria: tuple[str, ...] = ()
    baseline_criterion_status: Mapping[str, str] = field(default_factory=dict)
    baseline_action_index: int = 0
    evaluation_horizon_actions: int | None = None
    concurrent_intervention_ids: tuple[str, ...] = ()
    baseline_goal_set: GoalSetSnapshot | None = None
    baseline_attention_last_seen: Mapping[str, int] = field(default_factory=dict)
    condition_id: str = "legacy-condition"
    condition_basis_digest: str = "legacy-condition"

    def __post_init__(self) -> None:
        _text(self.intervention_id, "intervention_id", limit=128)
        _text(self.cause, "intervention cause", limit=128)
        _text(self.expected_effect, "expected effect", limit=512)
        _text(self.scope, "intervention scope", limit=256)
        _text(self.condition_id, "intervention condition ID", limit=128)
        _text(self.condition_basis_digest, "intervention condition digest", limit=128)
        if self.start_revision < 0 or self.expires_after_actions < 1:
            raise ValueError("intervention revision and expiry must be positive bounds")
        if not 0 <= self.actions_elapsed <= self.expires_after_actions:
            raise ValueError("intervention elapsed actions exceed its expiry")
        horizon = self.evaluation_horizon_actions
        if horizon is None:
            horizon = self.expires_after_actions
            object.__setattr__(self, "evaluation_horizon_actions", horizon)
        if not 1 <= horizon <= 64:
            raise ValueError("intervention evaluation horizon is out of bounds")
        if not isinstance(self.baseline_action_index, int) or self.baseline_action_index < 0:
            raise ValueError("baseline action index must be non-negative")
        # Persist the absolute last-covered action. Older stored rows omitted
        # it; their baseline is the last completed action, so duration N ends
        # at baseline + N under the shared PRE/POST boundary convention.
        expiry = self.expires_at_action
        if expiry is None:
            from .lease_window import lease_window
            expiry = lease_window(
                "POST_ACTION", self.baseline_action_index,
                self.expires_after_actions).expires_at_action
            object.__setattr__(self, "expires_at_action", expiry)
        if type(expiry) is not int or expiry < self.baseline_action_index:
            raise ValueError("intervention absolute expiry is invalid")
        if not 1 <= len(self.targets) <= MAX_TARGETS_PER_INTERVENTION:
            raise ValueError("intervention must have one to twelve targets")
        if len({target.target for target in self.targets}) != len(self.targets):
            raise ValueError("intervention targets must be unique")
        object.__setattr__(self, "target_criteria",
                           _ids(self.target_criteria, "target_criteria", limit=8))
        if (not isinstance(self.baseline_criterion_status, Mapping)
                or len(self.baseline_criterion_status) > 8):
            raise ValueError("baseline criterion status must be bounded")
        statuses = {}
        for criterion_id, status in self.baseline_criterion_status.items():
            key = _text(criterion_id, "baseline criterion ID", limit=128)
            if key not in self.target_criteria or status not in {
                    "unmet", "in_progress", "verified", "blocked", "unknown"}:
                raise ValueError("baseline criterion status is invalid or out of scope")
            statuses[key] = status
        object.__setattr__(self, "baseline_criterion_status", MappingProxyType(statuses))
        object.__setattr__(self, "concurrent_intervention_ids",
                           _ids(self.concurrent_intervention_ids,
                                "concurrent_intervention_ids", limit=16))
        if self.baseline_goal_set is not None and not isinstance(
                self.baseline_goal_set, GoalSetSnapshot):
            value = self.baseline_goal_set
            object.__setattr__(self, "baseline_goal_set", GoalSetSnapshot(
                revision=int(value["revision"]),
                items=tuple(GoalItemSnapshot(**dict(item)) for item in value["items"])))
        if (not isinstance(self.baseline_attention_last_seen, Mapping)
                or len(self.baseline_attention_last_seen) > 256):
            raise ValueError("baseline attention history must be bounded")
        baseline_attention = {}
        for criterion_id, action_index in self.baseline_attention_last_seen.items():
            baseline_attention[_text(criterion_id, "baseline attention criterion", limit=128)] = (
                _nonnegative_int(action_index, "baseline attention action index"))
        object.__setattr__(self, "baseline_attention_last_seen",
                           MappingProxyType(baseline_attention))

    def to_dict(self) -> dict[str, Any]:
        return {"intervention_id": self.intervention_id, "cause": self.cause,
                "expected_effect": self.expected_effect,
                "start_revision": self.start_revision, "scope": self.scope,
                "expires_after_actions": self.expires_after_actions,
                "expires_at_action": self.expires_at_action,
                "evaluation_horizon_actions": self.evaluation_horizon_actions,
                "actions_elapsed": self.actions_elapsed,
                "targets": [target.to_dict() for target in self.targets],
                "target_criteria": list(self.target_criteria),
                "baseline_criterion_status": dict(self.baseline_criterion_status),
                "baseline_action_index": self.baseline_action_index,
                "concurrent_intervention_ids": list(self.concurrent_intervention_ids),
                "baseline_goal_set": (self.baseline_goal_set.to_dict()
                                      if self.baseline_goal_set is not None else None),
                "baseline_attention_last_seen": dict(self.baseline_attention_last_seen),
                "condition_id": self.condition_id,
                "condition_basis_digest": self.condition_basis_digest}

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "BehaviorIntervention":
        data = dict(value)
        data["targets"] = tuple(InterventionTarget(**item) for item in data["targets"])
        for name in ("target_criteria", "concurrent_intervention_ids"):
            data[name] = tuple(data.get(name, ()))
        if data.get("baseline_goal_set") is not None:
            value = data["baseline_goal_set"]
            if not isinstance(value, GoalSetSnapshot):
                data["baseline_goal_set"] = GoalSetSnapshot(
                    revision=int(value["revision"]),
                    items=tuple(GoalItemSnapshot(**dict(item)) for item in value["items"]))
        data["baseline_attention_last_seen"] = dict(
            data.get("baseline_attention_last_seen") or {})
        return cls(**data)


@dataclass(frozen=True)
class TaskSetOutcome:
    """Independent task-set deltas observed across one intervention window."""

    targeted_progress_delta: int = 0
    total_required_remaining_before: int = 0
    total_required_remaining_after: int = 0
    newly_verified: tuple[str, ...] = ()
    newly_blocked: tuple[str, ...] = ()
    regressions: tuple[str, ...] = ()
    max_starvation_before: int = 0
    max_starvation_after: int = 0
    attention_coverage_delta: int = 0

    def __post_init__(self) -> None:
        for name in ("targeted_progress_delta", "total_required_remaining_before",
                     "total_required_remaining_after", "max_starvation_before",
                     "max_starvation_after"):
            _nonnegative_int(getattr(self, name), name)
        if type(self.attention_coverage_delta) is not int:
            raise ValueError("attention coverage delta must be an integer")
        for name in ("newly_verified", "newly_blocked", "regressions"):
            object.__setattr__(self, name,
                               _ids(getattr(self, name), name, limit=256))

    def to_dict(self) -> dict[str, Any]:
        return {"targeted_progress_delta": self.targeted_progress_delta,
                "total_required_remaining_before": self.total_required_remaining_before,
                "total_required_remaining_after": self.total_required_remaining_after,
                "newly_verified": list(self.newly_verified),
                "newly_blocked": list(self.newly_blocked),
                "regressions": list(self.regressions),
                "max_starvation_before": self.max_starvation_before,
                "max_starvation_after": self.max_starvation_after,
                "attention_coverage_delta": self.attention_coverage_delta}


@dataclass(frozen=True)
class InterventionOutcome:
    intervention_id: str
    cause: str
    outcome: str
    observed_revision: int
    evidence_refs: tuple[str, ...] = ()
    scope: str = "legacy-unscoped"
    attribution: str = "insufficient"
    criterion_ids_progressed: tuple[str, ...] = ()
    goal_progress: bool | None = None
    artifact_changed: bool | None = None
    repeated_approach: bool | None = None
    verification_quality: str = "unknown"
    comparison_id: str | None = None
    target_criteria: tuple[str, ...] = ()
    task_set_outcome: TaskSetOutcome | None = None
    condition_id: str = "legacy-condition"
    condition_basis_digest: str = "legacy-condition"

    def __post_init__(self) -> None:
        _text(self.intervention_id, "intervention_id", limit=128)
        _text(self.cause, "intervention cause", limit=128)
        _text(self.scope, "outcome scope", limit=256)
        _text(self.condition_id, "outcome condition ID", limit=128)
        _text(self.condition_basis_digest, "outcome condition digest", limit=128)
        if self.outcome not in INTERVENTION_OUTCOMES:
            raise ValueError("unsupported intervention outcome")
        if self.attribution not in INTERVENTION_ATTRIBUTIONS:
            raise ValueError("unsupported intervention attribution")
        if self.observed_revision < 0:
            raise ValueError("observed revision must be non-negative")
        if self.verification_quality not in {"improved", "unchanged", "degraded", "unknown"}:
            raise ValueError("unsupported verification quality")
        if self.comparison_id is not None and (
                not isinstance(self.comparison_id, str) or not self.comparison_id
                or len(self.comparison_id) > 128):
            raise ValueError("comparison_id must be bounded text or null")
        for name in ("goal_progress", "artifact_changed", "repeated_approach"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, bool):
                raise ValueError(f"{name} must be a boolean or null")
        object.__setattr__(self, "evidence_refs",
                           _ids(self.evidence_refs, "evidence_refs", limit=16))
        object.__setattr__(self, "criterion_ids_progressed",
                           _ids(self.criterion_ids_progressed,
                                "criterion_ids_progressed", limit=8))
        object.__setattr__(self, "target_criteria",
                           _ids(self.target_criteria, "outcome target_criteria", limit=8))
        task_set_outcome = self.task_set_outcome
        if task_set_outcome is not None and not isinstance(task_set_outcome, TaskSetOutcome):
            task_set_outcome = TaskSetOutcome(**dict(task_set_outcome))
            object.__setattr__(self, "task_set_outcome", task_set_outcome)

    def to_dict(self) -> dict[str, Any]:
        return {"intervention_id": self.intervention_id, "cause": self.cause,
                "outcome": self.outcome,
                "observed_revision": self.observed_revision,
                "evidence_refs": list(self.evidence_refs), "scope": self.scope,
                "attribution": self.attribution,
                "criterion_ids_progressed": list(self.criterion_ids_progressed),
                "goal_progress": self.goal_progress,
                "artifact_changed": self.artifact_changed,
                "repeated_approach": self.repeated_approach,
                "verification_quality": self.verification_quality,
                "comparison_id": self.comparison_id,
                "target_criteria": list(self.target_criteria),
                "task_set_outcome": (self.task_set_outcome.to_dict()
                                     if self.task_set_outcome is not None else None),
                "condition_id": self.condition_id,
                "condition_basis_digest": self.condition_basis_digest}

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "InterventionOutcome":
        data = dict(value)
        # Read old locally persisted DP v1 states without treating their
        # controller-generated labels as independently attributed outcomes.
        data["outcome"] = {"improved": "inconclusive", "no_effect": "inconclusive"}.get(
            data.get("outcome"), data.get("outcome"))
        data["evidence_refs"] = tuple(data.get("evidence_refs", ()))
        data["criterion_ids_progressed"] = tuple(data.get("criterion_ids_progressed", ()))
        data["target_criteria"] = tuple(data.get("target_criteria", ()))
        if data.get("task_set_outcome") is not None:
            data["task_set_outcome"] = (
                data["task_set_outcome"]
                if isinstance(data["task_set_outcome"], TaskSetOutcome)
                else TaskSetOutcome(**dict(data["task_set_outcome"])))
        return cls(**data)


@dataclass(frozen=True)
class BehaviorState:
    """DP's meaning of the current cognitive/behavioral configuration.

    ``active_stateworks`` is copied from an authoritative CSW snapshot.  A DP
    decision can rank eligible StateWorks, but cannot directly change this
    field or commit a CSW transition.
    """

    revision: int = 0
    cognitive_mode: str = "DELIVER"
    framework_modes: Mapping[str, str] = field(default_factory=lambda: {
        "owl": "normal", "anchor": "passive", "fuse": "normal",
        "ward": "mandatory", "flow": "off", "sispis": "normal",
        "dox": "off",
    })
    active_stateworks: tuple[str, ...] = ()
    focus_criteria: tuple[str, ...] = ()
    attention_lease: AttentionLease | None = None
    mode_lease: ModeLease | None = None
    attention_last_seen: Mapping[str, int] = field(default_factory=dict)
    verification_intensity: str = "normal"
    exploration_budget: str = "normal"
    retry_policy: str = "normal"
    recovery_level: int = 0
    context_strategy: str = "standard"
    response_strategy: str = "direct"
    active_interventions: tuple[BehaviorIntervention, ...] = ()
    intervention_history: tuple[InterventionOutcome, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.revision, int) or self.revision < 0:
            raise ValueError("behavior revision must be a non-negative integer")
        if self.cognitive_mode not in BEHAVIOR_MODES:
            raise ValueError("unsupported cognitive mode")
        if not isinstance(self.framework_modes, Mapping) or len(self.framework_modes) > 32:
            raise ValueError("framework_modes must be a bounded mapping")
        modes: dict[str, str] = {}
        for name, mode in self.framework_modes.items():
            key = _text(name, "framework name", limit=64).lower()
            if not isinstance(mode, str) or mode not in FRAMEWORK_MODES:
                raise ValueError(f"unsupported mode for framework {key!r}")
            modes[key] = mode
        # WARD remains at its hard floor even when a caller supplies stale state.
        modes["ward"] = "mandatory"
        object.__setattr__(self, "framework_modes", MappingProxyType(modes))
        object.__setattr__(self, "active_stateworks",
                           _ids(self.active_stateworks, "active_stateworks", limit=16))
        object.__setattr__(self, "focus_criteria",
                           _ids(self.focus_criteria, "focus_criteria", limit=8))
        lease = self.attention_lease
        if lease is not None and not isinstance(lease, AttentionLease):
            lease = AttentionLease.from_mapping(lease)
            object.__setattr__(self, "attention_lease", lease)
        if lease is not None and lease.criterion_ids != self.focus_criteria:
            raise ValueError("focus criteria must match the active attention lease")
        mode_lease = self.mode_lease
        if mode_lease is not None and not isinstance(mode_lease, ModeLease):
            mode_lease = ModeLease.from_mapping(mode_lease)
            object.__setattr__(self, "mode_lease", mode_lease)
        if mode_lease is not None and mode_lease.mode != self.cognitive_mode:
            raise ValueError("mode lease must describe the current cognitive mode")
        if (not isinstance(self.attention_last_seen, Mapping)
                or len(self.attention_last_seen) > 256):
            raise ValueError("attention history must be bounded to 256 criteria")
        attention_last_seen = {}
        for criterion_id, action_index in self.attention_last_seen.items():
            attention_last_seen[_text(criterion_id, "attention criterion ID", limit=128)] = (
                _nonnegative_int(action_index, "attention action index"))
        object.__setattr__(self, "attention_last_seen",
                           MappingProxyType(attention_last_seen))
        if self.verification_intensity not in {"passive", "normal", "high", "strict"}:
            raise ValueError("unsupported verification intensity")
        if self.exploration_budget not in {"disabled", "low", "normal", "bounded"}:
            raise ValueError("unsupported exploration budget")
        if self.retry_policy not in {"normal", "materially_different_only", "no_retry"}:
            raise ValueError("unsupported retry policy")
        if not isinstance(self.recovery_level, int) or not 0 <= self.recovery_level <= 3:
            raise ValueError("recovery level must be between zero and three")
        if self.context_strategy not in {"standard", "compact", "criterion_focused"}:
            raise ValueError("unsupported context strategy")
        if self.response_strategy not in {"direct", "detailed", "structured"}:
            raise ValueError("unsupported response strategy")
        if len(self.active_interventions) > MAX_ACTIVE_INTERVENTIONS:
            raise ValueError("too many active interventions")
        ids = [item.intervention_id for item in self.active_interventions]
        if len(ids) != len(set(ids)):
            raise ValueError("active intervention IDs must be unique")
        if len(self.intervention_history) > 32:
            raise ValueError("intervention history is bounded to 32 outcomes")

    def to_dict(self) -> dict[str, Any]:
        return {"revision": self.revision, "cognitive_mode": self.cognitive_mode,
                "framework_modes": dict(self.framework_modes),
                "active_stateworks": list(self.active_stateworks),
                "focus_criteria": list(self.focus_criteria),
                "attention_lease": (self.attention_lease.to_dict()
                                    if self.attention_lease is not None else None),
                "mode_lease": (self.mode_lease.to_dict()
                               if self.mode_lease is not None else None),
                "attention_last_seen": dict(self.attention_last_seen),
                "verification_intensity": self.verification_intensity,
                "exploration_budget": self.exploration_budget,
                "retry_policy": self.retry_policy, "recovery_level": self.recovery_level,
                "context_strategy": self.context_strategy,
                "response_strategy": self.response_strategy,
                "active_interventions": [item.to_dict() for item in self.active_interventions],
                "intervention_history": [item.to_dict() for item in self.intervention_history]}

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "BehaviorState":
        data = dict(value)
        data["active_interventions"] = tuple(
            item if isinstance(item, BehaviorIntervention)
            else BehaviorIntervention.from_mapping(item)
            for item in data.get("active_interventions", ()))
        data["intervention_history"] = tuple(
            item if isinstance(item, InterventionOutcome)
            else InterventionOutcome.from_mapping(item)
            for item in data.get("intervention_history", ()))
        for name in ("active_stateworks", "focus_criteria"):
            data[name] = tuple(data.get(name, ()))
        if data.get("attention_lease") is not None:
            data["attention_lease"] = (
                data["attention_lease"]
                if isinstance(data["attention_lease"], AttentionLease)
                else AttentionLease.from_mapping(data["attention_lease"]))
        if data.get("mode_lease") is not None:
            data["mode_lease"] = (
                data["mode_lease"] if isinstance(data["mode_lease"], ModeLease)
                else ModeLease.from_mapping(data["mode_lease"]))
        data["attention_last_seen"] = dict(data.get("attention_last_seen") or {})
        return cls(**data)


@dataclass(frozen=True)
class BehaviorDirective:
    directive_id: str
    operation: str
    target: str
    value: str
    cause: str
    expected_effect: str
    scope: str
    expires_after_actions: int
    intervention_id: str
    expires_at_action: int

    def __post_init__(self) -> None:
        for name in ("directive_id", "target", "cause", "scope", "intervention_id"):
            _text(getattr(self, name), name, limit=256 if name == "scope" else 128)
        _text(self.value, "directive value", limit=2048)
        _text(self.expected_effect, "expected effect", limit=512)
        if self.operation not in {"set_mode", "set_framework", "set_control",
                                  "prefer_statework", "release_statework"}:
            raise ValueError("unsupported behavior directive operation")
        if not 1 <= self.expires_after_actions <= 64:
            raise ValueError("directive expiry must be between one and 64 actions")
        if type(self.expires_at_action) is not int or self.expires_at_action < 0:
            raise ValueError("directive absolute expiry must be a non-negative action index")
        if self.operation == "set_framework" and self.target.lower() == "ward" \
                and self.value != "mandatory":
            raise ValueError("behavior directives cannot lower the WARD hard floor")

    def to_dict(self) -> dict[str, Any]:
        return {"directive_id": self.directive_id, "operation": self.operation,
                "target": self.target, "value": self.value, "cause": self.cause,
                "expected_effect": self.expected_effect, "scope": self.scope,
                "expires_after_actions": self.expires_after_actions,
                "intervention_id": self.intervention_id,
                "expires_at_action": self.expires_at_action}

    @classmethod
    def from_legacy_mapping(cls, value: Mapping[str, Any], *,
                            host_action_index: int,
                            boundary_kind: str) -> "BehaviorDirective":
        """Decode a persisted pre-absolute-expiry directive during migration."""
        data = dict(value)
        if data.get("expires_at_action") is None:
            if type(host_action_index) is not int or host_action_index < 0:
                raise ValueError("legacy directive action index is invalid")
            duration = data.get("expires_after_actions")
            if type(duration) is not int or not 1 <= duration <= 64:
                raise ValueError("legacy directive duration is invalid")
            starts_at_candidate = boundary_kind == "pre_action"
            start = host_action_index if starts_at_candidate else host_action_index + 1
            data["expires_at_action"] = start + duration - 1
        return cls(**data)


@dataclass(frozen=True)
class StateWorkOption:
    """A StateWork CSW has already declared eligible for this identity/revision."""

    statework_id: str
    domain: str = ""
    behavior_tags: tuple[str, ...] = ()
    purpose: str = ""

    def __post_init__(self) -> None:
        _text(self.statework_id, "statework_id", limit=128)
        for name in ("domain", "purpose"):
            value = getattr(self, name)
            if not isinstance(value, str) or len(value) > (64 if name == "domain" else 256):
                raise ValueError(f"{name} must be bounded text")
        object.__setattr__(self, "behavior_tags",
                           _ids(self.behavior_tags, "behavior_tags", limit=16, item_limit=64))

    def to_dict(self) -> dict[str, Any]:
        return {"statework_id": self.statework_id, "domain": self.domain,
                "behavior_tags": list(self.behavior_tags), "purpose": self.purpose}


@dataclass(frozen=True)
class CSWBehaviorSnapshot:
    revision: int
    active_stateworks: tuple[str, ...] = ()
    eligible_stateworks: tuple[StateWorkOption, ...] = ()
    workflow_states: Mapping[str, str] = field(default_factory=dict)
    snapshot_id: str = ""
    workflow_revisions: Mapping[str, int] = field(default_factory=dict)
    eligible_steps: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.revision, int) or self.revision < 0:
            raise ValueError("CSW revision must be non-negative")
        object.__setattr__(self, "active_stateworks",
                           _ids(self.active_stateworks, "active_stateworks", limit=16))
        if len(self.eligible_stateworks) > 32:
            raise ValueError("CSW snapshot contains too many eligible StateWorks")
        ids = [item.statework_id for item in self.eligible_stateworks]
        if len(ids) != len(set(ids)):
            raise ValueError("eligible StateWork IDs must be unique")
        if not isinstance(self.workflow_states, Mapping) or len(self.workflow_states) > 16:
            raise ValueError("workflow_states must be bounded")
        states = {}
        for key, value in self.workflow_states.items():
            states[_text(key, "workflow ID", limit=128)] = _text(value, "workflow state", limit=64)
        object.__setattr__(self, "workflow_states", MappingProxyType(states))
        if self.snapshot_id and (not isinstance(self.snapshot_id, str)
                                 or len(self.snapshot_id) != 64
                                 or any(char not in "0123456789abcdef"
                                        for char in self.snapshot_id)):
            raise ValueError("CSW snapshot ID must be a SHA-256 digest")
        revisions = dict(self.workflow_revisions)
        if (len(revisions) > 16 or not set(revisions).issubset(set(self.active_stateworks))
                or any(not isinstance(value, int) or value < 0
                       for value in revisions.values())):
            raise ValueError("workflow revisions must be bounded to active StateWorks")
        object.__setattr__(self, "workflow_revisions", MappingProxyType(revisions))
        step_map = {}
        if not isinstance(self.eligible_steps, Mapping) or len(self.eligible_steps) > 16:
            raise ValueError("CSW legal step map must be bounded")
        for statework_id, steps in self.eligible_steps.items():
            key = _text(statework_id, "workflow ID", limit=128)
            step_map[key] = _ids(steps, "eligible step IDs", limit=64)
        object.__setattr__(self, "eligible_steps", MappingProxyType(step_map))

    def to_dict(self) -> dict[str, Any]:
        return {"revision": self.revision, "snapshot_id": self.snapshot_id,
                "active_stateworks": list(self.active_stateworks),
                "eligible_stateworks": [item.to_dict() for item in self.eligible_stateworks],
                "workflow_states": dict(self.workflow_states),
                "workflow_revisions": dict(self.workflow_revisions),
                "eligible_steps": {key: list(value)
                                    for key, value in self.eligible_steps.items()}}


@dataclass(frozen=True)
class FrameworkSignal:
    source: str
    code: str
    active: bool = True
    severity: str = "warning"
    criterion_id: str | None = None
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _text(self.source, "signal source", limit=64)
        _text(self.code, "signal code", limit=128)
        if self.severity not in {"info", "warning", "critical"}:
            raise ValueError("unsupported signal severity")
        if self.criterion_id is not None:
            _text(self.criterion_id, "criterion_id", limit=128)
        object.__setattr__(self, "evidence_refs",
                           _ids(self.evidence_refs, "evidence_refs", limit=8))


@dataclass(frozen=True)
class BehavioralDecision:
    decision_id: str
    scope: str
    expected_revision: int
    state: BehaviorState
    directives: tuple[BehaviorDirective, ...] = ()
    preferred_stateworks: tuple[str, ...] = ()
    intervention_outcomes: tuple[InterventionOutcome, ...] = ()
    reason: str = "no behavioral change required"

    def __post_init__(self) -> None:
        _text(self.decision_id, "decision_id", limit=128)
        _text(self.scope, "decision scope", limit=256)
        _text(self.reason, "decision reason", limit=512)
        if self.expected_revision < 0 or self.state.revision != self.expected_revision + 1:
            raise ValueError("decision must advance exactly one behavior revision")
        if len(self.directives) > 32 or len(self.preferred_stateworks) > 16:
            raise ValueError("behavior decision exceeds bounded output limits")
        object.__setattr__(self, "preferred_stateworks",
                           _ids(self.preferred_stateworks, "preferred_stateworks", limit=16))
        if len(self.intervention_outcomes) > 16:
            raise ValueError("too many intervention outcomes")

    def to_dict(self) -> dict[str, Any]:
        return {"decision_id": self.decision_id, "scope": self.scope,
                "expected_revision": self.expected_revision, "state": self.state.to_dict(),
                "directives": [item.to_dict() for item in self.directives],
                "preferred_stateworks": list(self.preferred_stateworks),
                "intervention_outcomes": [item.to_dict() for item in self.intervention_outcomes],
                "reason": self.reason}


__all__ = [
    "BEHAVIOR_MODES", "INTERVENTION_ATTRIBUTIONS", "BehaviorIntervention", "BehaviorDirective", "BehaviorState",
    "BehavioralDecision", "CSWBehaviorSnapshot", "FrameworkSignal",
    "AttentionLease", "ModeLease", "GoalItemSnapshot", "GoalSetSnapshot",
    "InterventionOutcome", "InterventionTarget", "StateWorkOption",
    "TaskSetOutcome",
]
