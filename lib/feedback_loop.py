"""DigitalPsychology feedback loop — executable contracts.

Implements the review's DP sections as a minimal, dependency-free plane:
- event plane: canonical schema (FormatChecker-validated) parse path, sink
  validation on emit, actor vs behavioral-subject, bounded secret-safe
  payload, NDJSON event sink (per-task, 0600) + SQLite WAL store
- episode builder: relationship-based segmentation (parent/supersedes/
  invalidates), per-task/subject chronological sorting, climactic pattern
  labeling that never stamps a cross-task pattern onto constituent segments
- probe evaluators: registered deterministic evaluators over real events with
  tri-state outcomes (PASS/FAIL/NOT_APPLICABLE); `validate()` computes
  baseline/intervention/holdout effect from applicable trials only
- guard lifecycle: explicit transition graph with immutable receipt gates
- guard compile: eligibility (active + deterministic canary), explicit
  supersedes, supersession-cycle detection, conflict check on the remaining
  set, priority/budget at task composition
- PolicySnapshot (immutable) + EmergencyOverride (separate)
- retention: full fidelity for critical transitions, stable hash sampling for
  routine events, bounded raw store

FrameWorks consumes only the compiled artifact (compiled-guard-pack.json);
it never imports this module.
"""
from __future__ import annotations

import hashlib
import copy
import fcntl
import json
import os
import re
import sqlite3
import tempfile
import uuid
from importlib.resources import files as resource_files
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Protocol, Tuple

from .guard_contracts import guard_semantic_hash
from .events import CATEGORIES, SCHEMA_VERSION
from .policy import ALLOWED_TRANSITIONS, GUARD_STATUS, PRIORITY
from .receipts import (DEFAULT_EXPERIMENT_CRITERION, DEFAULT_PRODUCTION_CRITERION,
                       MIN_EVIDENCE_SAMPLES,
                       RECEIPT_BUILDER_VERSION, RECEIPT_PROVENANCE_VERSION,
                       is_production_grade_criterion, wilson_interval)
from .versions import (GUARD_REGISTRY_SCHEMA_VERSION,
                       RECEIPT_REGISTRY_SCHEMA_VERSION)

try:
    import jsonschema
    from jsonschema import Draft202012Validator, FormatChecker
    HAS_JSONSCHEMA = True
except ImportError:  # pragma: no cover
    HAS_JSONSCHEMA = False

def trusted_state_root() -> Path:
    """Control-plane state outside the agent-editable repository."""
    base = os.environ.get("DIGITALPSYCHOLOGY_STATE_ROOT")
    if base:
        return Path(base)
    xdg = os.environ.get("XDG_STATE_HOME")
    return (Path(xdg) if xdg else Path.home() / ".local" / "state") / "digitalpsychology"


def default_registry_path() -> Path:
    return trusted_state_root() / "guards.json"


def default_receipts_path() -> Path:
    return trusted_state_root() / "receipts.json"


def _durable_json_write(path: Path, data: Dict[str, Any], *,
                        expected_revision: Optional[int] = None) -> None:
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except PermissionError:
        pass
    lock_path = path.with_name(f".{path.name}.lock")
    lock_path.touch(mode=0o600, exist_ok=True)
    os.chmod(lock_path, 0o600)
    with lock_path.open("r+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        current_revision = 0
        if path.exists():
            try:
                current_revision = int(json.loads(path.read_text(encoding="utf-8")).get(
                    "revision", 0) or 0)
            except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
                raise ReceiptError(f"trusted state revision is unreadable: {path}") from exc
        if expected_revision is not None and current_revision != expected_revision:
            raise ReceiptError(f"trusted state changed concurrently: {path}")
        fd, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
        try:
            payload = json.dumps(data, indent=2) + "\n"
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
            try:
                directory_fd = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                pass
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)

# BCE: behavioral tri-state outcomes.
PASS = "PASS"
FAIL = "FAIL"
NOT_APPLICABLE = "NOT_APPLICABLE"
TRI_STATE = {PASS, FAIL, NOT_APPLICABLE}


class EventError(ValueError):
    pass


class ReceiptError(ValueError):
    pass


@dataclass(frozen=True)
class TrialEvidence:
    """Host-produced, immutable evidence for one causal trial."""

    trial_id: str
    trajectory_id: str
    probe_id: str
    probe_version: str
    policy_hash: str
    execution_policy_hash: str
    session_id: str
    task_id: str
    attempt_id: str
    outcome: str
    host_evidence_ref: str

    def __post_init__(self) -> None:
        for name in (
            "trial_id", "trajectory_id", "probe_id", "probe_version",
            "policy_hash", "execution_policy_hash", "session_id", "task_id",
            "attempt_id", "host_evidence_ref",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ReceiptError(f"trial evidence requires non-empty {name}")
        if self.outcome not in TRI_STATE:
            raise ReceiptError(f"unknown trial outcome {self.outcome!r}")

    def to_record(self) -> Tuple[str, ...]:
        return (
            self.trial_id, self.trajectory_id, self.probe_id, self.probe_version,
            self.policy_hash, self.execution_policy_hash, self.session_id,
            self.task_id, self.attempt_id, self.host_evidence_ref, self.outcome,
        )


def _research_trial_evidence(trial_id: str, value: Any) -> TrialEvidence:
    """Wrap legacy scalar output without making it production evidence."""
    outcome = PASS if value is True else FAIL if value is False else value
    if outcome not in TRI_STATE:
        raise ReceiptError(f"unknown research trial outcome {outcome!r}")
    marker = f"research-only:{trial_id}"
    return TrialEvidence(
        trial_id=trial_id, trajectory_id=marker, probe_id=marker,
        probe_version="research-only", policy_hash=marker,
        execution_policy_hash=marker, session_id=marker, task_id=marker,
        attempt_id=marker, outcome=str(outcome), host_evidence_ref=marker)




@dataclass(frozen=True)
class ValidationReceipt:
    """Immutable evidentiary receipt. `experiment -> validated` requires a
    receipt the registry verifies; `canary -> active` requires a canary
    receipt. An agent can never certify its own rule by setting booleans."""
    receipt_id: str
    guard_key: str
    kind: str  # validation | canary | rollback
    evaluator_version_hash: str
    baseline_trial_ids: Tuple[str, ...] = ()
    intervention_trial_ids: Tuple[str, ...] = ()
    holdout_trial_ids: Tuple[str, ...] = ()
    baseline_count: int = 0
    intervention_count: int = 0
    holdout_count: int = 0
    baseline_rate: float = 0.0
    intervention_rate: float = 0.0
    effect: float = 0.0
    regressions: Tuple[str, ...] = ()
    environment: str = ""
    model: Optional[str] = None
    harness: Optional[str] = None
    evidence_hash: str = ""
    created_at: str = ""
    guard_semantic_hash: str = ""
    baseline_policy_hashes: Tuple[str, ...] = ()
    treatment_policy_hashes: Tuple[str, ...] = ()
    holdout_policy_hashes: Tuple[str, ...] = ()
    baseline_outcomes: Tuple[Tuple[str, str], ...] = ()
    intervention_outcomes: Tuple[Tuple[str, str], ...] = ()
    holdout_outcomes: Tuple[Tuple[str, str], ...] = ()
    # (trial_id, trajectory_id, probe_id, probe_version, policy_hash,
    #  execution_policy_hash, session_id, task_id, attempt_id,
    #  host_evidence_ref, outcome)
    baseline_trials: Tuple[Tuple[str, ...], ...] = ()
    intervention_trials: Tuple[Tuple[str, ...], ...] = ()
    holdout_trials: Tuple[Tuple[str, ...], ...] = ()
    baseline_ci_lower: Optional[float] = None
    baseline_ci_upper: Optional[float] = None
    intervention_ci_lower: Optional[float] = None
    intervention_ci_upper: Optional[float] = None
    provenance_version: str = RECEIPT_PROVENANCE_VERSION
    research_only: bool = False
    content_hash: str = ""
    builder_version: str = ""
    criterion: Mapping[str, Any] = field(default_factory=lambda: dict(DEFAULT_EXPERIMENT_CRITERION))

    def __post_init__(self) -> None:
        object.__setattr__(self, "receipt_id", self.receipt_id or f"rcpt-{uuid.uuid4().hex[:12]}")
        object.__setattr__(self, "created_at", self.created_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
        for name in ("baseline_trial_ids", "intervention_trial_ids", "holdout_trial_ids",
                     "regressions", "baseline_policy_hashes", "treatment_policy_hashes",
                     "holdout_policy_hashes"):
            object.__setattr__(self, name, tuple(getattr(self, name) or ()))
        for name in ("baseline_outcomes", "intervention_outcomes", "holdout_outcomes"):
            object.__setattr__(self, name, tuple(tuple(item) for item in (getattr(self, name) or ())))
        for name in ("baseline_trials", "intervention_trials", "holdout_trials"):
            object.__setattr__(self, name, tuple(tuple(item) for item in (getattr(self, name) or ())))
        criterion = dict(DEFAULT_EXPERIMENT_CRITERION)
        criterion.update(dict(self.criterion or {}))
        object.__setattr__(self, "criterion", criterion)
        if self.receipt_id in ("", None):
            raise ReceiptError("receipt requires an id")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "guard_key": self.guard_key,
            "kind": self.kind,
            "evaluator_version_hash": self.evaluator_version_hash,
            "baseline_trial_ids": list(self.baseline_trial_ids),
            "intervention_trial_ids": list(self.intervention_trial_ids),
            "holdout_trial_ids": list(self.holdout_trial_ids),
            "provenance_version": self.provenance_version,
            "research_only": self.research_only,
            "baseline_count": self.baseline_count,
            "intervention_count": self.intervention_count,
            "holdout_count": self.holdout_count,
            "baseline_rate": round(self.baseline_rate, 6),
            "intervention_rate": round(self.intervention_rate, 6),
            "effect": round(self.effect, 6),
            "regressions": list(self.regressions),
            "environment": self.environment,
            "model": self.model,
            "harness": self.harness,
            "evidence_hash": self.evidence_hash,
            "created_at": self.created_at,
            "guard_semantic_hash": self.guard_semantic_hash,
            "baseline_policy_hashes": list(self.baseline_policy_hashes),
            "treatment_policy_hashes": list(self.treatment_policy_hashes),
            "holdout_policy_hashes": list(self.holdout_policy_hashes),
            "baseline_outcomes": [list(item) for item in self.baseline_outcomes],
            "intervention_outcomes": [list(item) for item in self.intervention_outcomes],
            "holdout_outcomes": [list(item) for item in self.holdout_outcomes],
            "baseline_trials": [list(item) for item in self.baseline_trials],
            "intervention_trials": [list(item) for item in self.intervention_trials],
            "holdout_trials": [list(item) for item in self.holdout_trials],
            "baseline_ci_lower": self.baseline_ci_lower,
            "baseline_ci_upper": self.baseline_ci_upper,
            "intervention_ci_lower": self.intervention_ci_lower,
            "intervention_ci_upper": self.intervention_ci_upper,
            "content_hash": self.content_hash,
            "builder_version": self.builder_version,
            "criterion": dict(self.criterion),
        }


def _receipt_content_hash(receipt: ValidationReceipt) -> str:
    body = json.dumps({k: v for k, v in receipt.to_dict().items()
                       if k != "content_hash"}, sort_keys=True,
                      separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


class ReceiptBuilder:
    """Build receipts from exact trial outputs and immutable policy pins.

    A receipt never accepts caller-authored rates as evidence. The builder
    recomputes applicable counts, rates, effect, confidence intervals, and a
    digest over the complete trial/provenance set. Registry verification later
    repeats those calculations from the frozen receipt fields.
    """

    def __init__(self, *, evaluator_version_hash: str, guard_semantic_hash: str,
                 criterion: Optional[Mapping[str, Any]] = None,
                 research_only: bool = False) -> None:
        if not evaluator_version_hash or not guard_semantic_hash:
            raise ReceiptError("receipt builder requires evaluator and guard semantic hashes")
        self.evaluator_version_hash = evaluator_version_hash
        self.guard_semantic_hash = guard_semantic_hash
        if criterion is DEFAULT_PRODUCTION_CRITERION:
            supplied_criterion = dict(DEFAULT_PRODUCTION_CRITERION)
        else:
            supplied_criterion = dict(criterion or {})
            if criterion is not None and supplied_criterion == DEFAULT_PRODUCTION_CRITERION:
                # A copied production-looking mapping is not the trusted
                # built-in criterion.  Keep it visibly caller-authored so
                # promotion gates cannot accept a forged production policy.
                supplied_criterion["caller_authored"] = True
        self.criterion = dict(DEFAULT_EXPERIMENT_CRITERION)
        self.criterion.update(supplied_criterion)
        self.research_only = bool(research_only)
        if self.research_only:
            self.criterion["research_only"] = True
        if not self.criterion.get("version") or not self.criterion.get("evaluator_version"):
            raise ReceiptError("promotion criteria require version and evaluator_version")

    @staticmethod
    def _group(ids: Iterable[str], outputs: Mapping[str, Any],
               *, policy_hashes: Tuple[str, ...], research_only: bool
               ) -> Tuple[Tuple[str, ...], Tuple[Tuple[str, str], ...],
                          Tuple[Tuple[str, ...], ...]]:
        trial_ids = tuple(ids)
        if not trial_ids or len(set(trial_ids)) != len(trial_ids):
            raise ReceiptError("trial IDs must be non-empty and unique within a cohort")
        if any(trial_id not in outputs for trial_id in trial_ids):
            missing = [trial_id for trial_id in trial_ids if trial_id not in outputs]
            raise ReceiptError(f"missing probe outputs for trial IDs: {missing}")
        normalized: List[Tuple[str, str]] = []
        records: List[Tuple[str, ...]] = []
        for trial_id in trial_ids:
            value = outputs[trial_id]
            if isinstance(value, TrialEvidence):
                evidence = value
            elif research_only:
                evidence = _research_trial_evidence(trial_id, value)
            else:
                raise ReceiptError(
                    f"trial {trial_id!r} must be a host-produced TrialEvidence object")
            if evidence.trial_id != trial_id:
                raise ReceiptError(f"trial {trial_id!r} evidence has a different trial_id")
            normalized.append((trial_id, evidence.outcome))
            trial_policy = evidence.policy_hash
            execution_policy = evidence.execution_policy_hash
            if not policy_hashes and not research_only:
                raise ReceiptError("production cohorts require declared policy identities")
            if trial_policy not in policy_hashes:
                raise ReceiptError(
                    f"trial {trial_id!r} claims policy {trial_policy!r}, "
                    f"outside its declared cohort policies {policy_hashes!r}")
            if execution_policy not in policy_hashes:
                raise ReceiptError(
                    f"trial {trial_id!r} executed under policy {execution_policy!r}, "
                    f"outside its declared cohort policies {policy_hashes!r}")
            records.append(evidence.to_record())
        return trial_ids, tuple(normalized), tuple(records)

    @staticmethod
    def _rate(outcomes: Iterable[Tuple[str, str]]) -> Tuple[float, int]:
        applicable = [outcome for _trial_id, outcome in outcomes if outcome != NOT_APPLICABLE]
        return ((sum(outcome == PASS for outcome in applicable) / len(applicable)) if applicable else 0.0,
                len(applicable))

    def build(self, *, guard_key: str, kind: str,
              baseline_trial_ids: Iterable[str], intervention_trial_ids: Iterable[str],
              holdout_trial_ids: Iterable[str], trial_outputs: Mapping[str, Any],
              baseline_policy_hashes: Iterable[str], treatment_policy_hashes: Iterable[str],
              holdout_policy_hashes: Iterable[str], environment: str = "",
              model: Optional[str] = None, harness: Optional[str] = None,
              receipt_id: Optional[str] = None) -> ValidationReceipt:
        if kind not in {"validation", "canary", "rollback"}:
            raise ReceiptError(f"unsupported receipt kind {kind!r}")
        baseline_policy_hashes = tuple(sorted(set(baseline_policy_hashes)))
        treatment_policy_hashes = tuple(sorted(set(treatment_policy_hashes)))
        holdout_policy_hashes = tuple(sorted(set(holdout_policy_hashes)))
        if not baseline_policy_hashes or not treatment_policy_hashes or not holdout_policy_hashes:
            raise ReceiptError("every cohort requires at least one exact policy hash")
        baseline_hash = baseline_policy_hashes[0]
        treatment_hash = treatment_policy_hashes[0]
        holdout_hash = holdout_policy_hashes[0]
        baseline_ids, baseline_outcomes, baseline_trials = self._group(
            baseline_trial_ids, trial_outputs, policy_hashes=baseline_policy_hashes,
            research_only=self.research_only)
        intervention_ids, intervention_outcomes, intervention_trials = self._group(
            intervention_trial_ids, trial_outputs, policy_hashes=treatment_policy_hashes,
            research_only=self.research_only)
        holdout_ids, holdout_outcomes, holdout_trials = self._group(
            holdout_trial_ids, trial_outputs, policy_hashes=holdout_policy_hashes,
            research_only=self.research_only)
        groups = [baseline_ids, intervention_ids, holdout_ids]
        if len(set().union(*map(set, groups))) != sum(map(len, groups)):
            raise ReceiptError("trial IDs must be disjoint across cohorts")
        all_records = [record for group in (baseline_trials, intervention_trials, holdout_trials)
                       for record in group]
        trajectory_ids = [record[1] for record in all_records]
        task_attempts = [(record[7], record[8]) for record in all_records]
        if len(set(trajectory_ids)) != len(trajectory_ids):
            raise ReceiptError("trajectory IDs must be unique across cohorts")
        if len(set(task_attempts)) != len(task_attempts):
            raise ReceiptError("task/attempt identities must be unique across cohorts")
        baseline_rate, baseline_count = self._rate(baseline_outcomes)
        intervention_rate, intervention_count = self._rate(intervention_outcomes)
        _holdout_rate, holdout_count = self._rate(holdout_outcomes)
        minimum = max(MIN_EVIDENCE_SAMPLES, int(self.criterion.get(
            "minimum_applicable_per_cohort",
            self.criterion.get("min_applicable", MIN_EVIDENCE_SAMPLES))))
        if min(baseline_count, intervention_count, holdout_count) < minimum:
            raise ReceiptError(f"each cohort needs at least {minimum} applicable trials")
        available_probes = {(record[2], record[3]) for record in
                            (*baseline_trials, *intervention_trials, *holdout_trials)}
        required_probes = list(self.criterion.get("regression_probes") or [])
        if self.criterion.get("target_probe"):
            required_probes.append(self.criterion["target_probe"])
        for probe in required_probes:
            if "@" not in str(probe):
                raise ReceiptError(f"probe criterion must be versioned: {probe!r}")
            if tuple(str(probe).rsplit("@", 1)) not in available_probes:
                raise ReceiptError(f"required evaluator probe was not run: {probe}")
        regressions = tuple(trial_id for trial_id, outcome in holdout_outcomes if outcome == FAIL)
        evidence_payload = {
            "builder_version": RECEIPT_BUILDER_VERSION,
            "guard_key": guard_key,
            "kind": kind,
            "evaluator_version_hash": self.evaluator_version_hash,
            "guard_semantic_hash": self.guard_semantic_hash,
            "baseline": list(baseline_outcomes),
            "intervention": list(intervention_outcomes),
            "holdout": list(holdout_outcomes),
            "baseline_trials": list(baseline_trials),
            "intervention_trials": list(intervention_trials),
            "holdout_trials": list(holdout_trials),
            "baseline_policy_hashes": list(baseline_policy_hashes),
            "treatment_policy_hashes": list(treatment_policy_hashes),
            "holdout_policy_hashes": list(holdout_policy_hashes),
            "environment": environment,
            "model": model,
            "harness": harness,
            "criterion": self.criterion,
        }
        evidence_hash = hashlib.sha256(_stable_json(evidence_payload).encode("utf-8")).hexdigest()
        content_addressed_id = f"rcpt-{evidence_hash[:16]}"
        if receipt_id is not None and receipt_id != content_addressed_id:
            raise ReceiptError(
                "receipt_id is content-addressed; caller-supplied IDs must equal "
                f"{content_addressed_id}")
        base_low, base_high = wilson_interval(baseline_rate, baseline_count)
        int_low, int_high = wilson_interval(intervention_rate, intervention_count)
        payload = ValidationReceipt(
            receipt_id=content_addressed_id, guard_key=guard_key, kind=kind,
            evaluator_version_hash=self.evaluator_version_hash,
            baseline_trial_ids=baseline_ids, intervention_trial_ids=intervention_ids,
            holdout_trial_ids=holdout_ids, baseline_count=baseline_count,
            intervention_count=intervention_count, holdout_count=holdout_count,
            baseline_rate=baseline_rate, intervention_rate=intervention_rate,
            effect=intervention_rate - baseline_rate, regressions=regressions,
            environment=environment, model=model, harness=harness,
            evidence_hash=evidence_hash, guard_semantic_hash=self.guard_semantic_hash,
            baseline_policy_hashes=baseline_policy_hashes,
            treatment_policy_hashes=treatment_policy_hashes,
            holdout_policy_hashes=holdout_policy_hashes,
            baseline_outcomes=baseline_outcomes, intervention_outcomes=intervention_outcomes,
            holdout_outcomes=holdout_outcomes, baseline_ci_lower=base_low,
            baseline_ci_upper=base_high, intervention_ci_lower=int_low,
            intervention_ci_upper=int_high, builder_version=RECEIPT_BUILDER_VERSION,
            baseline_trials=baseline_trials, intervention_trials=intervention_trials,
            holdout_trials=holdout_trials,
            provenance_version=RECEIPT_PROVENANCE_VERSION,
            research_only=self.research_only, criterion=self.criterion)
        return replace(payload, content_hash=_receipt_content_hash(payload))


class ReceiptRegistry:
    """Persistent store of immutable receipts. Verifies signature + required
    effect/counts for the requested kind."""

    def __init__(self, path: Optional[Path] = None, *, default_to_trusted_state: bool = False) -> None:
        self.path = path or (default_receipts_path() if default_to_trusted_state else None)
        self.revision = 0
        self._receipts: Dict[str, ValidationReceipt] = {}
        if self.path is not None and self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.revision = int(data.get("revision", 0) or 0)
            for item in data.get("receipts", []):
                rid = item.get("receipt_id") or item.get("id")
                legacy_content_hash = item.get("receipt_signature")
                rcpt = ValidationReceipt(**{k: v for k, v in item.items()
                                           if k not in {"id", "receipt_id", "receipt_signature", "content_hash"}},
                                         content_hash=item.get("content_hash") or legacy_content_hash or "",
                                         receipt_id=rid)
                self._receipts[rcpt.receipt_id] = rcpt
        self._committed_receipts = dict(self._receipts)
        self._committed_revision = self.revision

    def save(self) -> None:
        if self.path is None:
            return
        data = {"schema_version": RECEIPT_REGISTRY_SCHEMA_VERSION,
                "revision": self.revision + 1,
                "receipts": [r.to_dict() for r in sorted(self._receipts.values(), key=lambda r: r.receipt_id)]}
        try:
            _durable_json_write(self.path, data, expected_revision=self.revision)
        except Exception:
            # A failed CAS/write must not leave the live object claiming
            # authority that durable storage rejected.
            self._receipts = dict(self._committed_receipts)
            self.revision = self._committed_revision
            raise
        self.revision += 1
        self._committed_receipts = dict(self._receipts)
        self._committed_revision = self.revision

    def add(self, receipt: ValidationReceipt) -> None:
        if receipt.receipt_id in self._receipts:
            raise ReceiptError(f"duplicate receipt id {receipt.receipt_id}")
        if not receipt.builder_version or receipt.builder_version != RECEIPT_BUILDER_VERSION:
            raise ReceiptError("receipts must be emitted by ReceiptBuilder")
        if receipt.content_hash != _receipt_content_hash(receipt):
            raise ReceiptError("receipt content hash does not match its content")
        self._receipts[receipt.receipt_id] = receipt

    def get(self, receipt_id: str) -> Optional[ValidationReceipt]:
        return self._receipts.get(receipt_id)

    @staticmethod
    def _verify(receipt: ValidationReceipt, kind: str, guard_key: str) -> bool:
        if receipt.kind != kind or receipt.guard_key != guard_key:
            return False
        if receipt.builder_version != RECEIPT_BUILDER_VERSION:
            return False
        if receipt.research_only:
            return False
        if receipt.provenance_version != RECEIPT_PROVENANCE_VERSION:
            return False
        if receipt.content_hash != _receipt_content_hash(receipt):
            return False
        if not receipt.guard_semantic_hash:
            return False
        groups = [receipt.baseline_trial_ids, receipt.intervention_trial_ids, receipt.holdout_trial_ids]
        outcome_groups = [receipt.baseline_outcomes, receipt.intervention_outcomes, receipt.holdout_outcomes]
        if any(len(set(group)) != len(group) for group in groups):
            return False
        if len(set().union(*map(set, groups))) != sum(map(len, groups)):
            return False
        for ids, outcomes in zip(groups, outcome_groups):
            if tuple(trial_id for trial_id, _outcome in outcomes) != tuple(ids):
                return False
            if any(outcome not in TRI_STATE for _trial_id, outcome in outcomes):
                return False
        trial_groups = [receipt.baseline_trials, receipt.intervention_trials,
                        receipt.holdout_trials]
        for ids, outcomes, records in zip(groups, outcome_groups, trial_groups):
            if len(records) != len(ids):
                return False
            for record, trial_id, outcome_pair in zip(records, ids, outcomes):
                _outcome_id, outcome = outcome_pair
                if len(record) != 11 or record[0] != trial_id or record[10] != outcome:
                    return False
                if not all(isinstance(value, str) and value for value in record[1:]):
                    return False
        declared_policy_groups = [
            set(receipt.baseline_policy_hashes),
            set(receipt.treatment_policy_hashes),
            set(receipt.holdout_policy_hashes),
        ]
        for records, allowed in zip(trial_groups, declared_policy_groups):
            if not allowed or any(record[4] not in allowed or record[5] not in allowed
                                  for record in records):
                return False
        all_records = [record for group in trial_groups for record in group]
        if len({record[1] for record in all_records}) != len(all_records):
            return False
        if len({(record[7], record[8]) for record in all_records}) != len(all_records):
            return False
        applicable_counts = [sum(outcome != NOT_APPLICABLE for _trial_id, outcome in outcomes)
                             for outcomes in outcome_groups]
        if [receipt.baseline_count, receipt.intervention_count, receipt.holdout_count] != applicable_counts:
            return False
        if min(applicable_counts) < MIN_EVIDENCE_SAMPLES:
            return False
        def computed_rate(outcomes: Tuple[Tuple[str, str], ...]) -> float:
            applicable = [outcome for _trial_id, outcome in outcomes if outcome != NOT_APPLICABLE]
            return sum(outcome == PASS for outcome in applicable) / len(applicable) if applicable else 0.0
        baseline_rate = computed_rate(receipt.baseline_outcomes)
        intervention_rate = computed_rate(receipt.intervention_outcomes)
        if abs(receipt.baseline_rate - baseline_rate) > 1e-9:
            return False
        if abs(receipt.intervention_rate - intervention_rate) > 1e-9:
            return False
        if not all(0 <= value <= 1 for value in (receipt.baseline_rate, receipt.intervention_rate)):
            return False
        if abs(receipt.effect - (receipt.intervention_rate - receipt.baseline_rate)) > 1e-9:
            return False
        criterion = dict(DEFAULT_EXPERIMENT_CRITERION)
        criterion.update(dict(receipt.criterion or {}))
        if not criterion.get("version") or not criterion.get("evaluator_version"):
            return False
        trial_records = [record for group in trial_groups for record in group]
        available_probes = {(record[2], record[3]) for record in trial_records}
        target_probe = criterion.get("target_probe")
        required_probes = list(criterion.get("regression_probes") or [])
        if target_probe:
            required_probes.append(target_probe)
        for probe in required_probes:
            if "@" not in str(probe):
                return False
            probe_id, probe_version = str(probe).rsplit("@", 1)
            if (probe_id, probe_version) not in available_probes:
                return False
        minimum = max(MIN_EVIDENCE_SAMPLES, int(criterion.get(
            "minimum_applicable_per_cohort",
            criterion.get("min_applicable", MIN_EVIDENCE_SAMPLES))))
        if min(applicable_counts) < minimum:
            return False
        if kind != "rollback" and receipt.effect <= float(criterion.get("min_effect", 0.0)):
            return False
        if kind != "rollback" and criterion.get("confidence_rule") == "wilson_lower_bound_positive":
            low, _high = wilson_interval(intervention_rate, applicable_counts[1])
            if low is None or low <= 0:
                return False
        elif kind != "rollback" and criterion.get("confidence_rule") == "wilson_intervention_gt_baseline":
            int_low, _ = wilson_interval(intervention_rate, applicable_counts[1])
            _, base_high = wilson_interval(baseline_rate, applicable_counts[0])
            if int_low is None or base_high is None or int_low <= base_high:
                return False
        if not receipt.evidence_hash or not receipt.evaluator_version_hash:
            return False
        if not receipt.content_hash:
            return False
        if not receipt.baseline_policy_hashes or not receipt.treatment_policy_hashes or not receipt.holdout_policy_hashes:
            return False
        expected_evidence_payload = {
            "builder_version": receipt.builder_version,
            "guard_key": receipt.guard_key,
            "kind": receipt.kind,
            "evaluator_version_hash": receipt.evaluator_version_hash,
            "guard_semantic_hash": receipt.guard_semantic_hash,
            "baseline": list(receipt.baseline_outcomes),
            "intervention": list(receipt.intervention_outcomes),
            "holdout": list(receipt.holdout_outcomes),
            "baseline_trials": [list(item) for item in receipt.baseline_trials],
            "intervention_trials": [list(item) for item in receipt.intervention_trials],
            "holdout_trials": [list(item) for item in receipt.holdout_trials],
            "baseline_policy_hashes": list(receipt.baseline_policy_hashes),
            "treatment_policy_hashes": list(receipt.treatment_policy_hashes),
            "holdout_policy_hashes": list(receipt.holdout_policy_hashes),
            "environment": receipt.environment,
            "model": receipt.model,
            "harness": receipt.harness,
            "criterion": dict(receipt.criterion),
        }
        if receipt.evidence_hash != hashlib.sha256(
                _stable_json(expected_evidence_payload).encode("utf-8")).hexdigest():
            return False
        expected_regressions = tuple(trial_id for trial_id, outcome in receipt.holdout_outcomes
                                     if outcome == FAIL)
        if tuple(receipt.regressions) != expected_regressions:
            return False
        if kind == "validation":
            return receipt.intervention_rate > receipt.baseline_rate
        if kind == "canary":
            return receipt.effect > 0 and not receipt.regressions
        if kind == "rollback":
            return (receipt.effect < 0
                    and bool(receipt.regressions))
        return False

    def verify(self, receipt_id: str, kind: str, guard_key: str) -> bool:
        rcpt = self._receipts.get(receipt_id)
        if rcpt is None:
            return False
        return self._verify(rcpt, kind, guard_key)


# ---------------------------------------------------------------- events

@dataclass
class Event:
    event_id: str
    timestamp: str
    task_id: str
    agent_id: str
    category: str
    event_type: str
    namespace_id: str = "default"
    application_id: str = "unknown-application"
    application_version: Optional[str] = None
    application_instance_id: Optional[str] = None
    provider_id: Optional[str] = None
    model_id: Optional[str] = None
    model_revision: Optional[str] = None
    model_capability_hash: Optional[str] = None
    harness_id: Optional[str] = None
    harness_version: Optional[str] = None
    agent_instance_id: Optional[str] = None
    session_id: Optional[str] = None
    attempt_id: str = "attempt-1"
    segment_id: str = "segment-0"
    behavioral_subject: Optional[str] = None
    actor_id: Optional[str] = None
    parent_event: Optional[str] = None
    subject: Optional[str] = None
    input_state: Optional[str] = None
    output_state: Optional[str] = None
    evidence_refs: List[str] = field(default_factory=list)
    uncertainty: Optional[float] = None
    authority_source: Optional[str] = None
    scope: Optional[str] = None
    supersedes: Optional[str] = None
    invalidates: Optional[str] = None
    interaction_id: Optional[str] = None
    parent_session_id: Optional[str] = None
    delegator_agent_id: Optional[str] = None
    delegate_agent_id: Optional[str] = None
    delegation_id: Optional[str] = None
    role: Optional[str] = None
    payload: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.namespace_id or not self.application_id:
            raise EventError("event requires namespace_id and application_id")
        if self.application_instance_id is None:
            self.application_instance_id = self.application_id
        if self.actor_id is None:
            self.actor_id = self.agent_id
        if self.agent_instance_id is None:
            self.agent_instance_id = self.agent_id
        if self.session_id is None:
            # Legacy in-memory fixtures remain parseable, but every emitted
            # event still carries an explicit session boundary.
            self.session_id = f"session-legacy-{self.task_id}"
        if self.behavioral_subject is None:
            self.behavioral_subject = self.subject or f"agent:{self.agent_id}"

    def to_dict(self) -> Dict[str, Any]:
        """Canonical serialization. Payload stays a nested field — it can
        never overwrite canonical envelope fields."""
        d = {
            "schema_version": SCHEMA_VERSION,
            "event_id": self.event_id,
            "timestamp": self.timestamp,
            "namespace_id": self.namespace_id,
            "application_id": self.application_id,
            "application_instance_id": self.application_instance_id,
            "agent_instance_id": self.agent_instance_id,
            "session_id": self.session_id,
            "task_id": self.task_id,
            "attempt_id": self.attempt_id,
            "segment_id": self.segment_id,
            "agent_id": self.agent_id,
            "behavioral_subject": self.behavioral_subject,
            "actor_id": self.actor_id or self.agent_id,
            "category": self.category,
            "event_type": self.event_type,
        }
        for k in ("application_version", "provider_id", "model_id", "model_revision",
                  "model_capability_hash", "harness_id", "harness_version"):
            value = getattr(self, k)
            if value is not None:
                d[k] = value
        for k in ("parent_event", "subject", "input_state", "output_state", "evidence_refs",
                  "uncertainty", "authority_source", "scope", "supersedes", "invalidates",
                  "interaction_id", "parent_session_id", "delegator_agent_id",
                  "delegate_agent_id", "delegation_id", "role"):
            v = getattr(self, k)
            if v not in (None, []):
                d[k] = v
        if self.payload:
            d["payload"] = dict(self.payload)
        return d


class EventSchema:
    """Single authoritative parse path: raw JSON -> schema validator with
    FormatChecker -> Event.from_validated_dict."""

    _schema: Optional[Dict[str, Any]] = None

    @classmethod
    def load(cls) -> Dict[str, Any]:
        if cls._schema is None:
            resource = None
            for package in ("digital_psychology", "lib"):
                try:
                    candidate = resource_files(package).joinpath("schemas/behavior-event.schema.json")
                    if candidate.is_file():
                        resource = candidate
                        break
                except (ModuleNotFoundError, FileNotFoundError):
                    continue
            if resource is None:
                resource = Path(__file__).resolve().parents[1] / "schemas" / "behavior-event.schema.json"
            cls._schema = json.loads(resource.read_text(encoding="utf-8"))
        return cls._schema

    @classmethod
    def validate(cls, data: Dict[str, Any]) -> None:
        schema = cls.load()
        if not HAS_JSONSCHEMA:  # pragma: no cover
            raise EventError("jsonschema not available; cannot validate events")
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        errors = sorted(validator.iter_errors(data), key=lambda e: list(e.path))
        if errors:
            raise EventError(errors[0].message)
        _parse_timestamp(data["timestamp"])


def _parse_timestamp(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception as exc:
        raise EventError(f"invalid timestamp {value!r}: {exc}") from exc


@dataclass(frozen=True)
class EventSink(Protocol):
    """Out-of-band persistence for telemetry. Implementations must
    schema-validate each event before persisting."""

    def emit(self, event: Event) -> None: ...


class NDJSONSink:
    """Per-task append-only NDJSON with restrictive permissions. Validates
    each event against the schema before appending — direct Event(...)
    construction cannot bypass the authoritative parse path."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, event: Event) -> None:
        data = event.to_dict()
        EventSchema.validate(data)
        with self.path.open("a", encoding="utf-8") as fh:
            os.chmod(self.path, 0o600)
            fh.write(json.dumps(data) + "\n")


class SQLiteSink:
    """Concurrent-writer-safe event store using SQLite WAL.

    Event identity is scoped to the producer origin.  Replay of the same
    origin/event payload is idempotent; the same event id from another origin
    is not treated as globally unique.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), timeout=30)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("""CREATE TABLE IF NOT EXISTS events (
            row_id INTEGER PRIMARY KEY AUTOINCREMENT,
            namespace_id TEXT NOT NULL,
            application_id TEXT NOT NULL,
            application_instance_id TEXT NOT NULL,
            provider_id TEXT,
            model_id TEXT,
            model_revision TEXT,
            model_capability_hash TEXT,
            harness_id TEXT,
            harness_version TEXT,
            event_id TEXT NOT NULL,
            agent_instance_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            attempt_id TEXT NOT NULL,
            segment_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            behavioral_subject TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            payload TEXT NOT NULL,
            UNIQUE(namespace_id, application_instance_id, event_id)
        )""")
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(events)")}
        if "row_id" not in columns:
            # The pre-origin schema used event_id as the global primary key.
            # Rebuild it transactionally so two producer origins can reuse an
            # event id without losing the legacy rows.
            self._conn.execute("ALTER TABLE events RENAME TO events_legacy")
            self._conn.execute("""CREATE TABLE events (
                row_id INTEGER PRIMARY KEY AUTOINCREMENT,
                namespace_id TEXT NOT NULL,
                application_id TEXT NOT NULL,
                application_instance_id TEXT NOT NULL,
                provider_id TEXT,
                model_id TEXT,
                model_revision TEXT,
                model_capability_hash TEXT,
                harness_id TEXT,
                harness_version TEXT,
                event_id TEXT NOT NULL,
                agent_instance_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                attempt_id TEXT NOT NULL,
                segment_id TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                behavioral_subject TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                payload TEXT NOT NULL,
                UNIQUE(namespace_id, application_instance_id, event_id)
            )""")
            legacy_rows = self._conn.execute(
                "SELECT event_id, agent_instance_id, session_id, task_id, attempt_id, "
                "segment_id, agent_id, behavioral_subject, timestamp, payload "
                "FROM events_legacy").fetchall()
            for row in legacy_rows:
                (event_id, agent_instance_id, session_id, task_id, attempt_id,
                 segment_id, agent_id, behavioral_subject, timestamp, payload) = row
                try:
                    payload_data = json.loads(payload)
                except (TypeError, json.JSONDecodeError):
                    payload_data = {}
                if not isinstance(payload_data, dict):
                    payload_data = {}
                namespace_id = str(payload_data.get("namespace_id") or "default")
                application_id = str(payload_data.get("application_id") or "unknown-application")
                application_instance_id = str(
                    payload_data.get("application_instance_id") or application_id)
                for name, value in (("namespace_id", namespace_id),
                                    ("application_id", application_id),
                                    ("application_instance_id", application_instance_id),
                                    ("actor_id", agent_id)):
                    payload_data.setdefault(name, value)
                normalized_payload = json.dumps(payload_data, sort_keys=True)
                self._conn.execute(
                    "INSERT INTO events (namespace_id, application_id, application_instance_id, "
                    "event_id, agent_instance_id, session_id, task_id, attempt_id, segment_id, "
                    "agent_id, behavioral_subject, timestamp, payload) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (namespace_id, application_id, application_instance_id, event_id,
                     agent_instance_id, session_id, task_id, attempt_id, segment_id,
                     agent_id, behavioral_subject, timestamp, normalized_payload))
            self._conn.execute("DROP TABLE events_legacy")
            columns = {row[1] for row in self._conn.execute("PRAGMA table_info(events)")}
        for name, default in (("namespace_id", "default"),
                              ("application_id", "unknown-application"),
                              ("application_instance_id", "unknown-application"),
                              ("agent_instance_id", "legacy"),
                              ("session_id", "legacy"),
                              ("attempt_id", "attempt-1"),
                              ("segment_id", "segment-0"),
                              ("behavioral_subject", "legacy")):
            if name not in columns:
                self._conn.execute(
                    f"ALTER TABLE events ADD COLUMN {name} TEXT NOT NULL DEFAULT {json.dumps(default)}")
        for name in ("provider_id", "model_id", "model_revision",
                     "model_capability_hash", "harness_id", "harness_version"):
            if name not in columns:
                self._conn.execute(f"ALTER TABLE events ADD COLUMN {name} TEXT")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_events_task ON events (task_id)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_events_agent ON events (agent_id)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_events_session ON events (agent_instance_id, session_id, task_id, attempt_id)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_events_origin ON events (namespace_id, application_id, application_instance_id, provider_id, model_id, model_revision, harness_id, harness_version)")
        self._conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_events_origin ON events (namespace_id, application_instance_id, event_id)")
        self._conn.commit()

    def emit(self, event: Event) -> None:
        data = event.to_dict()
        EventSchema.validate(data)
        payload = json.dumps(data, sort_keys=True)
        origin = (data["namespace_id"], data["application_instance_id"], data["event_id"])
        try:
            existing = self._conn.execute(
                "SELECT payload FROM events WHERE namespace_id = ? AND application_instance_id = ? AND event_id = ?",
                origin).fetchone()
            if existing is not None:
                if existing[0] != payload:
                    raise EventError("replayed event identity has different payload")
                return
            self._conn.execute(
                "INSERT INTO events (namespace_id, application_id, application_instance_id, provider_id, model_id, model_revision, model_capability_hash, harness_id, harness_version, event_id, agent_instance_id, session_id, task_id, attempt_id, segment_id, agent_id, behavioral_subject, timestamp, payload) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (data["namespace_id"], data["application_id"], data["application_instance_id"],
                 data.get("provider_id"), data.get("model_id"), data.get("model_revision"),
                 data.get("model_capability_hash"), data.get("harness_id"), data.get("harness_version"),
                 data["event_id"], data["agent_instance_id"], data["session_id"],
                 data["task_id"], data["attempt_id"], data["segment_id"],
                 data["agent_id"], data["behavioral_subject"], data["timestamp"], payload),
            )
            self._conn.commit()
        except sqlite3.IntegrityError as exc:
            raise EventError(
                f"event identity collision for origin {origin!r}") from exc

    def close(self) -> None:
        self._conn.close()


class EventFactory:
    @classmethod
    def from_validated_dict(cls, data: Dict[str, Any]) -> "Event":
        """Constructs an Event from data that already passed EventSchema."""
        try:
            category = data["category"]
            event_type = data["event_type"]
        except KeyError as exc:
            raise EventError(f"missing required field {exc}") from exc
        if category not in CATEGORIES:
            raise EventError(f"unknown category {category!r}")
        if not event_type or not isinstance(event_type, str):
            raise EventError("event_type must be a non-empty string")
        return Event(
            event_id=data["event_id"],
            timestamp=data["timestamp"],
            namespace_id=data.get("namespace_id", "default"),
            application_id=data.get("application_id", "unknown-application"),
            application_version=data.get("application_version"),
            application_instance_id=data.get("application_instance_id"),
            provider_id=data.get("provider_id"),
            model_id=data.get("model_id"),
            model_revision=data.get("model_revision"),
            model_capability_hash=data.get("model_capability_hash"),
            harness_id=data.get("harness_id"),
            harness_version=data.get("harness_version"),
            agent_instance_id=data.get("agent_instance_id"),
            session_id=data.get("session_id"),
            task_id=data["task_id"],
            attempt_id=data.get("attempt_id", "attempt-1"),
            segment_id=data.get("segment_id", "segment-0"),
            agent_id=data["agent_id"],
            behavioral_subject=data.get("behavioral_subject"),
            category=category,
            event_type=event_type,
            actor_id=data.get("actor_id"),
            parent_event=data.get("parent_event"),
            subject=data.get("subject"),
            input_state=data.get("input_state"),
            output_state=data.get("output_state"),
            evidence_refs=list(data.get("evidence_refs") or []),
            uncertainty=data.get("uncertainty"),
            authority_source=data.get("authority_source"),
            scope=data.get("scope"),
            supersedes=data.get("supersedes"),
            invalidates=data.get("invalidates"),
            interaction_id=data.get("interaction_id"),
            parent_session_id=data.get("parent_session_id"),
            delegator_agent_id=data.get("delegator_agent_id"),
            delegate_agent_id=data.get("delegate_agent_id"),
            delegation_id=data.get("delegation_id"),
            role=data.get("role"),
            payload=dict(data.get("payload") or {}),
        )

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Event":
        """Authoritative entry point: schema-validate then construct."""
        EventSchema.validate(data)
        return cls.from_validated_dict(data)


def load_events(path: Path) -> List[Event]:
    events = []
    seen: set = set()
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            raise EventError(f"{path.name}:{lineno}: invalid JSON: {exc}") from exc
        try:
            ev = EventFactory.from_dict(data)
        except EventError as exc:
            raise EventError(f"{path.name}:{lineno}: {exc}") from exc
        identity = (ev.namespace_id, ev.application_instance_id, ev.agent_instance_id,
                    ev.session_id, ev.task_id, ev.attempt_id, ev.behavioral_subject,
                    ev.event_id)
        if identity in seen:
            raise EventError(f"{path.name}:{lineno}: duplicate composite event identity {identity!r}")
        seen.add(identity)
        events.append(ev)
    return events


def _ordered_subsequence(pattern: List[str], sequence: List[str]) -> bool:
    it = iter(sequence)
    return all(any(p == t for t in it) for p in pattern)


# ----------------------------------------------------------------- episodes

SEGMENT_BOUNDARY_EVENTS = {"contradiction", "completion", "handoff", "state_transition"}


@dataclass
class Episode:
    episode_id: str
    task_id: str
    agent_id: str
    agent_instance_id: str = "agent"
    session_id: str = "session-unknown"
    attempt_id: str = "attempt-1"
    behavioral_subject: str = ""
    namespace_id: str = "default"
    application_instance_id: str = "unknown-application"
    role: Optional[str] = None
    interaction_id: Optional[str] = None
    delegation_id: Optional[str] = None
    events: List[Event] = field(default_factory=list)
    pattern: Optional[str] = None
    characteristic: Optional[str] = None
    summary: str = ""

    def add(self, event: Event) -> None:
        self.events.append(event)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "task_id": self.task_id,
            "agent_id": self.agent_id,
            "agent_instance_id": self.agent_instance_id,
            "session_id": self.session_id,
            "attempt_id": self.attempt_id,
            "behavioral_subject": self.behavioral_subject,
            "interaction_id": self.interaction_id,
            "pattern": self.pattern,
            "characteristic": self.characteristic,
            "summary": self.summary,
            "events": [e.to_dict() for e in self.events],
        }


class EpisodeBuilder:
    """Segments events into behavior episodes for a behavioral subject.

    Grouping key is (agent_instance_id, session_id, task_id, attempt_id,
    behavioral_subject).  Agent identity is never enough to join concurrent
    sessions, and task identity is never enough to join retry attempts.
    Operator contradictions group with the agent's episode through the same
    correlation envelope (actor_id=operator, agent_id=agent-a).

    Segmentation is relationship-driven: parent_event/supersedes/invalidates
    links bind episodes together; hard boundaries (contradiction/completion/
    handoff anchors) plus a bounded time fallback start new episodes. Events
    are sorted per (task, subject) BEFORE segmenting so concurrent telemetry
    cannot arrive out of chronological order.

    Pattern labeling is climactic: a cross-task pattern is only applied to
    the episode containing the resolution, never stamped onto every
    constituent segment. A segment containing only the original completion
    claim never inherits the stale-model-defense label."""

    def __init__(self, window_minutes: float = 30.0) -> None:
        self._all: List[Event] = []
        self.window_minutes = window_minutes

    def add(self, event: Event) -> None:
        self._all.append(event)

    @staticmethod
    def _linked(ev: Event, current: Episode) -> bool:
        """Does `ev` continue the current episode via an explicit
        relationship, even if a hard boundary would otherwise start a new
        segment?"""
        ids = {e.event_id for e in current.events}
        for rel in ("parent_event", "supersedes", "invalidates"):
            ref = getattr(ev, rel)
            if ref in ids:
                return True
            for e in current.events:
                if getattr(e, rel) == ev.event_id:
                    return True
        return False

    @staticmethod
    def _is_boundary(ev: Event) -> bool:
        if ev.category in SEGMENT_BOUNDARY_EVENTS:
            return True
        return ev.event_type in {"completion_claim", "handoff"}

    def build(self) -> List[Episode]:
        # deterministic chronological order per task/subject before
        # segmentation (concurrent telemetry may arrive out of order)
        keyed: Dict[Tuple[str, str, str, str, str], List[Event]] = {}
        for ev in self._all:
            keyed.setdefault((ev.namespace_id, ev.application_instance_id,
                             ev.agent_instance_id or ev.agent_id,
                             ev.session_id or "session-unknown",
                             ev.task_id, ev.attempt_id or "attempt-1",
                             ev.behavioral_subject or ev.subject or ev.agent_id), []).append(ev)
        out: List[Episode] = []
        counter = 0
        for (namespace_id, application_instance_id, agent_instance_id, session_id, task_id, attempt_id, behavioral_subject) in sorted(keyed):
            stream = sorted(keyed[(namespace_id, application_instance_id, agent_instance_id, session_id, task_id, attempt_id,
                                   behavioral_subject)], key=lambda e: (e.timestamp, e.event_id))
            segments: List[Episode] = []
            current: Optional[Episode] = None
            for ev in stream:
                if current is None:
                    counter += 1
                    current = Episode(
                        f"ep-{counter:03d}", task_id, stream[0].agent_id,
                        agent_instance_id, session_id, attempt_id, behavioral_subject,
                        stream[0].namespace_id, stream[0].application_instance_id, stream[0].role,
                        stream[0].interaction_id, stream[0].delegation_id)
                    current.add(ev)
                    segments.append(current)
                    continue
                boundary = self._is_boundary(ev)
                linked = self._linked(ev, current)
                if boundary and not linked:
                    counter += 1
                    current = Episode(
                        f"ep-{counter:03d}", task_id, ev.agent_id,
                        agent_instance_id, session_id, attempt_id, behavioral_subject,
                        ev.namespace_id, ev.application_instance_id, ev.role,
                        ev.interaction_id, ev.delegation_id)
                    current.add(ev)
                    segments.append(current)
                    continue
                if not boundary and current.events:
                    try:
                        prev = _parse_timestamp(current.events[-1].timestamp)
                        cur = _parse_timestamp(ev.timestamp)
                        if (cur - prev).total_seconds() > self.window_minutes * 60:
                            counter += 1
                            current = Episode(
                                f"ep-{counter:03d}", task_id, ev.agent_id,
                                agent_instance_id, session_id, attempt_id,
                                behavioral_subject, ev.namespace_id, ev.application_instance_id,
                                ev.role, ev.interaction_id, ev.delegation_id)
                            current.add(ev)
                            segments.append(current)
                            continue
                    except EventError:
                        pass
                current.add(ev)
            # climactic pattern labeling: the labeled episode is the one that
            # contains the response (fresh evidence / revision), not every
            # segment of the task.
            for segment in segments:
                sequence = [e.event_type for e in segment.events]
                if self._matches_stale_defense(segment):
                    segment.pattern = "stale_model_defense"
                    segment.characteristic = ("defends prior completion before "
                                              "fresh evidence reopens it")
                elif "completion_claim" in sequence and self._reaches_completion(segment):
                    segment.pattern = "completion_declared"
                    segment.characteristic = "completion claim anchored by boundary evidence in episode"
                elif "completion_claim" in sequence:
                    segment.pattern = "completion_possible"
                    segment.characteristic = "completion claim present (evidence not verified here)"
                segment.summary = " → ".join(sequence)
            out.extend(segments)
        return sorted(out, key=lambda e: (
            e.agent_instance_id, e.session_id, e.task_id, e.attempt_id,
            e.behavioral_subject, e.episode_id))

    def _reaches_completion(self, segment: Episode) -> bool:
        events = segment.events
        for i, ev in enumerate(events):
            if ev.event_type == "completion_claim":
                return bool(ev.evidence_refs)
        return False

    def _matches_stale_defense(self, segment: Episode) -> bool:
        sequence = [e.event_type for e in segment.events]
        return _ordered_subsequence(
            ["completion_claim", "operator_contradiction", "defense_of_prior_claim",
             "fresh_tool_use", "revised_claim"],
            sequence)


# -------------------------------------------------------------- probe eval

@dataclass
class ProbeResult:
    probe_id: str
    outcome: str  # PASS | FAIL | NOT_APPLICABLE
    evaluator: str
    notes: str = ""

    @property
    def passed(self) -> bool:
        return self.outcome == PASS


EVALUATORS: Dict[str, Callable[[List[Event]], str]] = {}


def register_evaluator(name: str):
    def deco(fn):
        EVALUATORS[name] = fn
        return fn
    return deco


def _events_after(events: List[Event], i: int) -> List[Event]:
    return events[i + 1:]


@register_evaluator("contradiction_causes_reinspection")
def _eval_reinspection(events: List[Event]) -> str:
    """A contradiction must be followed by fresh relevant inspection evidence
    before a revised decision/completion."""
    inspected_event_types = {
        "fresh_tool_use", "test_suite_result", "command_output",
        "file_read", "api_response", "repro_output",
    }
    contradictions = [ev for ev in events if ev.category == "contradiction"]
    if not contradictions:
        return NOT_APPLICABLE
    for contradiction in contradictions:
        fresh: List[Event] = []
        revised: List[Event] = []
        for ev in events:
            if ev.timestamp <= contradiction.timestamp:
                continue
            relevant = (ev.subject is None or contradiction.subject is None or
                        ev.subject == contradiction.subject)
            if relevant and (ev.category == "tool_result" or ev.event_type in inspected_event_types):
                fresh.append(ev)
            if (ev.category == "decision" and
                    (ev.output_state or ev.event_type in {"revised_claim", "revised_decision"})):
                revised.append(ev)
        if not fresh or not revised:
            return FAIL
        if min(revised, key=lambda e: e.timestamp).timestamp <= min(fresh, key=lambda e: e.timestamp).timestamp:
            return FAIL
    return PASS


@register_evaluator("completion_has_boundary_evidence")
def _eval_boundary_evidence(events: List[Event]) -> str:
    """Completion evidence must exist, precede the claim, match its subject,
    and not be invalidated."""
    completions = [ev for ev in events if ev.category == "completion"]
    if not completions:
        return NOT_APPLICABLE
    by_id = {ev.event_id: ev for ev in events}
    for completion in completions:
        if not completion.evidence_refs:
            return FAIL
        for ref in completion.evidence_refs:
            evidence = by_id.get(ref)
            if evidence is None or evidence.timestamp >= completion.timestamp:
                return FAIL
            if completion.subject and evidence.subject and evidence.subject != completion.subject:
                return FAIL
            if any(other.invalidates == ref and other.timestamp >= completion.timestamp for other in events):
                return FAIL
    return PASS


@register_evaluator("bad_action_is_structurally_blocked")
def _eval_structural_action_gate(events: List[Event]) -> str:
    """Evaluate the host gate without inspecting guard text or assignments."""
    attempts = [event for event in events
                if event.category == "action" and event.event_type == "action_requested"
                and (event.payload or {}).get("tool_type") == "write"]
    if not attempts:
        return NOT_APPLICABLE
    allowed = {"allowed", "allow"}
    return FAIL if any((event.payload or {}).get("summary") in allowed
                       for event in attempts) else PASS


@register_evaluator("retry_changes_hypothesis")
def _eval_retry_hypothesis(events: List[Event]) -> str:
    retries = [e for e in events if e.category == "retry"]
    if not retries:
        return NOT_APPLICABLE  # no retries must not inflate this probe
    hypotheses = [e for e in events if e.category == "decision" and "hypothesis" in (e.event_type or "").lower()]
    return PASS if len(retries) < 2 or len(hypotheses) >= 1 else FAIL


@register_evaluator("completion_blocked_while_unresolved_contradiction")
def _eval_completion_blocked(events: List[Event]) -> str:
    """Completion is illegal while a subject-relevant contradiction remains
    unresolved by fresh inspection evidence."""
    inspected_event_types = {
        "fresh_tool_use", "test_suite_result", "command_output",
        "file_read", "api_response", "repro_output",
    }
    unresolved: set[str] = set()
    seen_completion = False
    for ev in events:
        if ev.category == "contradiction":
            unresolved.add(ev.subject or "*")
        elif ev.category == "tool_result" or ev.event_type in inspected_event_types:
            unresolved.discard(ev.subject or "*")
            unresolved.discard("*")
        elif ev.category == "completion":
            seen_completion = True
            if ev.subject in unresolved or "*" in unresolved:
                return FAIL
    return PASS if seen_completion else NOT_APPLICABLE


class ProbeRunner:
    """Runs registered deterministic evaluators over real events with
    tri-state outcomes. `trials` and `holdout_ratio` actually drive trial
    selection: trials caps the number of evaluated trials; holdout_ratio
    splits the trial set into train/holdout deterministically."""

    def __init__(self, trials: Optional[int] = None, holdout_ratio: float = 0.2,
                 seed: int = 0) -> None:
        self.trials = trials
        self.holdout_ratio = holdout_ratio
        self.seed = seed

    def _split_episodes(self, episodes: List["Episode"]) -> Tuple[List["Episode"], List["Episode"]]:
        """Split whole behavioral episodes into train/holdout using a stable
        seeded hash of the episode identity. An episode is never divided."""
        if not episodes or self.holdout_ratio <= 0:
            return episodes, []
        count = max(0, int(round(len(episodes) * self.holdout_ratio)))
        if count == 0:
            return episodes, []
        scored = []
        for episode in episodes:
            event_ids = [event.event_id for event in episode.events]
            identity = ":".join((
                episode.task_id,
                episode.agent_id,
                event_ids[0] if event_ids else "",
                event_ids[-1] if event_ids else "",
            ))
            digest = hashlib.sha256(f"{self.seed}:{identity}".encode("utf-8")).hexdigest()
            scored.append((int(digest[:16], 16), episode))
        scored.sort(key=lambda pair: (pair[0], pair[1].episode_id))
        holdout_ids = {episode.episode_id for _score, episode in scored[:count]}
        train = [episode for episode in episodes if episode.episode_id not in holdout_ids]
        holdout = [episode for episode in episodes if episode.episode_id in holdout_ids]
        return train, holdout

    def _split(self, events: List[Event]) -> Tuple[List[Event], List[Event]]:
        """Compatibility split over a flat event list. It reconstructs whole
        episodes first, then applies the episode-level seeded split."""
        builder = EpisodeBuilder()
        for event in events:
            builder.add(event)
        train_episodes, holdout_episodes = self._split_episodes(builder.build())
        return ([event for episode in train_episodes for event in episode.events],
                [event for episode in holdout_episodes for event in episode.events])

    def run(self, probe: str, events: List[Event]) -> ProbeResult:
        if probe not in EVALUATORS:
            return ProbeResult(probe, FAIL, "unknown_evaluator", f"no registered evaluator {probe!r}")
        try:
            outcome = EVALUATORS[probe](events)
            if outcome not in TRI_STATE:
                outcome = FAIL
            return ProbeResult(probe, outcome, probe, "deterministic evaluator")
        except Exception as exc:  # pragma: no cover
            return ProbeResult(probe, FAIL, probe, str(exc))

    def run_split(self, probe: str, events: List[Event]) -> Tuple[ProbeResult, ProbeResult]:
        train, holdout = self._split(events)
        return self.run(probe, train), self.run(probe, holdout)

    def validate(self, guard: Guard, baseline_results: List[ProbeResult],
                 intervention_results: List[ProbeResult],
                 holdout_results: Optional[List[ProbeResult]] = None) -> Dict[str, Any]:
        """Effect computation over APPLICABLE trials only (NOT_APPLICABLE is
        excluded from both numerator and denominator)."""
        def applicable(results: List[ProbeResult]) -> List[ProbeResult]:
            return [r for r in results if r.outcome != NOT_APPLICABLE]

        def rate(results: List[ProbeResult]) -> float:
            app = applicable(results)
            return (sum(1 for r in app if r.passed) / max(len(app), 1)) if app else 0.0

        baseline_rate = rate(baseline_results)
        intervention_rate = rate(intervention_results)
        holdout_rate = rate(holdout_results or [] if holdout_results is not None else intervention_results)
        regressions = [r.probe_id for r in applicable(holdout_results or []) if not r.passed]
        evaluators = {r.evaluator for r in baseline_results + intervention_results + (holdout_results or [])}
        per_evaluator = []
        for evaluator in evaluators:
            per_evaluator.append((
                rate([r for r in baseline_results if r.evaluator == evaluator]),
                rate([r for r in intervention_results if r.evaluator == evaluator]),
            ))
        disagreement = len({(round(a, 6), round(b, 6)) for a, b in per_evaluator}) > 1
        applicable_counts = {
            "baseline": len(applicable(baseline_results)),
            "intervention": len(applicable(intervention_results)),
            "holdout": len(applicable(holdout_results or [])),
        }
        return {
            "baseline_rate": baseline_rate,
            "intervention_rate": intervention_rate,
            "effect_delta": intervention_rate - baseline_rate,
            "holdout_rate": holdout_rate,
            "regressions": regressions,
            "evaluator_disagreement": disagreement,
            "applicable_counts": applicable_counts,
        }


# ------------------------------------------------------------------- guards

@dataclass
class Guard:
    id: str
    status: str
    target_behavior: str
    rule: str
    domain: Optional[str] = None
    task_shapes: List[str] = field(default_factory=list)
    trigger: Optional[str] = None
    priority: str = "P1"
    conflicts_with: List[str] = field(default_factory=list)
    supersedes: List[str] = field(default_factory=list)
    estimated_tokens: int = 0
    validation: Dict[str, Any] = field(default_factory=dict)
    rollout: Dict[str, Any] = field(default_factory=dict)
    model: Optional[str] = None
    harness: Optional[str] = None
    version: str = "1"
    family: Optional[str] = None
    location: Optional[str] = None
    source_finding_ref: Optional[str] = None
    scope: Dict[str, Any] = field(default_factory=dict)
    cost: Optional[float] = None
    validation_receipt_ref: Optional[str] = None
    canary_receipt_ref: Optional[str] = None
    activation_receipt_ref: Optional[str] = None
    rollback_receipt_ref: Optional[str] = None
    status_history: List[Dict[str, Any]] = field(default_factory=list)
    monitoring: Dict[str, Any] = field(default_factory=dict)
    evaluator: Optional[str] = None
    enforcement_key: Optional[str] = None
    enforcement: Dict[str, Any] = field(default_factory=dict)
    _key: Optional[str] = field(default=None, init=False, repr=False)
    _enforcement_key_explicit: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.status not in GUARD_STATUS:
            raise ValueError(f"invalid guard status {self.status!r}")
        if isinstance(self.priority, int):
            self.priority = f"P{min(max(self.priority, 0), 3)}"
        if self.priority not in PRIORITY:
            raise ValueError(f"invalid guard priority {self.priority!r}")
        if self.family is None or not self.family:
            # derive from id before the version suffix
            self.family = self.id.split("@")[0]
        if self._key is None:
            self._key = f"{self.family}@{self.version}"
        object.__setattr__(self, "_enforcement_key_explicit", self.enforcement_key is not None)
        if self.enforcement_key is None:
            self.enforcement_key = self._default_enforcement_key()
        if not self.enforcement:
            structural = {
                "completion.boundary_evidence": "completion.boundary_evidence",
                "contradiction.reinspect": "contradiction.reinspect",
                "authority.boundaries": "authority.boundaries",
            }
            handler = structural.get(self.enforcement_key)
            self.enforcement = {
                "mode": "structural" if handler else "prompt",
                "handler": handler,
                "handler_version": "1",
                "parameters": {},
                "fallback_prompt": None if handler else self.rule,
            }

    def _default_enforcement_key(self) -> str:
        """Stable equivalence key; semantic text is deliberately not parsed."""
        return re.sub(r"[^a-z0-9_.-]+", "_", self.target_behavior.lower()).strip("_")

    @property
    def key(self) -> str:
        return self._key or f"{self.family}@{self.version}"

    @property
    def priority_int(self) -> int:
        return PRIORITY[self.priority]


class GuardRegistry:
    """Persistent registry keyed by versioned identity (family@version), so
    G-completion v1 active and G-completion v2 canary can coexist."""

    def __init__(self, guards: Optional[Iterable[Guard]] = None, path: Optional[Path] = None,
                 *, default_to_trusted_state: bool = False) -> None:
        self._guards: Dict[str, Guard] = {}
        self.path = path or (default_registry_path() if default_to_trusted_state else None)
        self.revision = 0
        if self.path is not None and self.path.exists():
            self._load(self.path)
        self._committed_guards = copy.deepcopy(self._guards)
        self._committed_revision = self.revision
        for g in guards or []:
            self.add(g)

    def _load(self, path: Path) -> None:
        data = json.loads(path.read_text(encoding="utf-8"))
        self.revision = int(data.get("revision", 0) or 0)
        for item in data.get("guards", []):
            clean = {k: v for k, v in item.items() if k not in {"id", "key"}}
            guard = Guard(**clean, id=item["id"])
            self._guards[guard.key] = guard

    def save(self) -> None:
        if self.path is None:
            return
        data = {"schema_version": GUARD_REGISTRY_SCHEMA_VERSION,
                "revision": self.revision + 1,
                "guards": [guard_to_dict(g) for g in sorted(self._guards.values(), key=lambda g: (g.priority_int, g.key))]}
        try:
            _durable_json_write(self.path, data, expected_revision=self.revision)
        except Exception:
            self._guards = copy.deepcopy(self._committed_guards)
            self.revision = self._committed_revision
            raise
        self.revision += 1
        self._committed_guards = copy.deepcopy(self._guards)
        self._committed_revision = self.revision

    def add(self, guard: Guard) -> None:
        """Add newly researched data; only candidates may enter here."""
        if guard.status != "candidate":
            raise ValueError(
                "GuardRegistry.add accepts candidate guards only; use the "
                "receipt-gated lifecycle to promote a guard")
        self._insert(guard)

    def _insert(self, guard: Guard) -> None:
        if guard.key in self._guards:
            raise ValueError(f"duplicate guard key {guard.key}")
        self._guards[guard.key] = guard

    def import_guard(self, guard: Guard, *, receipt_registry: ReceiptRegistry) -> None:
        """Import persisted state only after its receipt chain is verifiable."""
        if guard.status not in {"candidate", "experiment", "validated", "canary", "active"}:
            raise ValueError(f"cannot import guard in status {guard.status!r}")
        self._insert(guard)
        problems = self.audit_deployments(receipt_registry)
        if problems:
            del self._guards[guard.key]
            raise ValueError("unverifiable guard import: " + "; ".join(problems))

    def all(self) -> List[Guard]:
        return sorted(self._guards.values(), key=lambda g: (g.priority_int, g.key))

    def by_id(self, gid: str) -> Optional[Guard]:
        return self._guards.get(gid) or self._guards.get(f"{gid}@1")

    def audit_deployments(self, receipt_registry: ReceiptRegistry) -> List[str]:
        """Return deployable guards whose complete receipt chain is broken."""
        problems: List[str] = []
        for guard in self.all():
            if guard.status not in {"validated", "canary", "active"}:
                continue
            if not guard.validation_receipt_ref:
                problems.append(f"{guard.key}: missing validation receipt")
                continue
            validation = receipt_registry.get(guard.validation_receipt_ref)
            if (validation is None
                    or validation.guard_semantic_hash != guard_semantic_hash(guard)
                    or not receipt_registry.verify(guard.validation_receipt_ref, "validation", guard.key)):
                problems.append(f"{guard.key}: invalid validation receipt chain")
            if guard.status == "active":
                if not guard.activation_receipt_ref:
                    problems.append(f"{guard.key}: missing activation receipt")
                elif not receipt_registry.verify(guard.activation_receipt_ref, "canary", guard.key):
                    problems.append(f"{guard.key}: invalid activation receipt chain")
        return problems

    def transition(self, gid: str, new_status: str,
                   receipt_registry: Optional[ReceiptRegistry] = None,
                   receipt_id: Optional[str] = None) -> None:
        guard = self.by_id(gid)
        if guard is None:
            raise KeyError(f"no guard {gid}")
        if new_status not in GUARD_STATUS:
            raise ValueError(f"invalid status {new_status}")
        # Preserve the historical in-memory API: callers may hold the Guard
        # returned by by_id() and attach rollout metadata before the next
        # transition.  Durable save() already snapshots/restores the whole
        # registry on a failed CAS.
        old_status = guard.status
        allowed = ALLOWED_TRANSITIONS[old_status]
        if new_status not in allowed:
            raise ValueError(f"{gid} cannot go {guard.status} -> {new_status} (allowed: {sorted(allowed)})")
        receipts = receipt_registry or ReceiptRegistry()
        if new_status == "validated" and guard.status == "experiment":
            receipt = receipts.get(receipt_id) if receipt_id else None
            if (receipt is None or receipt.guard_semantic_hash != guard_semantic_hash(guard)
                    or not is_production_grade_criterion(dict(receipt.criterion or {}))
                    or not receipts.verify(receipt_id, "validation", guard.key)):
                raise ValueError(
                    f"{gid} experiment -> validated requires a verifiable validation receipt; "
                    f"booleans like successful_probe_record are not evidence")
        if new_status == "canary" and guard.status == "validated":
            if not guard.rollout:
                raise ValueError(f"{gid} validated -> canary requires rollout configuration")
            validation = receipts.get(guard.validation_receipt_ref) if guard.validation_receipt_ref else None
            if guard.validation_receipt_ref and (
                    validation is None
                    or validation.guard_semantic_hash != guard_semantic_hash(guard)
                    or not is_production_grade_criterion(dict(validation.criterion or {}))
                    or not receipts.verify(guard.validation_receipt_ref, "validation", guard.key)):
                raise ValueError(f"{gid} validation receipt {guard.validation_receipt_ref} is not verifiable")
        if new_status == "active" and guard.status == "canary":
            receipt = receipts.get(receipt_id) if receipt_id else None
            if (receipt is None or receipt.guard_semantic_hash != guard_semantic_hash(guard)
                    or not is_production_grade_criterion(dict(receipt.criterion or {}))
                    or not receipts.verify(receipt_id, "canary", guard.key)):
                raise ValueError(
                    f"{gid} canary -> active requires a verifiable canary receipt; "
                    f"canary_success/blocking_regression booleans are not evidence")
        guard.status = new_status
        if new_status == "validated" and receipt_id:
            guard.validation_receipt_ref = receipt_id
        elif new_status == "active" and receipt_id:
            guard.activation_receipt_ref = receipt_id
        elif new_status == "rolled_back" and receipt_id:
            guard.rollback_receipt_ref = receipt_id
        guard.status_history.append({
            "from": old_status,
            "to": new_status,
            "receipt_ref": receipt_id,
            "recorded_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        })
        self._guards[guard.key] = guard

    def promote_guard(self, gid: str, new_status: str, *,
                      receipt_registry: ReceiptRegistry,
                      receipt_id: str,
                      receipt: Optional[ValidationReceipt] = None,
                      compiled_pack_path: Optional[Path] = None,
                      compiled_pack: Optional[Mapping[str, Any]] = None) -> None:
        """Persist receipt, lifecycle status, and optional pack as one
        control-plane operation.

        The JSON files are individually atomic and protected by locks.  The
        receipt is durably written before the deployable status, so a crash
        cannot create an active guard with no supporting receipt.
        """
        stored_receipt = receipt_registry.get(receipt_id)
        if stored_receipt is None and receipt is not None:
            if receipt.receipt_id != receipt_id:
                raise ReceiptError("promotion receipt id does not match receipt_id")
            receipt_registry.add(receipt)
            stored_receipt = receipt
        if stored_receipt is None:
            raise ReceiptError(f"promotion receipt {receipt_id!r} is missing")
        guard = self.by_id(gid)
        if guard is None:
            raise KeyError(f"no guard {gid}")
        if stored_receipt.guard_semantic_hash != guard_semantic_hash(guard):
            raise ReceiptError("promotion receipt is bound to a different guard semantic hash")
        if not receipt_registry.verify(receipt_id, "canary" if new_status == "active" else "validation", guard.key):
            raise ReceiptError("promotion receipt does not satisfy the requested lifecycle gate")
        receipt_registry.save()
        self.transition(gid, new_status, receipt_registry=receipt_registry,
                        receipt_id=receipt_id)
        self.save()
        if compiled_pack_path is not None and compiled_pack is not None:
            _durable_json_write(Path(compiled_pack_path), dict(compiled_pack))


def guard_to_dict(guard: Guard) -> Dict[str, Any]:
    scope = dict(guard.scope or {})
    if guard.domain and "domains" not in scope:
        scope["domains"] = [guard.domain]
    if guard.task_shapes and "task_shapes" not in scope:
        scope["task_shapes"] = list(guard.task_shapes)
    if guard.model and "models" not in scope:
        scope["models"] = [guard.model]
    if guard.harness and "harnesses" not in scope:
        scope["harnesses"] = [guard.harness]
    return {
        "id": guard.id,
        "key": guard.key,
        "family": guard.family or guard.id.split("@")[0],
        "version": guard.version,
        "status": guard.status,
        "target_behavior": guard.target_behavior,
        "rule": guard.rule,
        "location": guard.location,
        "source_finding_ref": guard.source_finding_ref,
        "scope": scope,
        "domain": guard.domain,
        "task_shapes": list(guard.task_shapes),
        "trigger": guard.trigger,
        "priority": guard.priority,
        "conflicts_with": list(guard.conflicts_with),
        "supersedes": list(guard.supersedes),
        "estimated_tokens": guard.estimated_tokens,
        "cost": guard.cost,
        "validation_receipt_ref": guard.validation_receipt_ref,
        "canary_receipt_ref": guard.canary_receipt_ref,
        "activation_receipt_ref": guard.activation_receipt_ref,
        "rollback_receipt_ref": guard.rollback_receipt_ref,
        "status_history": list(guard.status_history),
        "validation": dict(guard.validation),
        "rollout": dict(guard.rollout),
        "monitoring": dict(guard.monitoring),
        "model": guard.model,
        "harness": guard.harness,
        "evaluator": guard.evaluator,
        "enforcement_key": guard.enforcement_key,
        "enforcement": dict(guard.enforcement),
    }


class GuardCompiler:
    """Research/conformance compiler for guard validation.

    CognitiveFrameWorks owns the deployed runtime compiler and task selection;
    this implementation remains here for DP lifecycle experiments and the
    cross-system JSON conformance fixture.

    Order:
        1. eligibility (active + deterministically-assigned canary)
        2. scope (fail-closed on model/harness/domain/task-shape restrictions
           when the runtime value is unknown)
        3. supersession (explicit metadata only; cycle detection; never
           resurrects an invalid cyclic set)
        4. conflict validation on the remaining set
        5. priority / budget (token budget belongs at task composition time)
    """

    def __init__(self, token_budget: int = 200, always_on_ids: Optional[Iterable[str]] = None,
                 canary_task_ids: Optional[Iterable[str]] = None) -> None:
        self.token_budget = token_budget
        self.always_on_ids = set(always_on_ids or [])
        self.canary_task_ids = set(canary_task_ids or [])

    @classmethod
    def canary_assigned(cls, guard: Guard, task_id: Optional[str]) -> bool:
        """Deterministic canary assignment: stable hash of (guard version +
        task id) percentile < canary_fraction. Explicit task IDs remain an
        override for tests/targeted rollout."""
        if task_id is None:
            return False
        rollout = guard.rollout or {}
        if isinstance(rollout, dict) and rollout.get("canary_task_ids"):
            if task_id in set(rollout["canary_task_ids"]):
                return True
        canary_fraction = float(rollout.get("canary_fraction", 0.0) or 0.0)
        if canary_fraction <= 0:
            return False
        key = f"{guard.key}+{task_id}".encode("utf-8")
        digest = hashlib.sha256(key).hexdigest()
        percentile = int(digest[:8], 16) / float(0xFFFFFFFF)
        return percentile < canary_fraction

    @classmethod
    def _eligible(cls, guard: Guard, task_id: Optional[str] = None) -> bool:
        if guard.status == "active":
            return True
        if guard.status == "canary":
            return cls.canary_assigned(guard, task_id)
        return False

    @classmethod
    def _scoped(cls, guard: Guard, task_domains: Iterable[str], task_shapes: Iterable[str],
                model: Optional[str], harness: Optional[str], trigger: Optional[str] = None,
                stateworks: Optional[Iterable[str]] = None) -> bool:
        """Fail-closed scope matching. A guard with a model/harness/domain/
        task-shape restriction is only eligible when the runtime value is
        known AND matches."""
        domains = set(task_domains)
        shapes = set(task_shapes)
        statework_ids = set(stateworks or ())
        scope = guard.scope or {}
        g_domains = set(scope.get("domains") or ([] if guard.domain is None else [guard.domain]))
        g_stateworks = set(scope.get("stateworks") or [])
        g_shapes = set(scope.get("task_shapes") or guard.task_shapes or [])
        g_models = set(scope.get("models") or ([] if guard.model is None else [guard.model]))
        g_harnesses = set(scope.get("harnesses") or ([] if guard.harness is None else [guard.harness]))
        if g_domains and (not domains or not (g_domains & domains)):
            return False
        if g_stateworks and (not statework_ids or not (g_stateworks & statework_ids)):
            return False
        if g_shapes and (not shapes or not (g_shapes & shapes)):
            return False
        if g_models and (not model or model not in g_models):
            return False
        if g_harnesses and (not harness or harness not in g_harnesses):
            return False
        if guard.trigger is not None and guard.trigger != trigger:
            return False
        return True

    @staticmethod
    def _resolve_ref(ref: str, guards: List[Guard]) -> Optional[str]:
        """A supersede/conflict reference may name a bare id or a versioned
        key (family@version). Resolve to the canonical key of an existing
        guard, or None when it points outside the set."""
        if ref in {g.key for g in guards}:
            return ref
        if ref in {g.id for g in guards}:
            for g in guards:
                if g.id == ref:
                    return g.key
        return None

    @classmethod
    def _conflicts(cls, guards: List[Guard]) -> List[Tuple[Guard, Guard]]:
        conflicts = []
        keys = {g.key for g in guards}
        for i, a in enumerate(guards):
            for b in guards[i + 1:]:
                a_refs = {cls._resolve_ref(r, guards) for r in a.conflicts_with}
                b_refs = {cls._resolve_ref(r, guards) for r in b.conflicts_with}
                if b.key in a_refs or a.key in b_refs:
                    conflicts.append((a, b))
        return conflicts

    @classmethod
    def _supersession_cycles(cls, guards: List[Guard]) -> List[str]:
        keys = {g.key for g in guards}
        graph: Dict[str, List[str]] = {}
        for g in guards:
            graph[g.key] = [cls._resolve_ref(s, guards)
                            for s in g.supersedes if cls._resolve_ref(s, guards) in keys and s != g.key]
        visiting: set = set()
        done: set = set()
        cycles: List[str] = []

        def dfs(node: str) -> None:
            if node in done:
                return
            if node in visiting:
                cycles.append(node)
                return
            visiting.add(node)
            for nxt in graph.get(node, []):
                dfs(nxt)
            visiting.remove(node)
            done.add(node)

        for k in keys:
            dfs(k)
        return cycles

    def compile(self, guards: List[Guard], task_domains: Iterable[str], task_shapes: Iterable[str],
                task_id: Optional[str] = None, model: Optional[str] = None,
                harness: Optional[str] = None, trigger: Optional[str] = None,
                stateworks: Optional[Iterable[str]] = None) -> Dict[str, Any]:
        # 1. eligibility
        eligible = [g for g in guards if self._eligible(g, task_id)]
        # 2. scope (fail-closed)
        scoped = [g for g in eligible if self._scoped(
            g, task_domains, task_shapes, model, harness, trigger, stateworks)]
        # Exact enforcement equivalence is data-driven. Prefer a universal
        # kernel rule (always_on_ids) and otherwise retain the highest-priority
        # version, never both as independent experiments.
        deduplicated: List[Guard] = []
        seen_enforcement: Dict[str, Guard] = {}
        for guard in sorted(scoped, key=lambda item: (
                item.id not in self.always_on_ids,
                item.priority_int, item.key)):
            key = guard.enforcement_key or guard.key
            prior = seen_enforcement.get(key)
            if prior is not None:
                continue
            seen_enforcement[key] = guard
            deduplicated.append(guard)
        scoped = deduplicated
        # 3. supersession on the scoped set (a new guard that supersedes an
        #    old conflicting guard replaces it before conflict checking)
        superseded: set = set()
        for g in scoped:
            superseded.update(g.supersedes)
        superseded_keys = {self._resolve_ref(s, scoped) or s for s in superseded}
        remaining = [g for g in scoped if g.key not in superseded_keys and g.id not in superseded]
        cycles = self._supersession_cycles(remaining)
        if cycles:
            return {"error": "supersession cycle",
                    "cycles": sorted(set(cycles)),
                    "active": [], "estimated_tokens": 0}
        families: Dict[str, List[Guard]] = {}
        for guard in remaining:
            families.setdefault(guard.family or guard.id.split("@")[0], []).append(guard)
        duplicate_families = {
            family: sorted(item.key for item in members)
            for family, members in families.items() if len(members) > 1
        }
        if duplicate_families:
            return {"error": "multiple effective guard versions without explicit supersession",
                    "families": duplicate_families,
                    "active": [], "estimated_tokens": 0}
        if not remaining:
            # supersession removed everything (cyclic/self-destructive set) —
            # never resurrect: fail compilation
            return {"error": "supersession emptied the eligible set",
                    "active": [], "estimated_tokens": 0}
        # 4. conflict validation on the remaining set
        conflicts = self._conflicts(remaining)
        if conflicts:
            return {"error": "unresolved conflicts",
                    "conflicts": [tuple(sorted((a.id, b.id))) for a, b in conflicts],
                    "active": [], "estimated_tokens": 0}
        # 5. priority / budget at task composition
        remaining.sort(key=lambda g: (g.id not in self.always_on_ids, g.priority_int, g.key))
        always_on = [g for g in remaining if g.id in self.always_on_ids]
        optional = [g for g in remaining if g.id not in self.always_on_ids]
        active: List[Guard] = []
        tokens = 0
        for g in always_on:
            tokens += max(g.estimated_tokens, 10)
            active.append(g)
        for g in optional:
            cost = max(self._measured_tokens(g.rule), g.estimated_tokens, 10)
            if tokens + cost > self.token_budget:
                continue
            active.append(g)
            tokens += cost
        estimated = sum(max(g.estimated_tokens, 10) for g in active)
        return {
            "active": [g.id for g in active],
            "estimated_tokens": estimated,
            "conflicts": [],
            "retired": [g.id for g in guards if g.status in {"stale", "rolled_back"}],
            "ineligible": [g.id for g in guards if not self._eligible(g, task_id)],
            "deduplicated": [g.id for g in eligible if g not in scoped],
            "scoped_out": [g.id for g in eligible if g not in scoped],
        }

    @staticmethod
    def _measured_tokens(rule: str) -> int:
        return max(1, (len(rule) + 3) // 4)


# ------------------------------------------------------- policy snapshot

@dataclass(frozen=True)
class PolicySnapshot:
    """Immutable pin of a task's policy. Normal task policy cannot mutate.
    A safety containment creates an EmergencyOverride record; the next task
    gets a new snapshot."""
    task_id: str
    framework_version: str
    framework_hash: str
    stateworks: Tuple[str, ...] = ()
    guard_pack_version: str = ""
    guard_pack_hash: str = ""
    model: Optional[str] = None
    harness: Optional[str] = None
    toolset: Optional[str] = None
    frozen_at: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "frozen_at", self.frozen_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "framework_version": self.framework_version,
            "framework_hash": self.framework_hash,
            "stateworks": list(self.stateworks),
            "guard_pack_version": self.guard_pack_version,
            "guard_pack_hash": self.guard_pack_hash,
            "model": self.model,
            "harness": self.harness,
            "toolset": self.toolset,
            "frozen_at": self.frozen_at,
        }


@dataclass(frozen=True)
class EmergencyOverride:
    task_id: str
    override_reason: str
    rolled_back_to_guard_pack: str
    recorded_at: str = ""


class GuardResolver:
    """Per-task resolver: pinned guard-pack version + scope/harness/model/
    trigger applicability + immutable snapshot. The signature matches the
    implementation: model/harness/trigger are passed through to the
    compiler's fail-closed scope matching."""

    def __init__(self, compiler: GuardCompiler, registry: GuardRegistry,
                 snapshot: Optional[PolicySnapshot] = None) -> None:
        self.compiler = compiler
        self.registry = registry
        self.snapshot = snapshot

    def resolve(self, task_id: str, domains: Iterable[str], shapes: Iterable[str],
                model: Optional[str] = None, harness: Optional[str] = None,
                trigger: Optional[str] = None,
                snapshot: Optional[PolicySnapshot] = None,
                stateworks: Optional[Iterable[str]] = None) -> Dict[str, Any]:
        snaps = snapshot or self.snapshot
        result = self.compiler.compile(self.registry.all(), task_domains=domains,
                                       task_shapes=shapes, task_id=task_id,
                                       model=model, harness=harness, trigger=trigger,
                                       stateworks=stateworks)
        if snaps is not None:
            result["pinned"] = snaps.to_dict()
        return result


# ------------------------------------------------------- retention

class Retention:
    """Full fidelity for critical transitions (contradiction, correction,
    completion, handoff, blocker, state_transition, high-risk mutations);
    stable hash sampling for routine events (never every-Nth, which biases
    periodic streams); bounded raw store."""

    CRITICAL = {"contradiction", "correction", "completion", "handoff", "blocker", "state_transition"}

    def __init__(self, raw_limit: int = 100_000, sample_rate: float = 0.1) -> None:
        self.raw_limit = raw_limit
        self.sample_rate = sample_rate

    @staticmethod
    def _sample_key(event: Event) -> str:
        return event.event_id or f"{event.task_id}:{event.timestamp}"

    def _sampled(self, event: Event) -> bool:
        digest = hashlib.sha256(self._sample_key(event).encode("utf-8")).hexdigest()
        bucket = int(digest[:8], 16) / float(0xFFFFFFFF)
        return bucket < self.sample_rate

    def classify(self, event: Event) -> str:
        if event.category in self.CRITICAL:
            return "full"
        if event.category == "action" and event.event_type in {"mutation", "handoff"}:
            return "full"
        if event.category in {"tool_result", "retry", "observation"}:
            return "sampled"
        return "aggregate"

    def retain(self, events: List[Event]) -> Dict[str, List[Event]]:
        full, sampled, aggregate = [], [], []
        for ev in events:
            kind = self.classify(ev)
            if kind == "full":
                full.append(ev)
            elif kind == "sampled":
                if self._sampled(ev):
                    sampled.append(ev)
            else:
                aggregate.append(ev)
        kept = full + sampled
        if len(kept) > self.raw_limit:
            kept = kept[-self.raw_limit:]
        return {"full": full, "sampled": sampled, "aggregate": aggregate, "kept": kept}
