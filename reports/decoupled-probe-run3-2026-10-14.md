# Decoupled probe, improved set: the model never answers `infrae`

**Date:** 2026-10-14
**Probe:** `qualification/decoupled_probe.py`, 74 inferences (7.3s each, cached)
**Status:** complete. Decidable share fixed; **a sharper defect found**
**Advisory mode:** closed. Nothing trained. No weights changed.

---

## What changed since the previous probe

The earlier fixture set was 72% contract-ambiguous — only 48 cases were scorable,
too thin to measure anything. Rebuilt around **trigger-confusable families**: each
capability is now a trigger that more than one StateWork services, taken directly
from the CSW contracts (e.g. `mutating_change` → getter `[authority, rollback]` vs
infrae `[change_gate, authority]`). A family lists only the bundles that actually
offer that trigger.

| Metric | Previous | Now |
|---|---|---|
| total cases | 171 | 74 |
| **decidable** | 0.28 | **0.77** |
| scorable in probe | 48 | **57** |
| name-decoupled | 0.281 | **0.75** |

## Result

```
  COUPLED    accuracy : 16/27 = 0.593
  DECOUPLED  accuracy : 11/30 = 0.367
  predictions          : getter 70%, gitter 23%, infrae 7%
```

## The degeneracy guard fires again — for a different reason

70% of predictions are `getter`, so the raw 0.226 gap is still not valid evidence
about name- vs obligation-following. The constant-predictor baseline proves it:
`always-getter` scores 0.630 coupled / 0.333 decoupled — a **+0.296** gap, larger
than the model's own. So the gap is explained by route frequency.

But this time the underlying data supports a statement the gap cannot. The
confusion matrix:

```
                 getter  gitter  infrae
  gold=getter        20       5       2
  gold=gitter         0       5       0
  gold=infrae        20       3       2     <-- never predicted when correct
```

Per-rotation hit rate:

```
  rotation 0 (canonical)  : 16/27 = 0.593
  rotation 1 (decoupled)  : 10/27 = 0.370
  rotation 2 (decoupled)  :  1/3  = 0.333
```

Per-class recall:

```
  gold=getter  : 20/27 = 0.741
  gold=gitter  :  5/5  = 1.000
  gold=infrae  :  2/25 = 0.080   <-- the finding
```

**The model essentially never answers `infrae`.** 4 predictions out of 74, against
25 cases where `infrae` is the contract-correct answer. It answers `getter` for
`infrae`-gold cases **20 times**.

A constant-`getter` baseline reproduces the accuracy numbers, but it cannot account
for a model that answers `gitter` correctly 5/5 while answering `infrae` correctly
2/25 — the failure is selective, not uniform. That asymmetry is what makes the
class-collapse claim structural rather than an artefact of the degeneracy.

The rotation-0 → rotated drop (0.593 → 0.370/0.333) is consistent with the model
having learned the **canonical** association, but it is *consistent only*: the same
drop is what a collapsed predictor produces on a rotated set.

## Honest status of the earlier name-matching claim

Still **not established**. The rotation drop is real and consistent with
name-association, but with 70% of predictions on `getter`, no coupled/decoupled
number on this checkpoint is admissible as evidence. State that plainly rather than
overstate it.

What *is* established, and is new:

1. **The fixture set is now adequate** — 0.77 decidable, 0.75 decoupled.
2. **The model is class-collapsed** — `infrae` recall 0.080 against 25 gold cases.
   This alone makes the checkpoint unusable regardless of how the name-vs-obligation
   question resolves.
3. **`gitter` recall is perfect (5/5) but on only 5 cases** — recorded so it is not
   mistaken for a capability.

## Consequence

A route that is correct 25 times is answered 2 times. That is a training problem
(class collapse), not an interpretation problem.

Note the determinism relevance: if a model with this output distribution were
authorised to reorder StateWork, it would systematically starve `infrae`
(infrastructure work) while over-selecting `getter` (repository work). At the
current 0.75 ceiling versus the 0.82 deterministic floor, the shadow gate blocks it
— the correct outcome, and the reason the advisory boundary is doing real work.

## Verification

| Check | Result |
|---|---|
| probe completed | `EXIT=0`, 74 inferences, 7.3s each |
| judgment cache | 74 records at `/tmp/dp-audit/laya-judgments.jsonl` |
| `qualification/` tests | **102 passed** |
| degenerate-predictor guard | fired correctly (70% `getter`) |
| constant-predictor baseline | reported alongside the raw gap |
| production code changed | **none** (0 modified tracked files) |
| baseline checkpoint | untouched |
