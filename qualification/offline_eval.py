"""Deliverable D — offline route_preference evaluation harness.

Uses the PINNED checkpoint, tokenizer and encoder through DP's local inference
path. Never calls the external backend, and never substitutes a different model.

Every result is bound to checkpoint revision + content hash + StateWork contract
version, so a measurement cannot later be attributed to the wrong artifact.

This module performs NO inference on import and runs none unless `run()` is
called with an explicit local provider.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .corpus_schema import CorpusRecord
from .leakage_checks import run_all_checks
from .qualification_spec import SAMPLE_UNIT, QualificationSpec
from .route_fixtures import coverage_report

DEFAULT_CHECKPOINT = ("convaiinnovations/laya-typed-decisions")
DEFAULT_REVISION = "1a793eb568e6718f15941d08f85432581df534e3"
DEFAULT_INFERENCE_CEILING = 64

MODEL_FILES = (
    "model.safetensors", "rl_agent_config.json", "encoder/config.json",
    "tokenizer/tokenizer.json", "tokenizer/tokenizer_config.json",
)


def checkpoint_digest(model_path: str | Path) -> str:
    """Hash the exact checkpoint, config and tokenizer inputs.

    Mirrors DP's own model_content_digest contract so a qualification result is
    bound to the same artifact identity the runtime enforces.
    """
    root = Path(model_path).expanduser().resolve(strict=True)
    material: list[tuple[str, str]] = []
    for relative in MODEL_FILES:
        path = root / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Laya model artifact is missing or linked: {relative}")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        material.append((relative, digest.hexdigest()))
    return hashlib.sha256(json.dumps(
        material, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def split_by_trajectory(records: Sequence[CorpusRecord], *,
                        holdout_fraction: float = 0.34
                        ) -> tuple[tuple[CorpusRecord, ...],
                                   tuple[CorpusRecord, ...]]:
    """Split at TRAJECTORY granularity so correlated frames stay together.

    Deterministic by trajectory ID, so a rerun reproduces the same split.
    """
    if not 0.0 < holdout_fraction < 1.0:
        raise ValueError("holdout fraction must be between zero and one")
    by_trajectory: dict[str, list[CorpusRecord]] = {}
    for record in records:
        by_trajectory.setdefault(record.trajectory_id, []).append(record)
    ordered = sorted(by_trajectory)
    holdout_count = max(1, int(len(ordered) * holdout_fraction))
    # Deterministic stride selection rather than a hash of insertion order.
    holdout_ids = set(ordered[::max(1, len(ordered) // holdout_count)][:holdout_count])
    train = tuple(r for r in records if r.trajectory_id not in holdout_ids)
    holdout = tuple(r for r in records if r.trajectory_id in holdout_ids)
    return train, holdout


@dataclass
class OfflineEvalResult:
    """A version-bound measurement. Carries no claim about model quality."""

    checkpoint: str
    checkpoint_revision: str
    checkpoint_content_hash: str
    statework_contract_version: str
    inference_count: int
    inference_ceiling: int
    split: Mapping[str, int]
    leakage: Mapping[str, Any]
    fixture_coverage: Mapping[str, Any]
    sample_size: Mapping[str, Any]
    # Populated only when real predictions exist.
    discrimination: Mapping[str, Any] | None = None
    calibration: Mapping[str, Any] | None = None
    margin: Mapping[str, Any] | None = None
    incremental_utility: Mapping[str, Any] | None = None
    verdict: str = "INCONCLUSIVE_NO_INFERENCE"

    def to_dict(self) -> dict[str, Any]:
        return {
            "checkpoint": self.checkpoint,
            "checkpoint_revision": self.checkpoint_revision,
            "checkpoint_content_hash": self.checkpoint_content_hash,
            "statework_contract_version": self.statework_contract_version,
            "inference_count": self.inference_count,
            "inference_ceiling": self.inference_ceiling,
            "split": dict(self.split),
            "leakage": dict(self.leakage),
            "fixture_coverage": dict(self.fixture_coverage),
            "sample_size": dict(self.sample_size),
            "discrimination": self.discrimination,
            "calibration": self.calibration,
            "margin": self.margin,
            "incremental_utility": self.incremental_utility,
            "verdict": self.verdict,
            "note": ("infrastructure readiness only; no Laya performance is "
                     "asserted by this result"),
        }


class OfflineRouteEvaluator:
    """Validate and measure a route_preference corpus without external inference."""

    def __init__(self, *, checkpoint: str = DEFAULT_CHECKPOINT,
                 revision: str = DEFAULT_REVISION,
                 inference_ceiling: int = DEFAULT_INFERENCE_CEILING,
                 spec: QualificationSpec | None = None) -> None:
        if not 1 <= inference_ceiling <= 512:
            raise ValueError("inference ceiling must be between 1 and 512")
        self.checkpoint = checkpoint
        self.revision = revision
        self.inference_ceiling = inference_ceiling
        self.spec = spec or QualificationSpec()

    def validate(self, records: Sequence[CorpusRecord], *,
                 model_input_fields: Iterable[str] = ()
                 ) -> dict[str, Any]:
        """Run every leakage check and split by trajectory.

        Returns INVALID with diagnostics rather than proceeding on suspect data.
        """
        train, holdout = split_by_trajectory(records)
        leakage = run_all_checks(records, train=train, holdout=holdout,
                                 model_input_fields=model_input_fields)
        return {
            "valid": leakage.valid,
            "problems": list(leakage.problems),
            "split": {"train": len(train), "holdout": len(holdout),
                      "sample_unit": SAMPLE_UNIT},
        }

    def run(self, records: Sequence[CorpusRecord], *, provider: Any,
            task_contexts: Mapping[str, Any],
            model_path: str | Path,
            statework_contract_version: str,
            model_input_fields: Iterable[str] = ()
            ) -> tuple[OfflineEvalResult, Any]:
        """Run local inference over a corpus and return measured metrics.

        Requires an explicit provider: the caller owns checkpoint loading, and no
        external backend is ever contacted. Refuses to run on an invalid corpus
        rather than producing metrics from suspect data.
        """
        from .inference_bridge import (
            build_frame,
            compute_metrics,
            prediction_from_judgment,
        )

        validation = self.validate(records,
                                   model_input_fields=model_input_fields)
        if not validation["valid"]:
            raise ValueError(
                "refusing to evaluate an invalid corpus: "
                + "; ".join(validation["problems"]))

        capability = provider.capability()
        missing_contexts = sorted(
            {r.frame_id for r in records} - set(task_contexts))
        if missing_contexts:
            raise ValueError(
                f"corpus records lack a TaskContext: {missing_contexts}")

        if len(records) > self.inference_ceiling:
            raise ValueError(
                f"corpus of {len(records)} exceeds the inference ceiling of "
                f"{self.inference_ceiling}")

        predictions = []
        for sequence, record in enumerate(records, start=1):
            frame = build_frame(
                record, task_contexts[record.frame_id], sequence=sequence,
                backend_id=capability.backend_id, model_id=capability.model_id,
                model_revision=capability.model_revision,
                model_content_hash=capability.model_content_hash,
                provider_protocol_hash=capability.provider_protocol_hash)
            judgment = provider.judge(frame)
            predictions.append(prediction_from_judgment(
                record, judgment,
                checkpoint_revision=capability.model_revision,
                checkpoint_content_hash=capability.model_content_hash))

        from .route_fixtures import build_fixture_set
        trajectories = len({r.trajectory_id for r in records})
        metrics = compute_metrics(
            predictions, spec=self.spec, trajectories=trajectories)
        measured = metrics.to_dict()

        return OfflineEvalResult(
            checkpoint=self.checkpoint,
            checkpoint_revision=self.revision,
            checkpoint_content_hash=checkpoint_digest(model_path),
            statework_contract_version=statework_contract_version,
            inference_count=len(predictions),
            inference_ceiling=self.inference_ceiling,
            split=validation["split"],
            leakage={"valid": True, "problems": []},
            fixture_coverage=coverage_report(build_fixture_set()),
            sample_size=self.spec.evaluate_sample_size(
                outcomes=len(records), trajectories=trajectories,
                cohort_trajectories=trajectories),
            discrimination={"balanced_accuracy": measured["balanced_accuracy"],
                            "controller_accuracy": measured["controller_accuracy"],
                            "confusion": measured["confusion"]},
            calibration={"brier": measured["brier"], "ece": measured["ece"]},
            margin={"mean_margin": measured["mean_margin"]},
            incremental_utility={
                "false_intervention_rate": measured["false_intervention_rate"],
                "complementarity": measured["complementarity"]},
            verdict=measured["verdict"],
        ), metrics

    def prepare(self, records: Sequence[CorpusRecord], *,
                model_path: str | Path,
                statework_contract_version: str,
                model_input_fields: Iterable[str] = ()
                ) -> OfflineEvalResult:
        """Bind a corpus to its artifacts and report readiness.

        Performs NO inference. `run()` is required for that, and only with an
        explicit local provider.
        """
        validation = self.validate(records,
                                   model_input_fields=model_input_fields)
        from .route_fixtures import build_fixture_set
        coverage = coverage_report(build_fixture_set())
        trajectories = len({r.trajectory_id for r in records})
        sample_size = self.spec.evaluate_sample_size(
            outcomes=len(records), trajectories=trajectories,
            cohort_trajectories=trajectories)

        return OfflineEvalResult(
            checkpoint=self.checkpoint,
            checkpoint_revision=self.revision,
            checkpoint_content_hash=checkpoint_digest(model_path),
            statework_contract_version=statework_contract_version,
            inference_count=0,
            inference_ceiling=self.inference_ceiling,
            split=validation["split"],
            leakage={"valid": validation["valid"],
                     "problems": validation["problems"]},
            fixture_coverage=coverage,
            sample_size=sample_size,
            verdict=("INVALID_CORPUS" if not validation["valid"]
                     else "INCONCLUSIVE_UNDERPOWERED"
                     if sample_size["verdict"] != "QUALIFIED_FOR_EVALUATION"
                     else "READY_FOR_OFFLINE_INFERENCE"),
        )


__all__ = ["DEFAULT_CHECKPOINT", "DEFAULT_INFERENCE_CEILING", "DEFAULT_REVISION",
           "OfflineEvalResult", "OfflineRouteEvaluator", "checkpoint_digest",
           "split_by_trajectory"]
