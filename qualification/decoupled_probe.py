"""Decisive test: does the model follow OBLIGATION semantics, or route NAME?

Phase 1's permutation probe showed the model did not move when obligations
swapped. That probe used a single hand-built case. This runs the same question
across the generated contrastive set, where gold is contract-derived and 28% of
permuted cases have a DIFFERENT gold from the identical canonical case.

If the model scores well on canonical-only cases and degrades sharply on
decoupled cases, it is name-matching. If it holds up, it is reasoning.

Read-only with respect to everything except the model's own predictions.
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from collections.abc import Sequence
from typing import Any

sys.path.insert(0, "/home/bamn/DigitalPsychology")

from lib.laya_configuration import MODEL_CONTENT_SHA256, MODEL_REVISION
from lib.system_one_backends import PROVIDER_PROTOCOL_HASH
from qualification.contrastive_fixtures import ContrastiveCase, generate_cases
from qualification.corpus_schema import (
    ControllerDecision,
    CorpusRecord,
    ObservedOutcome,
    SemanticTarget,
)
from qualification.inference_bridge import (
    TaskContext,
    build_frame,
    prediction_from_judgment,
)

CKPT = "/home/bamn/.cache/laya/convaiinnovations-laya-typed-decisions-1a793eb"
CACHE_PATH = "/tmp/dp-audit/laya-judgments.jsonl"


def _PLACEHOLDER_CASE() -> ContrastiveCase:
    """A FIXED case used to fingerprint the frame shape.

    The fingerprint must describe the SHAPE of what the model is asked, not any
    particular case. It is therefore built from constants rather than from
    `generate_cases()`: deriving it from the corpus means the digest changes
    whenever the corpus grows, which silently invalidates every cache entry and
    disables caching altogether -- a failure that looks like a working cache
    because the probe just re-runs everything.
    """
    return ContrastiveCase(
        case_id="fingerprint-probe",
        capability="planning_change",
        objective="Fingerprint the shape of the frame this probe builds.",
        eligible=("getter", "infrae"),
        held_evidence=("intent", "risk"),
        obligations={"getter": ("intent", "risk"),
                     "infrae": ("plan", "invariants", "rollback")},
        gold="getter",
        ambiguity_reason=None,
        permutation=0,
        task_instance="")


def _cache_fingerprint() -> dict[str, Any]:
    """Everything that makes a cached prediction valid for THIS evaluation.

    A prediction is only reusable if the model that produced it, the frame it was
    asked about, and the inference configuration are all identical. Keying on
    `case_id` alone silently hands a NEW checkpoint the OLD checkpoint's answers,
    which would make the whole comparison meaningless -- and the next experiment
    is specifically a comparison between different weights.
    """
    frame_shape = _rec(_PLACEHOLDER_CASE())
    frame = build_frame(
        frame_shape, _ctx(_PLACEHOLDER_CASE()), sequence=1,
        backend_id="digitalpsychology-laya-native",
        model_id="laya-typed-decisions", model_revision=MODEL_REVISION,
        model_content_hash=MODEL_CONTENT_SHA256,
        provider_protocol_hash=PROVIDER_PROTOCOL_HASH)
    return {
        "cache_version": 2,
        "checkpoint_revision": MODEL_REVISION,
        "checkpoint_content_hash": MODEL_CONTENT_SHA256,
        "provider_protocol_hash": PROVIDER_PROTOCOL_HASH,
        # The full model-facing frame, minus the per-case sequence number.
        "frame_digest": _frame_digest(frame),
        "inference": {"device": "cpu", "max_latency_ms": 30000,
                      "offline": True},
    }


def _frame_digest(frame: Any) -> str:
    """Stable digest of the serialised frame the model actually reads."""
    payload = json.dumps(getattr(frame, "__dict__", frame), sort_keys=True,
                         default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _load_cache() -> dict[str, dict]:
    """Cache judgments by case_id, but ONLY entries matching the fingerprint.

    Stale entries (a different checkpoint, frame shape, or inference config) are
    dropped rather than reused: the caller then re-runs them and reports them as
    fresh inference rather than cache reuse.
    """
    fingerprint = _cache_fingerprint()
    try:
        out = {}
        reused = dropped = 0
        with open(CACHE_PATH, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                if rec.get("fingerprint") != fingerprint:
                    dropped += 1
                    continue
                out[rec["case_id"]] = rec
                reused += 1
        if dropped:
            print(f"  cache: dropped {dropped} entry/entries whose fingerprint "
                  "does not match this checkpoint/frame/inference config")
        return out
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _append_cache(rec: dict) -> None:
    rec = dict(rec)
    rec["fingerprint"] = _cache_fingerprint()
    with open(CACHE_PATH, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _ctx(case):
    return TaskContext(
        objective=case.objective,
        work_mode="delivery" if "change" in case.capability else "diagnose",
        deliverable_kind="code_behavior",
        current_state="UNKNOWN",
        domain_tags=("software",) if "repository" in case.capability
        else ("infrastructure",),
        workflows=tuple(
            (sid, f"Handle {sid} work.", "repository" if sid != "infrae"
             else "infrastructure", "UNKNOWN", (), sid in case.eligible,
             case.obligations.get(sid, ()))
            for sid in case.eligible),
        available_evidence=case.held_evidence,
        non_goals=())


def _rec(case):
    return CorpusRecord(
        frame_id=case.case_id,
        semantic_target=SemanticTarget(
            frame_id=case.case_id, appropriate_route=case.gold,
            eligible_routes=case.eligible,
            label_provenance="contract_derived",
            labelled_by="contrastive_generator",
            abstention_expected=case.gold is None),
        controller_decision=ControllerDecision(
            selected_route=None, available_candidates=case.eligible,
            policy_snapshot_hash="1" * 64, decision_reason="none",
            abstained=True),
        observed_outcome=ObservedOutcome(
            success_criteria_met=True, evidence_refs=("synthetic",),
            observed_by="generator"),
        checkpoint_revision=MODEL_REVISION,
        checkpoint_content_hash=MODEL_CONTENT_SHA256,
        statework_contract_version="csw-v1", calibration_id="none",
        policy_snapshot_hash="1" * 64, trajectory_id=case.case_id,
        subject_ref="synthetic")


def const_accuracy(route: str, rows, kind: str) -> float:
    """Accuracy a predictor that ALWAYS answers `route` would get on `rows`.

    rows entries are (case_id, kind, gold, selected, hit). Filtering by `kind`
    ("coupled" or "decoupled") shows that any gap from a degenerate predictor is
    already explained by route frequency and carries no signal about
    name- vs obligation-following.
    """
    hits = total = 0
    for _case_id, row_kind, gold, _sel, _hit in rows:
        if row_kind != kind or gold is None:
            continue
        total += 1
        if gold == route:
            hits += 1
    return hits / total if total else 0.0


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    from lib.system_one_backends import LayaProvider

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None,
                        help="only probe the first N cases (a scoped run)")
    parser.add_argument("--shard", type=int, default=1,
                        help="run shard S of N across parallel processes")
    parser.add_argument("--out", default="/tmp/dp-audit/decoupled-report.txt",
                        help="where to write the report (in addition to stdout)")
    args, _unknown = parser.parse_known_args(argv)

    print("=" * 74)
    print("Loading pinned checkpoint (offline)")
    print("=" * 74)
    provider = LayaProvider(
        CKPT, model_id="laya-typed-decisions", model_revision=MODEL_REVISION,
        expected_model_content_hash=MODEL_CONTENT_SHA256, device="cpu",
        max_latency_ms=30000)
    t0 = time.monotonic()
    provider.warmup_and_wait()
    print(f"  loaded in {time.monotonic() - t0:.1f}s")

    cases = generate_cases()
    if args.shard > 1:
        # Deterministic stratified shard: take every Nth case so each shard sees
        # the same mix of capabilities, rotations and golds as the whole corpus.
        # A plain prefix (--limit) would take only rotation-0 cases, leaving the
        # DECOUPLED column empty and the comparison meaningless.
        cases = [c for i, c in enumerate(cases) if i % args.shard == 0]
    if args.limit is not None:
        # Sample across the whole corpus rather than truncating, so a scoped run
        # still contains decoupled cases.
        stride = max(1, len(cases) // args.limit)
        cases = cases[::stride][:args.limit]
    cache = _load_cache()
    # Canonical golds keyed by (capability, held) so we can label each permuted
    # case as coupled or decoupled.
    canon_by_key = {(c.capability, c.held_evidence): c.gold
                    for c in cases if c.permutation == 0}

    coupled_hits = coupled_total = 0
    decoupled_hits = decoupled_total = 0
    ambiguous_total = 0
    rows = []
    t0 = time.monotonic()
    for index, case in enumerate(cases, start=1):
        cached = cache.get(case.case_id)
        if cached is not None:
            selected = cached["selected"]
        else:
            frame = build_frame(
                _rec(case), _ctx(case), sequence=index,
                backend_id="digitalpsychology-laya-native",
                model_id="laya-typed-decisions", model_revision=MODEL_REVISION,
                model_content_hash=MODEL_CONTENT_SHA256,
                provider_protocol_hash=PROVIDER_PROTOCOL_HASH)
            judgment = provider.judge(frame)
            artifact = prediction_from_judgment(
                _rec(case), judgment, checkpoint_revision=MODEL_REVISION,
                checkpoint_content_hash=MODEL_CONTENT_SHA256)
            selected = artifact.selected
            _append_cache({
                "case_id": case.case_id,
                "selected": selected,
                "gold": case.gold,
                "obligations": {k: list(v) for k, v in case.obligations.items()},
            })

        if case.gold is None:
            ambiguous_total += 1
            continue

        # Kind is derived from the CANONICAL assignment, not from the cache, so
        # the coupled/decoupled split stays correct whether or not we ran.
        key = (case.capability, case.held_evidence)
        expected_canonical = canon_by_key.get(key)
        kind = ("decoupled" if expected_canonical is not None
                and expected_canonical != case.gold else "coupled")
        hit = selected == case.gold
        if kind == "decoupled":
            decoupled_total += 1
            decoupled_hits += bool(hit)
        else:
            coupled_total += 1
            coupled_hits += bool(hit)
        rows.append((case.case_id, kind, case.gold, selected, hit))

    elapsed = time.monotonic() - t0
    print(f"  {len(cases)} inferences in {elapsed:.0f}s "
          f"({elapsed / max(1, len(cases)):.1f}s each)")

    print()
    print("=" * 74)
    print("RESULT — obligation-following vs name-following?")
    print("=" * 74)

    from collections import Counter
    pred_counts = Counter(sel for _, _, _, sel, _ in rows)
    print(f"  scorable cases      : {coupled_total + decoupled_total}")
    print(f"  ambiguous (no gold) : {ambiguous_total}")
    print(f"  cache reuse         : {len(cache)} cached, "
          f"{len(cases) - len(cache)} fresh")
    print()
    cou = coupled_hits / max(1, coupled_total)
    dec = decoupled_hits / max(1, decoupled_total)
    print(f"  COUPLED    accuracy : {coupled_hits:>3}/{coupled_total:<3} = {cou:.3f}"
          "  (gold matches the canonical route name)")
    print(f"  DECOUPLED  accuracy : {decoupled_hits:>3}/{decoupled_total:<3} = {dec:.3f}"
          "  (gold differs from the canonical route name)")
    print()
    print("  prediction distribution:")
    for route, count in pred_counts.most_common():
        share = count / max(1, sum(pred_counts.values()))
        print(f"    {route:<8} {count:>3}  ({share:.0%})")
    top_route, top_count = pred_counts.most_common(1)[0]
    top_share = top_count / max(1, sum(pred_counts.values()))
    degenerate = top_share >= 0.5
    print()
    print(f"  most-predicted route : {top_route} at {top_share:.0%}")
    print(f"  distinct predictions : {len(pred_counts)} of 3 eligible routes")
    print()
    print("=" * 74)
    gap = cou - dec
    print(f"  coupled-minus-decoupled gap: {gap:+.3f}")
    print()
    if degenerate:
        print(f"  !! DEGENERATE PREDICTOR: {top_share:.0%} of all predictions are the")
        print(f"     same route ({top_route}). The accuracy gap above is NOT valid")
        print("     evidence about name- vs obligation-following -- a near-constant")
        print("     predictor cannot show a meaningful coupled/decoupled split.")
        print()
        print("  A constant-predictor baseline scores:")
        const_cou = const_accuracy(top_route, rows, "coupled")
        const_dec = const_accuracy(top_route, rows, "decoupled")
        print(f"    always-{top_route} COUPLED   : {const_cou:.3f}")
        print(f"    always-{top_route} DECOUPLED : {const_dec:.3f}")
        print(f"    always-{top_route} gap       : {const_cou - const_dec:+.3f}")
        print()
        print("  => Fix the representation/training, not the interpretation.")
    elif gap > 0.15:
        print("  VERDICT: materially worse when the gold does NOT match the")
        print("  canonical route name -- association-matching on route identity.")
    elif gap < -0.15:
        print("  VERDICT: unexpectedly better on decoupled cases; investigate.")
    else:
        print("  VERDICT: no material gap -- responds to obligation, not name.")
    print(f"  n={len(cases)} synthetic cases; treat direction, not magnitude.")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
