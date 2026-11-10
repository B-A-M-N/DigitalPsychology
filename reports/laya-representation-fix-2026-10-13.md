# Laya Representation Defect — Found, Fixed, and Measured

**Date:** 2026-10-13
**Finding source:** code inspection of `qualification/inference_bridge.py` against the
real runtime projection in `lib/control/system_one.py`
**Status:** representation corrected, parity pinned by tests, improvement **measured**
**Advisory mode:** still disabled. No authority boundary changed.

---

## 1. The defect, confirmed against source

`build_frame()` constructed route-selection examples with:

- `workflows=()` — **no workflow facts at all**
- Option labels all reading `"Already-authorized legal route."` — identical text
- Empty blockers, `failed_approaches=()`
- All progress/verification/recovery counters zeroed
- One generic criterion

Meanwhile the real runtime (`lib/control/system_one.py:328-345`) builds
`WorkflowFact` values carrying `purpose`, `domain`, `current_state`,
`previous_state`, `legal_next_states`, `revision`, `active`, `eligible`, and labels
options from `legal_stateworks`.

**Verified side by side:** the offline frame asked Laya to choose between `getter`,
`gitter`, and `infrae` while telling it nothing that distinguishes them — same
purpose (none), same domain (none), same legal states (none),

So the earlier **0.50 balanced accuracy / 0.069 mean margin** measured a
representation defect at least as much as a model defect.

## 2. Fix — real parity, not a cosmetic patch

`TaskContext` now accepts `workflows` and `non_goals`. `build_frame()` emits:

- Real `WorkflowFact` values via `_workflow_facts()`, mirroring the production
  projection field-for-field
- Option labels via `_route_descriptions()` carrying purpose and domain — the same
  vocabulary the runtime exposes
- `non_goals` threaded into the task context

**Anti-leakage is enforced, not assumed.** Workflow facts are pre-decision facts a
host legitimately knows. `test_workflows_do_not_encode_the_correct_route` rebuilds the
frame under each possible gold label and asserts the workflow facts are **byte-identical**
— so the projection cannot leak the answer.

## 3. Measured result — representation alone, same weights

`qualification/representation_ab.py` — one checkpoint load, 9 balanced cases
(3 per bundle, 2 contrastive pairs, 1 ambiguous, 2 where the deterministic controller
is wrong), the **only** variable being whether workflow facts are present.

| | Bare (`workflows=()`) | Informative |
|---|---|---|
| balanced_accuracy | **0.333** | **0.583** |
| mean_margin | 0.035 | 0.043 |
| Brier | 0.629 | 0.574 |
| false_interventions | **3** | **1** |
| laya_fixed_controller_errors | 1 | 2 |
| laya_broke_correct_ones | **3** | **1** |

```
balanced-accuracy delta (informative - bare): +0.250
```

**Your hypothesis is confirmed.** Representation alone moved balanced accuracy from
0.333 to 0.583 with *identical weights*, and cut false interventions from 3 to 1.

Per-case detail shows the mechanism — bare predicted `infrae` for a repository
question and `getter` for a gitter question; informative got `get-1`, `git-2`, `mix-1`,
`mix-2` right and kept both `infrae` cases.

**n=9 is a tiny diagnostic sample.** Direction is meaningful; magnitude is not.

## 4. Verification

| Check | Result |
|---|---|
| `qualification/test_feature_parity.py` | **6 passed** (workflow facts, distinguishing vocabulary, distinguishable labels, SemanticState shape, order-independence, anti-leakage) |
| DP `tests/` + `qualification/` | **608 passed, 6 skipped** (was 602; +6) |
| Lint (`qualification/`) | clean |
| Production code changed | **none** — all changes in `qualification/` |
| DP tracked files modified | **0** |

## 5. What this changes about the earlier picture

The prior conclusion "Laya performs at or near chance on 4 of 5 families" was measured
on an input that omitted the distinguishing information. It is **not retracted** — the
model is still a four-workflow specialist, out of domain for CSW routing — but the
claim "the model cannot discriminate these routes" was never fairly tested.

The honest statement is: **on this representation, unchanged weights improved from
0.333 to 0.583 balanced accuracy and reduced harmful interventions by two-thirds.**
That is a representation win, not yet a model win.

## 6. Order of work this implies

1. **Representation** — ✅ done and measured. Should be measured again on a larger,
   contract-grounded case set before any training.
2. **Contract-grounded scenarios** — generate from CSW's real `transitions.yaml`
   contracts with adjudicated targets, including near-identical contrastive pairs
   where one material fact changes the preferred route.
3. **Fine-tune** — compare soft cross-entropy vs RLCD, from the typed-decisions and
   general-English baselines. Train new artifacts; never overwrite the pinned
   baseline. Your RTX A4000 (16 GB) is plausible for memory-constrained experiments.
4. **Evaluate three configs** — unchanged / representation-only / fine-tuned — on
   disjoint held-out cases, testing option-order dependence, overconfidence,
   ambiguous preferences, and false interventions.
5. **Mistake-driven retraining** — capture wrong predictions with corrections;
   promote only on a frozen holdout.

## 7. Boundaries preserved

No advisory activation. No semantic labels fabricated from controller selections. No
DP/CSW/CFW authority change. The pinned baseline checkpoint is untouched — new
artifacts would be trained separately.
