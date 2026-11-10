# Offline route_preference Qualification — Deliverables A–E

**Date:** 2026-10-12
**Status:** specification + harness complete. **No model inference has been run.**
**Checkpoint:** `convaiinnovations/laya-typed-decisions` @ `1a793eb`, verified present
(model.safetensors 842 MB + tokenizer + encoder) and usable offline.

> **This document defines the measurement. It contains NO Laya performance result.**
> Benchmark infrastructure being complete is not evidence that Laya's semantic
> predictions are accurate or behaviorally useful.

---

## Deliverable A — Three-observation corpus schema

File: `qualification/corpus_schema.py`

Each record keeps the three observations **structurally separate**, with explicit
provenance and an explicit unknown-counterfactual state:

```python
SemanticTarget        # independently labelled; NEVER derived from the controller
ControllerDecision    # what the deterministic controller chose, and why
ObservedOutcome       # host-authoritative what-happened
counterfactual_available: bool   # False => the alternative's outcome is UNKNOWN
```

Validation rejects the two circularity failures the directive calls out:

- **Label leakage** — a semantic target whose `label_provenance` names the
  controller decision is rejected: *"semantic target must not be derived from the
  controller decision."*
- **Fabricated counterfactuals** — a record asserting
  `counterfactual_available=True` with no `counterfactual_route` or no
  `counterfactual_outcome` is rejected. Unknown stays unknown.

Required identity fields bind a record to its artifact combination, so a
qualification cannot be inherited across checkpoints or contract versions:
`checkpoint_revision`, `checkpoint_content_hash`, `statework_contract_version`,
`calibration_id`, `policy_snapshot_hash`, `trajectory_id`, `subject_ref`.

## Deliverable B — Adversarial route fixtures

File: `qualification/route_fixtures.py`

Fixtures are built from the **real bundled contracts**, so their labels are checked
against governing evidence requirements rather than authored by assertion:

| Bundle | distinct `required_evidence` sets |
|---|---|
| `getter` | 6 — e.g. `[intent, risk]` to advance PLANNED→CHANGING |
| `gitter` | 3 — e.g. `[repository_truth]` to reach terminal |
| `infrae` | 16 — e.g. `[plan, invariants, rollback]` |

Ten case classes, matching the directive exactly:

1. `correct_deterministic` — deterministic selection is right; intervention must not fire.
2. `incorrect_deterministic` — deterministic selection is wrong; the opportunity case.
3. `ambiguous_preference` — no defensible preference; abstention expected.
4. `contradictory_evidence` — evidence conflicts; abstention expected.
5. `missing_repository_truth` — repository state unknown.
6. `recovery_vs_continuation` — recovery route vs continuation route.
7. `evidence_gated_transition` — transition blocked until evidence exists.
8. `ineligible_candidate` — preferred route is not CSW-eligible.
9. `semantic_would_worsen` — the semantically attractive route makes the result worse.
   This is the false-intervention guard.
10. `verification_before_mutation` — verification must precede mutation.

**Class 9 and class 1 are the important ones.** A model that "helps" by overriding
correct deterministic selections, or by preferring an attractive-but-worse route,
scores as harmful. Without these, a benchmark rewards overconfidence.

## Deliverable C — Statistical qualification specification

File: `qualification/qualification_spec.py`

Criteria are defined **before** any measurement. Authoritative definitions were
read from source, not assumed:

| Quantity | Authoritative definition | Source |
|---|---|---|
| `100/30/20` | `minimum_outcomes=100`, `PRODUCTION_MINIMUM_INDEPENDENT_TRAJECTORIES=30`, `PRODUCTION_MINIMUM_COHORT_TRAJECTORIES=20` | `lib/system_one_calibration.py` |
| `≥0.80` accuracy | `qualification_minimum_accuracy`, re-checked against `holdout_accuracy` | same |
| `≥0.15` margin | **AGGREGATE** — mean top1−top2 over the benchmark (`mean_confidence_margin`, floor `margin_floor`). NOT per-decision. | `laya-route-screen-stage-b-2026-10-10.json` |

**The `0.15` finding is load-bearing.** The Stage-B run's own per-case margins were
`0.0298–0.2090` (mean `0.1274`). A per-decision floor of `0.15` would have rejected
every case that produced it. Enforcing `0.15` per-decision would silently make the
only legitimate advisory surface unreachable. Per-decision ambiguity is a separate,
much smaller floor (`DEFAULT_ROUTE_PREFERENCE_MARGIN = 0.05`).

Specified separately, because they are independent:

- **Discrimination** — balanced accuracy on held-out cases.
- **Calibration** — Brier ≤ 0.25, ECE ≤ 0.12.
- **Per-decision preference margin** — top1−top2, floor 0.05.
- **Aggregate qualification margin** — mean margin, floor 0.15.
- **Incremental utility** — improvement over the deterministic baseline.
- **Complementarity on controller failures** — measured on the
  `incorrect_deterministic` subset specifically.
- **False-intervention risk** — error rate on `correct_deterministic` and
  `semantic_would_worsen`.

Sample-unit independence: the unit is the **trajectory**, not the frame. Repeated
frames from one task are correlated and must not be split across train/holdout.

**Underpowered results must report `INCONCLUSIVE`, never a manufactured pass/fail.**

## Deliverable D — Offline evaluation harness

File: `qualification/offline_eval.py`

- Reuses the **pinned** checkpoint, tokenizer, and encoder. No substitution.
- Calls the **local** inference path only. Never the external backend.
- Enforces the existing inference ceiling and reports it.
- Trajectory-level split integrity: all frames of a trajectory land in one split.
- Binds every result to checkpoint revision + content hash + contract version, so a
  result cannot be attributed to the wrong artifact.

The harness is written against DP's real `SystemOneFrame` contract, so its output
is directly consumable by `CalibrationRecord`.

## Deliverable E — Leakage and attribution checks

File: `qualification/leakage_checks.py`

Each check fails validation with an actionable diagnostic:

| Check | Detects |
|---|---|
| `check_label_leakage` | semantic labels copied from controller decisions |
| `check_trajectory_overlap` | same trajectory in train and holdout |
| `check_duplicates` | duplicate / near-duplicate fixtures |
| `check_outcome_leakage` | outcome fields leaked into model inputs |
| `check_provenance` | missing label provenance |
| `check_independence` | correlated observations counted as independent |
| `check_counterfactual_attribution` | counterfactual credited to the wrong route |

---

## What is deliberately NOT claimed

- **No Laya discrimination number.** No inference has been run in this pass.
- **No calibration result.** Brier/ECE require predictions that do not yet exist.
- **No incremental utility.** Requires the benchmark to run.
- **No advisory activation.** Advisory remains disabled.

The prior evidence remains what it was: an n=8 hand-authored screen whose own
artifact says *"a go/no-go for this checkpoint on route_preference, not
qualification."* Model capability is **unqualified** — not demonstrated inadequate,
and not demonstrated adequate.

---

## Activation decision (unchanged, and deliberately so)

| Status | Value |
|---|---|
| Shadow safe | **Yes** |
| Advisory technically reachable | **Yes** (`route_preference` StateWork reordering) |
| Advisory correctly guarded | **Yes** — provenance, margin, fail-closed validation, indeterminate classification |
| Advisory statistically qualified | **No** — no corpus, no predictions |
| Behavioral improvement demonstrated | **No** |
| Production activation authorized | **No** |
