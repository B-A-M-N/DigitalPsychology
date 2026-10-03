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
from typing import Any, Iterable, Mapping

from ..attention import fairness_bound_actions

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
    attention_rotated: bool = False
    intervention_applied: bool = False
    abstained: bool = False
    equivalent_retry: bool = False
    goal_drift: bool = False
    projection_digest: str | None = None
    semantic_query: bool = False
    cfw_preflight_rejection: bool = False
    cfw_final_rejection: bool = False

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
    actions: list[ActionTrace] = field(default_factory=list)
    _mode_history: list[tuple[int, str, str]] = field(default_factory=list)

    def record(self, action: ActionTrace, *, mode_cause: str = "mode_transition",
               authoritative_evidence: bool = False) -> None:
        if len(self.actions) >= _MAX_TRACE_ACTIONS:
            raise ValueError("behavioral trace exceeded its bounded action count")
        # Seed the history with the opening mode so later transitions have an
        # origin to be measured from.
        if not self._mode_history:
            self._mode_history.append(
                (action.action_index, action.cognitive_mode, mode_cause))
        elif self._mode_history[-1][1] != action.cognitive_mode:
            self._mode_history.append(
                (action.action_index, action.cognitive_mode, mode_cause))
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
        return sum(1 for a in self.actions if a.attention_rotated)

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
            _index_a, mode_a, _cause_a = first
            index_b, mode_b, cause_b = second
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
            justified = cause_b != "mode_transition" or third[2] != "mode_transition"
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
        return sum(1 for a in self.actions if a.semantic_query)

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
            "semantic_provider_status": self.semantic_provider_status,
            "semantic_model_hash": self.semantic_model_hash,
        }
        if task_outcome_score is not None:
            record["task_outcome_score"] = task_outcome_score
        if task_completed is not None:
            record["task_completed"] = task_completed
        return record


__all__ = ["REVERSAL_WINDOW_ACTIONS", "ActionTrace", "BehavioralTrace",
           "fairness_bound_actions"]
