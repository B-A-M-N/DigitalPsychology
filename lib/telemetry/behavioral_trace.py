"""Behavioral telemetry: the internal trace the cohort gate joins on.

The external evaluators measure task outcome and must stay ignorant of DP
internals. The production gate additionally needs starvation, mode thrash,
retry/drift/abstention counts, projection churn, and provider attestation. None
of that is observable from final task text, so DP records it here, per committed
action, from its own authoritative state.

Two rules govern this module:

- Every metric is computed from DP's real fairness invariant
  (``attention.fairness_bound_actions``), not estimated after the fact. The
  primary production invariant is ``fairness_violation_count == 0``.
- Raw transition counts are not reported as "thrash". A legitimate
  ``DELIVER -> VERIFY -> DELIVER`` is not thrash. A reversal is only counted
  when a mode returns to its previous value within the reversal window *without*
  new authoritative evidence, which is the pathological case the gate cares
  about.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Iterable, Mapping

from ..attention import fairness_bound_actions


def provider_attestation(factory: Any) -> dict[str, Any] | None:
    """Read only capability metadata pinned by DP's real provider handshake."""
    raw = getattr(factory, "system_one_attestation", None)
    if not isinstance(raw, Mapping):
        return None
    fields = ("backend_id", "model_id", "model_revision", "model_content_hash",
              "provider_protocol_hash", "calibration_bundle_hash", "provider_mode",
              "capability_digest")
    if any(not isinstance(raw.get(name), str) for name in fields):
        return None
    digest = re.compile(r"^[0-9a-f]{64}$")
    if (not digest.fullmatch(raw["model_content_hash"])
            or not digest.fullmatch(raw["provider_protocol_hash"])
            or not digest.fullmatch(raw["capability_digest"])
            or (raw["calibration_bundle_hash"]
                and not digest.fullmatch(raw["calibration_bundle_hash"]))
            or raw["provider_mode"] not in {"shadow", "advisory"}):
        return None
    families = raw.get("enabled_question_families")
    if (not isinstance(families, (tuple, list)) or len(families) > 16
            or any(not isinstance(item, str) or not item for item in families)
            or len(families) != len(set(families))):
        return None
    return {**{name: raw[name] for name in fields},
            "enabled_question_families": sorted(families)}


def runtime_configuration(*, cfw_connected: bool, csw_connected: bool,
                          attestation: Mapping[str, Any] | None) -> str:
    """Describe connected authorities from runtime capabilities, not task labels."""
    parts = (["cfw"] if cfw_connected else [])
    if csw_connected:
        parts.append("csw")
    parts.append("dp")
    configuration = "+".join(parts)
    if (attestation is not None
            and attestation.get("backend_id") == "digitalpsychology-laya-native"):
        configuration += "+laya"
    return configuration


def provider_status(attestation: Mapping[str, Any] | None, *,
                    factory_configured: bool) -> str:
    if attestation is not None:
        return str(attestation["provider_mode"])
    return "unattested" if factory_configured else "disabled"

# A reversal within this many committed actions, with no new evidence, is
# pathological. Legitimate hard interrupts clear the evidence requirement.
REVERSAL_WINDOW_ACTIONS = 2

_MAX_TRACE_ACTIONS = 10_000


@dataclass(frozen=True)
class ActionTrace:
    """One committed action's observable behavioral facts."""

    action_index: int
    cognitive_mode: str
    focus_criteria: tuple[str, ...]
    attention_ages: Mapping[str, int]
    fairness_bound: int
    max_starvation_age: int
    mode_entered_action: int | None = None
    attention_lease_started: bool = False
    attention_rotated: bool = False
    fairness_forced_rotation: bool = False
    fairness_prevented_criteria: tuple[str, ...] = ()
    intervention_applied: bool = False
    abstained: bool = False
    equivalent_retry: bool = False
    goal_drift: bool = False
    projection_digest: str | None = None
    semantic_query: bool = False
    semantic_attempted: bool = False
    semantic_result: bool = False
    semantic_used: bool = False
    semantic_failed: bool = False
    cfw_preflight_rejection: bool = False
    cfw_final_rejection: bool = False
    authoritative_evidence_classes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "focus_criteria", tuple(self.focus_criteria))
        object.__setattr__(self, "fairness_prevented_criteria",
                           tuple(self.fairness_prevented_criteria))
        object.__setattr__(self, "authoritative_evidence_classes",
                           tuple(self.authoritative_evidence_classes))

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_index": self.action_index,
            "cognitive_mode": self.cognitive_mode,
            "focus_criteria": list(self.focus_criteria),
            "attention_ages": dict(self.attention_ages),
            "fairness_bound": self.fairness_bound,
            "max_starvation_age": self.max_starvation_age,
            "mode_entered_action": self.mode_entered_action,
            "attention_lease_started": self.attention_lease_started,
            "attention_rotated": self.attention_rotated,
            "fairness_forced_rotation": self.fairness_forced_rotation,
            "fairness_prevented_criteria": list(self.fairness_prevented_criteria),
            "intervention_applied": self.intervention_applied,
            "abstained": self.abstained,
            "equivalent_retry": self.equivalent_retry,
            "goal_drift": self.goal_drift,
            "projection_digest": self.projection_digest,
            "semantic_query": self.semantic_query,
            "semantic_attempted": self.semantic_attempted,
            "semantic_result": self.semantic_result,
            "semantic_used": self.semantic_used,
            "semantic_failed": self.semantic_failed,
            "cfw_preflight_rejection": self.cfw_preflight_rejection,
            "cfw_final_rejection": self.cfw_final_rejection,
            "authoritative_evidence_classes": list(
                self.authoritative_evidence_classes),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ActionTrace":
        data = dict(value)
        for name in ("focus_criteria", "fairness_prevented_criteria",
                     "authoritative_evidence_classes"):
            data[name] = tuple(data.get(name) or ())
        data["attention_ages"] = dict(data.get("attention_ages") or {})
        return cls(**data)

    @property
    def starvation_ratio(self) -> float:
        if self.fairness_bound <= 0:
            return 0.0
        return round(self.max_starvation_age / self.fairness_bound, 6)

    @property
    def fairness_violation(self) -> bool:
        """True when a runnable criterion aged past the bound DP committed to."""
        return self.fairness_bound > 0 and self.max_starvation_age > self.fairness_bound


@dataclass
class BehavioralTrace:
    """Accumulates per-action traces and derives the gate's metrics."""

    configuration: str = "unknown"
    task_id: str = "unknown"
    semantic_provider_status: str = "disabled"
    semantic_model_hash: str | None = None
    semantic_provider_attestation: Mapping[str, Any] | None = None
    actions: list[ActionTrace] = field(default_factory=list)
    _mode_history: list[tuple[int, str, str, bool]] = field(default_factory=list)

    def record(self, action: ActionTrace, *, mode_cause: str = "mode_transition",
               authoritative_evidence: bool = False) -> None:
        if len(self.actions) >= _MAX_TRACE_ACTIONS:
            raise ValueError("behavioral trace exceeded its bounded action count")
        # Seed the history with the opening mode so later transitions have an
        # origin to be measured from.
        if not self._mode_history:
            self._mode_history.append(
                (action.action_index, action.cognitive_mode, mode_cause,
                 authoritative_evidence
                 or bool(action.authoritative_evidence_classes)))
        elif self._mode_history[-1][1] != action.cognitive_mode:
            self._mode_history.append(
                (action.action_index, action.cognitive_mode, mode_cause,
                 authoritative_evidence
                 or bool(action.authoritative_evidence_classes)))
        self.actions.append(action)

    # -- starvation ---------------------------------------------------------

    @property
    def max_starvation_age(self) -> int:
        return max((a.max_starvation_age for a in self.actions), default=0)

    @property
    def max_starvation_ratio(self) -> float:
        return max((a.starvation_ratio for a in self.actions), default=0.0)

    @property
    def fairness_violation_count(self) -> int:
        return sum(1 for a in self.actions if a.fairness_violation)

    @property
    def required_criterion_coverage(self) -> tuple[str, ...]:
        """Every criterion that ever received attention, in first-seen order."""
        seen: list[str] = []
        for action in self.actions:
            for criterion in action.focus_criteria:
                if criterion not in seen:
                    seen.append(criterion)
        return tuple(seen)

    @property
    def forced_attention_rotations(self) -> int:
        return self.fairness_forced_rotations

    @property
    def attention_lease_starts(self) -> int:
        return sum(1 for action in self.actions if action.attention_lease_started)

    @property
    def attention_rotations(self) -> int:
        return sum(1 for action in self.actions if action.attention_rotated)

    @property
    def fairness_forced_rotations(self) -> int:
        return sum(1 for action in self.actions if action.fairness_forced_rotation)

    @property
    def fairness_prevented_criteria(self) -> tuple[str, ...]:
        seen: list[str] = []
        for action in self.actions:
            for criterion in action.fairness_prevented_criteria:
                if criterion not in seen:
                    seen.append(criterion)
        return tuple(seen)

    # -- mode behavior ------------------------------------------------------

    @property
    def mode_transitions(self) -> int:
        """Committed actions that changed the cognitive mode."""
        return max(0, len(self._mode_history) - 1)

    @property
    def rapid_mode_reversals(self) -> int:
        """A -> B -> A inside the window with no new authoritative evidence.

        This is the definition the gate wants. Raw transition count is not
        thrash: DELIVER -> VERIFY -> DELIVER across real verification evidence
        is correct behavior and must not be counted. A reversal is only
        pathological when the return happened inside the window and nothing
        justified either move.
        """
        history = self._mode_history
        reversals = 0
        for first, second in zip(history, history[1:]):
            _index_a, mode_a, _cause_a, evidence_a = first
            index_b, mode_b, _cause_b, evidence_b = second
            if mode_a == mode_b:
                continue
            # The next genuine change of mode after entering mode_b.
            third = next((entry for entry in history
                          if entry[0] > index_b and entry[1] != mode_b), None)
            if third is None or third[1] != mode_a:
                continue
            within_window = (third[0] - index_b) <= REVERSAL_WINDOW_ACTIONS
            # New authoritative evidence justifies the move; only unsupported
            # oscillation counts as thrash.
            justified = evidence_a or evidence_b or third[3]
            if within_window and not justified:
                reversals += 1
        return reversals

    @property
    def median_mode_dwell_actions(self) -> float | None:
        """Median committed actions spent in each mode before the next change."""
        if len(self._mode_history) < 2:
            return None
        boundaries = [entry[0] for entry in self._mode_history]
        dwells = [b - a for a, b in zip(boundaries, boundaries[1:])]
        if not dwells:
            return None
        ordered = sorted(dwells)
        middle = len(ordered) // 2
        if len(ordered) % 2:
            return float(ordered[middle])
        return (ordered[middle - 1] + ordered[middle]) / 2.0

    # -- behavior counters --------------------------------------------------

    @property
    def equivalent_retries(self) -> int:
        return sum(1 for a in self.actions if a.equivalent_retry)

    @property
    def goal_drift_interventions(self) -> int:
        return sum(1 for a in self.actions if a.goal_drift)

    @property
    def abstentions(self) -> int:
        return sum(1 for a in self.actions if a.abstained)

    @property
    def cfw_preflight_rejections(self) -> int:
        return sum(1 for a in self.actions if a.cfw_preflight_rejection)

    @property
    def cfw_final_gate_rejections(self) -> int:
        return sum(1 for a in self.actions if a.cfw_final_rejection)

    @property
    def committed_actions(self) -> int:
        return len(self.actions)

    # -- projection churn ---------------------------------------------------

    @property
    def projection_updates(self) -> int:
        return sum(1 for a in self.actions if a.projection_digest)

    @property
    def unique_projection_digests(self) -> int:
        return len({a.projection_digest for a in self.actions if a.projection_digest})

    @property
    def semantic_queries(self) -> int:
        return sum(1 for a in self.actions if a.semantic_attempted or a.semantic_query)

    @property
    def semantic_results(self) -> int:
        return sum(1 for action in self.actions if action.semantic_result)

    @property
    def semantic_uses(self) -> int:
        return sum(1 for action in self.actions if action.semantic_used)

    @property
    def semantic_failures(self) -> int:
        return sum(1 for action in self.actions if action.semantic_failed)

    def to_dict(self, *, trial_id: str, task_outcome_score: float | None = None,
                task_completed: bool | None = None) -> dict[str, Any]:
        """Emit the machine-readable trial record the production gate joins on."""
        record: dict[str, Any] = {
            "trial_id": trial_id,
            "configuration": self.configuration,
            "task_id": self.task_id,
            "evidence_source": "internal_host_telemetry",

            "committed_actions": self.committed_actions,
            "max_starvation_age": self.max_starvation_age,
            "max_starvation_ratio": self.max_starvation_ratio,
            "fairness_bound": max((a.fairness_bound for a in self.actions), default=0),
            "fairness_violation_count": self.fairness_violation_count,
            "forced_attention_rotations": self.forced_attention_rotations,
            "attention_lease_starts": self.attention_lease_starts,
            "attention_rotations": self.attention_rotations,
            "fairness_forced_rotations": self.fairness_forced_rotations,
            "fairness_prevented_criteria": list(self.fairness_prevented_criteria),
            "required_criterion_coverage": list(self.required_criterion_coverage),

            "mode_transitions": self.mode_transitions,
            "rapid_mode_reversals": self.rapid_mode_reversals,
            "median_mode_dwell_actions": self.median_mode_dwell_actions,

            "equivalent_retries": self.equivalent_retries,
            "goal_drift_interventions": self.goal_drift_interventions,
            "abstentions": self.abstentions,

            "cfw_preflight_rejections": self.cfw_preflight_rejections,
            "cfw_final_gate_rejections": self.cfw_final_gate_rejections,

            "projection_updates": self.projection_updates,
            "unique_projection_digests": self.unique_projection_digests,
            "semantic_queries": self.semantic_queries,
            "semantic_results": self.semantic_results,
            "semantic_uses": self.semantic_uses,
            "semantic_failures": self.semantic_failures,
            "semantic_provider_status": self.semantic_provider_status,
            "semantic_model_hash": self.semantic_model_hash,
            "semantic_provider_attestation": (
                dict(self.semantic_provider_attestation)
                if self.semantic_provider_attestation is not None else None),
        }
        if task_outcome_score is not None:
            record["task_outcome_score"] = task_outcome_score
        if task_completed is not None:
            record["task_completed"] = task_completed
        return record


__all__ = ["REVERSAL_WINDOW_ACTIONS", "ActionTrace", "BehavioralTrace",
           "fairness_bound_actions", "provider_attestation",
           "provider_status", "runtime_configuration"]
