"""Bridge: corpus records -> SystemOneFrame -> local inference -> metrics.

`offline_eval.py` validates and binds a corpus but never runs inference, because a
`CorpusRecord` did not carry the task context a `SystemOneFrame` requires. This
module supplies that bridge so the qualification chain actually reaches
discrimination / calibration / margin metrics.

`SemanticState` requires an exact task-context key set and validates
`work_mode` / `task_complexity` / `deliverable_kind` against fixed vocabularies,
so a corpus record must supply them or frame construction fails closed.

No inference runs at import. `run()` requires an explicit provider.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from lib.protocol_artifact import QUESTION_REGISTRY
from lib.system_one_contracts import (
    ActivityFact,
    CriterionState,
    QuestionSpec,
    SemanticState,
    SystemOneFrame,
    SystemOneIdentity,
    canonical_hash,
)

from .corpus_schema import CorpusRecord
from .qualification_spec import QualificationSpec

WORK_MODES = frozenset({"delivery", "diagnose", "review", "research"})
TASK_COMPLEXITIES = frozenset({"trivial", "standard", "complex"})
DELIVERABLE_KINDS = frozenset({
    "visual_ui", "code_behavior", "document", "architecture", "test_suite",
    "analysis", "prose", "data", "configuration", "other",
})


class FrameConstructionError(ValueError):
    """A corpus record lacks the task context a SystemOneFrame requires."""


@dataclass(frozen=True)
class TaskContext:
    """The task/situation fields a frame needs, beyond the routing decision.

    ``workflows`` carries real ``WorkflowFact`` values so the offline frame
    matches what the production runtime supplies. Without them the model is
    asked to prefer a StateWork while being told nothing that distinguishes
    one from another -- a representation defect, not a model defect.
    """

    objective: str
    work_mode: str
    deliverable_kind: str
    task_complexity: str = "standard"
    phase: str = "implementation"
    current_state: str = "UNKNOWN"
    domain_tags: tuple[str, ...] = ("software",)
    criterion_id: str = "C1"
    criterion_claim: str = "The requested outcome is achieved."
    # Real workflow facts: (statework_id, purpose, domain, current_state,
    # legal_next_states, eligible, required_evidence). These are PRE-DECISION
    # facts a host would legitimately know; none of them encodes which route is
    # correct.
    workflows: tuple[tuple[str, str, str, str, tuple[str, ...], bool,
                           tuple[str, ...]], ...] = ()
    # Evidence the host already holds, so the model can tell which obligations
    # are satisfiable now. Pre-decision, not a hint.
    available_evidence: tuple[str, ...] = ()
    non_goals: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.work_mode not in WORK_MODES:
            raise FrameConstructionError(
                f"unsupported work_mode {self.work_mode!r}; expected one of "
                f"{sorted(WORK_MODES)}")
        if self.task_complexity not in TASK_COMPLEXITIES:
            raise FrameConstructionError(
                f"unsupported task_complexity {self.task_complexity!r}")
        if self.deliverable_kind not in DELIVERABLE_KINDS:
            raise FrameConstructionError(
                f"unsupported deliverable_kind {self.deliverable_kind!r}")
        if len(self.non_goals) > 8:
            raise FrameConstructionError("non_goals exceeds its bound")


@dataclass(frozen=True)
class PredictionArtifact:
    """One model prediction bound to the record it came from."""

    frame_id: str
    trajectory_id: str
    eligible_routes: tuple[str, ...]
    selected: str
    distribution: Mapping[str, float]
    margin: float
    # The independent label; NEVER derived from the prediction.
    appropriate_route: str | None
    abstention_expected: bool
    # What the deterministic controller chose.
    controller_selected: str | None
    checkpoint_revision: str
    checkpoint_content_hash: str

    @property
    def correct(self) -> bool | None:
        """Whether the prediction matches the INDEPENDENT semantic target.

        None when no target exists, so unusable labels cannot silently count as
        either a success or a failure.
        """
        if self.abstention_expected:
            return None
        if self.appropriate_route is None:
            return None
        return self.selected == self.appropriate_route

    @property
    def controller_correct(self) -> bool | None:
        if self.abstention_expected or self.appropriate_route is None:
            return None
        return self.controller_selected == self.appropriate_route

    @property
    def is_false_intervention(self) -> bool:
        """The model overrode a CORRECT deterministic selection.

        This is the harm case: improving accuracy by overriding correct
        deterministic choices is not an improvement. Requires the controller to
        have been right AND the model to be wrong on the same scorable case.
        """
        return (self.controller_correct is True and self.correct is False
                and self.appropriate_route is not None
                and not self.abstention_expected)


def _workflow_facts(context: TaskContext) -> tuple:
    """Build real WorkflowFact values from the task context.

    Mirrors the production projection in lib/control/system_one.py: each
    eligible StateWork contributes its purpose, domain, current state, legal next
    states, and -- the addition this project makes -- the required-evidence kinds
    its transitions demand. Those are facts the host knows BEFORE deciding, and
    none of them encodes which route is correct.
    """
    from lib.system_one_contracts import WorkflowFact
    return tuple(
        WorkflowFact(
            statework_id=statework_id,
            progress_class=None,
            domain=domain or None,
            purpose=purpose or None,
            current_state=current_state or "UNKNOWN",
            legal_next_states=tuple(legal_next),
            eligible=eligible,
            source="csw_authoritative",
        )
        for statework_id, purpose, domain, current_state, legal_next, eligible,
        _required in context.workflows)


def _route_descriptions(context: TaskContext, legal: tuple[str, ...]
                        ) -> tuple[tuple[str, str], ...]:
    """Option labels for the route_preference question.

    The production runtime labels options from ``legal_stateworks``. A generic
    label would make every option indistinguishable, so these carry the
    workflow's purpose and domain -- the same vocabulary the runtime exposes --
    plus the evidence its transitions require, which is what actually separates
    getter from gitter.
    """
    described = {}
    for row in context.workflows:
        statework_id, purpose, domain, _state, _next, _elig, required = row
        described[statework_id] = (purpose, domain, required)
    options = []
    for statework_id in legal:
        purpose, domain, required = described.get(
            statework_id, ("", "", ()))
        label = f"{statework_id} — {purpose or 'no stated purpose'}"
        if domain:
            label += f" [{domain}]"
        if required:
            held = sorted(set(required) & set(context.available_evidence))
            missing = sorted(set(required) - set(context.available_evidence))
            label += f" requires {','.join(required)}"
            if held:
                label += f" (held: {','.join(held)})"
            if missing:
                label += f" (missing: {','.join(missing)})"
        options.append((statework_id, label[:256]))
    return tuple(options)


def build_frame(record: CorpusRecord, context: TaskContext, *,
                sequence: int, backend_id: str, model_id: str,
                model_revision: str, model_content_hash: str,
                provider_protocol_hash: str) -> SystemOneFrame:
    """Construct a real SystemOneFrame for one corpus record's routing decision."""
    legal = record.semantic_target.eligible_routes
    identity = SystemOneIdentity(
        namespace_id="qualification", application_id="laya-qualification",
        application_instance_id="offline", agent_id="laya",
        agent_instance_id="offline", session_id=record.trajectory_id,
        task_id=record.frame_id, attempt_id="attempt-1", interaction_id=None)
    activity = ActivityFact(
        sequence=sequence, activity_type="action", source="host_observed",
        action_class="observation", host_declared_relation="ambiguous",
        target_kind="workspace", proposed_purpose=context.objective,
        affected_criterion_ids=(context.criterion_id,))
    state = SemanticState(
        task={
            "objective": context.objective,
            "work_mode": context.work_mode,
            "deliverable_kind": context.deliverable_kind,
            "task_complexity": context.task_complexity,
            "phase": context.phase,
            "active_segment": "segment-0",
            "active_statework": None,
            "active_flow": None,
            "non_goals": list(context.non_goals),
            "domain_tags": list(context.domain_tags),
        },
        situation={
            "current_state": context.current_state,
            "blockers": (),
            "actions_since_goal_progress": 0,
            "actions_since_artifact_change": 0,
            "verification_actions_since_artifact_change": 0,
            "recovery_epochs": 0,
            "repeated_branch_count": 0,
        },
        criteria=(CriterionState(
            context.criterion_id, context.criterion_claim, "functional"),),
        activity=(activity,), workflows=_workflow_facts(context),
        convergence=(), candidate=activity,
        legal_routes=legal)
    source = QUESTION_REGISTRY["route_preference"]
    questions = (QuestionSpec(
        question_id="route_preference", family=source["family"],
        question_type=source["type"], instructions=source["instructions"],
        criteria=_route_descriptions(context, legal)),)
    policy_hash = record.policy_snapshot_hash
    state_digest = canonical_hash(state.to_dict())
    bundle_digest = canonical_hash([q.to_dict() for q in questions])
    return SystemOneFrame(
        identity=identity, request_id=record.frame_id, sequence=sequence,
        state_digest=state_digest, question_bundle_digest=bundle_digest,
        request_digest=canonical_hash({
            "identity": identity.to_dict(), "sequence": sequence,
            "state_digest": state_digest,
            "question_bundle_digest": bundle_digest,
            "policy_hash": policy_hash}),
        policy_hash=policy_hash, backend_id=backend_id, model_id=model_id,
        model_revision=model_revision, model_content_hash=model_content_hash,
        provider_protocol_hash=provider_protocol_hash,
        calibration_bundle_hash=canonical_hash({}), state=state,
        questions=questions)


def prediction_from_judgment(record: CorpusRecord, judgment: Any, *,
                             checkpoint_revision: str,
                             checkpoint_content_hash: str) -> PredictionArtifact:
    """Extract a prediction artifact from a raw SystemOneJudgment."""
    answers = getattr(judgment, "answers", None) or ()
    route_answer = next(
        (a for a in answers if a.question_id == "route_preference"), None)
    if route_answer is None:
        raise FrameConstructionError(
            f"record {record.frame_id}: judgment carried no route_preference answer")
    distribution = {p.label: float(p.probability)
                    for p in (route_answer.calibrated_probabilities
                              or route_answer.raw_probabilities)}
    if not distribution:
        raise FrameConstructionError(
            f"record {record.frame_id}: judgment carried an empty distribution")
    ordered = sorted(distribution.items(), key=lambda kv: (-kv[1], kv[0]))
    margin = (ordered[0][1] - ordered[1][1]) if len(ordered) >= 2 else 0.0
    return PredictionArtifact(
        frame_id=record.frame_id, trajectory_id=record.trajectory_id,
        eligible_routes=record.semantic_target.eligible_routes,
        selected=str(route_answer.selected), distribution=distribution,
        margin=margin,
        appropriate_route=record.semantic_target.appropriate_route,
        abstention_expected=record.semantic_target.abstention_expected,
        controller_selected=record.controller_decision.selected_route,
        checkpoint_revision=checkpoint_revision,
        checkpoint_content_hash=checkpoint_content_hash)


@dataclass
class QualificationMetrics:
    """Measured metrics. Empty inputs yield INCONCLUSIVE, never a pass."""

    n: int = 0
    n_scored: int = 0
    balanced_accuracy: float | None = None
    controller_accuracy: float | None = None
    confusion: Mapping[str, Mapping[str, int]] = field(default_factory=dict)
    brier: float | None = None
    ece: float | None = None
    mean_margin: float | None = None
    false_interventions: int = 0
    false_intervention_rate: float | None = None
    complementarity: Mapping[str, Any] = field(default_factory=dict)
    verdict: str = "INCONCLUSIVE_NO_DATA"

    def to_dict(self) -> dict[str, Any]:
        return {
            "n": self.n, "n_scored": self.n_scored,
            "balanced_accuracy": self.balanced_accuracy,
            "controller_accuracy": self.controller_accuracy,
            "confusion": {k: dict(v) for k, v in self.confusion.items()},
            "brier": self.brier, "ece": self.ece,
            "mean_margin": self.mean_margin,
            "false_interventions": self.false_interventions,
            "false_intervention_rate": self.false_intervention_rate,
            "complementarity": dict(self.complementarity),
            "verdict": self.verdict,
        }


def compute_metrics(predictions: Sequence[PredictionArtifact], *,
                    spec: QualificationSpec | None = None,
                    trajectories: int = 0) -> QualificationMetrics:
    """Compute discrimination, calibration, margin and utility metrics.

    Discrimination and calibration are computed SEPARATELY: a model can be well
    calibrated and still at chance. Complementarity is measured explicitly on
    the subset where the deterministic controller was wrong.
    """
    spec = spec or QualificationSpec()
    metrics = QualificationMetrics(n=len(predictions))
    scored = [p for p in predictions if p.correct is not None]
    metrics.n_scored = len(scored)
    if not scored:
        metrics.verdict = "INCONCLUSIVE_NO_SCORABLE_LABELS"
        return metrics

    # --- discrimination (per-class, so a collapsed predictor cannot look good)
    labels = sorted({p.appropriate_route for p in scored
                     if p.appropriate_route is not None})
    per_class: dict[str, list[bool]] = {label: [] for label in labels}
    for prediction in scored:
        if prediction.appropriate_route is None:
            continue
        per_class[prediction.appropriate_route].append(
            prediction.selected == prediction.appropriate_route)
    recalls = [sum(v) / len(v) for v in per_class.values() if v]
    metrics.balanced_accuracy = (sum(recalls) / len(recalls)) if recalls else None

    confusion: dict[str, dict[str, int]] = {}
    for prediction in scored:
        row = confusion.setdefault(str(prediction.appropriate_route), {})
        row[prediction.selected] = row.get(prediction.selected, 0) + 1
    metrics.confusion = confusion

    # --- calibration: Brier over the full distribution, ECE in 10 bins
    brier_terms = []
    for prediction in scored:
        target = prediction.appropriate_route
        brier_terms.append(sum(
            (prediction.distribution.get(label, 0.0) - (1.0 if label == target else 0.0)) ** 2
            for label in prediction.distribution))
    metrics.brier = sum(brier_terms) / len(brier_terms) if brier_terms else None

    bins: list[list[tuple[float, bool]]] = [[] for _ in range(10)]
    for prediction in scored:
        confidence = max(prediction.distribution.values())
        correct = prediction.selected == prediction.appropriate_route
        index = min(9, int(confidence * 10))
        bins[index].append((confidence, correct))
    ece = 0.0
    for bucket in bins:
        if not bucket:
            continue
        mean_conf = sum(c for c, _ in bucket) / len(bucket)
        accuracy = sum(1 for _, ok in bucket if ok) / len(bucket)
        ece += (len(bucket) / len(scored)) * abs(mean_conf - accuracy)
    metrics.ece = ece

    # --- margin
    metrics.mean_margin = sum(p.margin for p in scored) / len(scored)

    # --- false interventions and complementarity
    false_interventions = [p for p in scored if p.is_false_intervention]
    metrics.false_interventions = len(false_interventions)
    metrics.false_intervention_rate = len(false_interventions) / len(scored)

    controller_scored = [p for p in scored if p.controller_correct is not None]
    controller_accuracy = (
        sum(1 for p in controller_scored if p.controller_correct) / len(controller_scored)
        if controller_scored else None)
    metrics.controller_accuracy = controller_accuracy

    # Complementarity: does Laya succeed where the deterministic controller fails?
    controller_wrong = [p for p in scored if p.controller_correct is False]
    laya_fixed = [p for p in controller_wrong if p.correct is True]
    laya_broke = [p for p in scored
                  if p.controller_correct is True and p.correct is False]
    metrics.complementarity = {
        "controller_wrong": len(controller_wrong),
        "laya_fixed_those": len(laya_fixed),
        "laya_broke_correct_ones": len(laya_broke),
        "note": ("near-chance aggregate accuracy does not rule out complementary "
                 "signal; this is measured on the controller-failure subset "
                 "specifically"),
    }

    # --- verdict, gated on the SAMPLE gates as well as the metric gates
    sample = spec.evaluate_sample_size(
        outcomes=metrics.n, trajectories=trajectories or metrics.n,
        cohort_trajectories=trajectories or metrics.n)
    if sample["verdict"] != "QUALIFIED_FOR_EVALUATION":
        metrics.verdict = sample["verdict"]
        return metrics

    discrimination = spec.evaluate_discrimination(
        balanced_accuracy=metrics.balanced_accuracy or 0.0)
    calibration = spec.evaluate_calibration(
        brier=metrics.brier if metrics.brier is not None else 1.0,
        ece=metrics.ece if metrics.ece is not None else 1.0)
    margin = spec.evaluate_margin(
        per_case_margins=tuple(p.margin for p in scored))

    # A single-class corpus cannot demonstrate discrimination: balanced accuracy
    # is 1.0 when only one label was ever tested, so a collapsed or trivially
    # uniform corpus would otherwise read as qualified.
    classes_observed = len(labels)
    if classes_observed < 2:
        metrics.verdict = "NOT_QUALIFIED_SINGLE_CLASS_CORPUS"
        return metrics

    metrics.verdict = (
        "QUALIFIED" if (discrimination["passed"] and calibration["brier_passed"]
                        and calibration["ece_passed"]
                        and margin["aggregate_passed"])
        else "NOT_QUALIFIED")
    return metrics


__all__ = ["FrameConstructionError", "PredictionArtifact", "QualificationMetrics",
           "TaskContext", "build_frame", "compute_metrics",
           "prediction_from_judgment"]
