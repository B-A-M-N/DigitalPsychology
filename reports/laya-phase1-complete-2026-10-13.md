# Laya Representation: Phase 1 Complete, With an Honest Caveat

**Date:** 2026-10-13
**Status:** feature parity achieved and measured; **adversarial audit weakened one claim**
**Advisory mode:** disabled. No authority boundary changed. No production code changed.

---

## What was built

### 1. Feature parity (`qualification/inference_bridge.py`)

`build_frame()` previously emitted `workflows=()` and option labels all reading
`"Already-authorized legal route."` — asking Laya to choose between `getter`,
`gitter`, and `infrae` while telling it nothing distinguishing them.

Now it emits real `WorkflowFact` values mirroring `lib/control/system_one.py`, and
option labels carrying purpose, domain, **and required-evidence obligations** read
verbatim from the authoritative CSW `transitions.yaml` contracts:

- `getter.observed.plan.planned` → `[intent, risk]`
- `gitter.unknown.observe.observed` → `[repository_truth]`
- `infrae.unknown.discover.observed` → `[discovery]`

`available_evidence` distinguishes held vs missing, so the model can see which
obligations are satisfiable. All of it is pre-decision fact.

### 2. Parity tests (`qualification/test_feature_parity.py`) — 9 passing

Workflow facts present, distinguishing vocabulary, distinguishable labels,
`SemanticState` shape, order-independence, held/missing evidence, evidence
distinguishing getter from gitter, bare-context backward compatibility, and
**anti-leakage** — rebuilding the frame under each gold label and asserting the
workflow facts are byte-identical.

## Measured A/B — same checkpoint, same weights

| | Bare | Informative |
|---|---|---|
| balanced_accuracy | 0.333 | **1.000** |
| false_interventions | 3 | **0** |
| laya_fixed_controller_errors | 1 | **4** |
| laya_broke_correct_ones | 3 | **0** |
| mean_margin | 0.041 | 0.030 |

**8/8 with evidence obligations exposed.** Δ +0.667 from representation alone.

## The adversarial audit — and why the 8/8 is weaker than it looks

I wrote `qualification/leakage_audit.py` rather than accept a perfect score. Four
checks: label leakage (clean), availability leakage (constant across golds), order
leakage (content-stable under permutation), obligation permutation.

**Two bugs in my own audit, both caught and fixed:**
1. `_ctx()` accepted an `obligations` parameter and **ignored it**, iterating a
   module-level constant — so check 4 was testing nothing and reported `False`
   (no change) for the wrong reason.
2. The order check compared position-dependent tuples, so a pure reordering read as
   a content change.

Both fixed. A leak audit that can't vary what it claims to test isn't a test.

**Then the decisive probe.** Keep the task fixed; swap which route carries which
obligation. A reasoning model should follow the obligation. A name-matching model
should not move.

```
original   selected=getter   dist={getter: 0.368, gitter: 0.326, infrae: 0.306}
permuted   selected=getter   dist={getter: 0.371, gitter: 0.325, infrae: 0.303}

The prediction did NOT move when obligations swapped.
```

**The prediction is essentially identical (Δ ≈ 0.003).** So on this case the model
is associating the route **name**, not the obligation semantics.

## What this means — stated plainly

The 8/8 is **real and it is not leak-derived** — the projection does not encode the
answer, and the improvement over bare input is genuine. But it is **fragile evidence**
for "Laya learned to distinguish StateWorks."

The honest reading: with informative input the checkpoint now gets these cases right,
but we cannot yet attribute that to reasoning about evidence obligations rather than
recognising route names — because in every current fixture the obligation and the name
co-vary.

**ECE was also 0.602 on the informative arm** while accuracy was 1.0 — the model is
badly overconfident. Perfect discrimination with poor calibration is exactly the
"calibration ≠ discrimination" case the spec already separates.

## What this implies for Phase 2

Contrastive fixtures must **decouple** obligation from name. Concretely:

- Same task, same eligibility, obligation swapped between two routes — with gold
  following the obligation, not the name.
- Cases where the obligation and the name disagree, so name-matching scores wrong.
- A held-out split containing decoupled cases specifically, so a name-matching model
  cannot pass.

Without that, training would teach the association we're trying to avoid, and eval
would keep reporting inflated numbers.

## Verification

| Check | Result |
|---|---|
| `qualification/test_feature_parity.py` | **9 passed**, lint clean |
| DP `tests/` + `qualification/` | **608 passed, 6 skipped** (was 602) |
| Production code changed | **none** |
| DP tracked files modified | **0** |

## Boundaries

No advisory activation. No labels fabricated from controller selections. No
DP/CSW/CFW authority change. The pinned baseline checkpoint is untouched.

## Recommendation

Do **not** start fine-tuning yet. A model trained on name/obligation-correlated
fixtures would learn the correlation, and the eval would certify it. Build the
decoupled contrastive set first — it is a fixture-authoring task, not a training
task — then re-run the three-configuration comparison on frozen holdout.
