# Decoupled probe result: near-constant predictor, Phase 1 finding NOT confirmed

**Date:** 2026-10-14
**Probe:** `qualification/decoupled_probe.py` (171 inferences, cached, rerunnable)
**Status:** complete; result is **inconclusive on the name-vs-obligation question**
**Advisory mode:** closed. Nothing trained. No weights changed.

---

## Why this probe existed

Phase 1's permutation probe found that swapping obligations between routes left
the prediction essentially unchanged (Δ ≈ 0.003), from which I concluded the model
was associating the route **name**, not obligation semantics. That probe used one
hand-built case.

This probe tests the same question across the 171-case contract-grounded set,
where 28% of permuted cases have a **different** gold from the identical canonical
case — so a name-matching model should fail at least 28% by construction.

## Result

```
  scorable cases      : 48          ambiguous (no gold) : 123
  COUPLED    accuracy :  6/16 = 0.375   (gold matches canonical route name)
  DECOUPLED  accuracy : 10/32 = 0.312   (gold differs from canonical route name)
  predictions          : getter 29 (60%), infrae 11 (23%), gitter 8 (17%)
  coupled-minus-decoupled gap: +0.062
```

## Why this does not confirm Phase 1

**The predictor is degenerate.** 60% of all predictions are `getter`. With a
near-constant predictor the coupled/decoupled gap carries no signal — and the
arithmetic proves it:

| Predictor | Coupled | Decoupled | Gap |
|---|---|---|---|
| actual model | 0.375 | 0.312 | +0.062 |
| **always-`getter`** | **0.312** | **0.344** | **−0.031** |

A constant predictor reproduces the gap, with the *opposite* sign, at comparable
magnitude. So the measured +0.062 is noise around a predictor that is mostly
answering one route regardless of input.

## Honest revision of the earlier claim

Phase 1 concluded the model follows the route name rather than the obligation.
That conclusion is **not confirmed** by the larger set. It is also not refuted —
it is **unmeasurable** while the predictor is near-constant. With 48 scorable
cases and a 60/23/17 prediction split, neither claim is supportable.

This is a downgrade of my own earlier verdict, stated explicitly: \u200bPhase 1's
name-matching finding was drawn from a single case and does not survive scaling.

## What is actually established

1. **The projection is not leaking** (four static checks in `leakage_audit.py`,
   all green) — the improvement from bare → informative input in Phase 1 was real.
2. **The checkpoint performs near chance on this set** — 0.375 / 0.312 against a
   balanced 3-way choice would be ~0.33.
3. **123 of 171 cases (72%) have no defensible preference** — the contract makes
   them ambiguous, so only 48 cases are scorable at all. That is the real
   limitation of the current fixture set: not enough decidable cases.

## Consequence for the program

The bottleneck is no longer labels or leakage. It is that **only 28% of generated
cases are both decidable and decoupled**, which is too thin a signal to measure a
model against, let alone train on.

Two concrete fixes, in order:

1. **Raise the decidable share.** Most ambiguity comes from obligations that are
   two-element conjunctions (`[authority, rollback]`, `[change_gate, authority]`,
   `[effective_state, regression_check]`), satisfiable only when the host holds
   *every* kind. Adding held-evidence patterns built from the *unions* of
   obligations, plus single-obligation-per-route variants, would produce far more
   uniquely-satisfiable cases.
2. **Then re-run this probe.** The cache means re-analysis costs no inference
   time once regenerated cases are added.

## Recommendation

Do **not** fine-tune yet, and do **not** report the 0.062 gap. Regenerate the
fixture set with a decidable share above ~50% and re-measure. Until the predictor
is spread across more than one route, no coupled/decoupled comparison on this
checkpoint means anything.

## Verification

| Check | Result |
|---|---|
| probe completed | `EXIT=0`, 171 inferences, 7.3s each |
| judgment cache written | 171 records at `/tmp/dp-audit/laya-judgments.jsonl` |
| provenance recorded per record | obligations + gold + selected |
| DP `qualification/` tests | **92 passing** (11 new export + 13 fixture + 9 parity + …) |
| full `tests/` + `qualification/` | **634 passed, 1 failed, 6 skipped** |
| the 1 failure | `test_control_mcp_host_bridge::…expires_local_leases…` — passes 41/41 in isolation and 2/2 solo; MCP timing flake under parallel load, **not** a regression |
| production code changed | **none** (0 modified tracked files) |
