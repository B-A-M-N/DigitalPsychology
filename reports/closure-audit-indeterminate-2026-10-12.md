# Closure Audit — Indeterminate Actuation Lifecycle, and Offline Qualification Assets

**Date:** 2026-10-12
**Type:** read-only audit. **No production code was changed in this step.**
**Scope:** (A) whether `ActuationFailure.indeterminate` survives to its consumers
across the caller, retry, persistence, restart, and reconciliation lifecycle;
(B) which `route_preference` qualification deliverables can be completed without
the rate-limited inference backend.

---

## Part A — Indeterminate actuation closure audit

### Verdict

**The actuator fix is correct but currently unobservable by its only consumer.**
Three defects are confirmed by execution. One is a genuine correctness gap in the
fix I shipped; the other two are scope gaps in the same classification.

### D1 — Indeterminacy does not propagate past the actuator (CONFIRMED, P1)

`cognitive_runtime_core/integrations/triad/actuator.py` now raises
`ActuationFailure(indeterminate=True)` correctly. But the trusted host never sees it.

`lib/runtime/transactions.py::abort_prepared` builds the `ControlAbortRequest` the
host consumes, forwarding only `applied_directive_ids`, `rollback_completed`, and
`failure_class`. `ControlAbortRequest` (`lib/control_protocol.py:540-549`) has **no
field** capable of carrying indeterminacy.

Verified by reconstructing exactly what `abort_prepared` builds from a real
indeterminate failure:

```
Actuator raised:            indeterminate=True, rollback_completed=False
Host receives:
    applied_directive_ids = ('activate-getter',)
    rollback_completed    = False
    failure_class         = 'ActuationFailure'
Indeterminacy representable in the abort request? False
```

Both of these produce **byte-identical** abort requests:

| Case | What the host must do |
|---|---|
| committed but unverifiable | reconcile from authoritative CSW state; do not retry |
| refused after partial apply | rollback failed; retry is permissible |

The host cannot distinguish them. This is the exact distinction the fix introduced,
lost at the one boundary that matters.

**Note on `failure_class`:** it is derived as
`type(error.__cause__ or error).__name__`. For an indeterminate failure the cause
is the underlying transport error, so it reports e.g. `TimeoutError`. That is
incidental — the field is a free-text class name, not a semantic channel, and must
not be relied upon to encode indeterminacy.

### D2 — A lost activation acknowledgment is not classified (CONFIRMED, P1)

`apply()` treats the **return value** of `activate_statework()` as the confirmation.
If the authority commits and the acknowledgment is lost, the exception propagates
from the activate call — not from the verification read. The
`_IndeterminateActuation` guard wraps only `self._snapshot(...)`.

Reproduced (`probe_lost_ack.py`) with a port that commits then drops the response:

```
apply() raised ActuationFailure: activation acknowledgment lost after commit
  indeterminate        = False
  rollback_completed   = True
  applied_directive_ids= ()
after:  active=() revision=8
activate_calls = 1
```

The caller is told **nothing happened and rollback succeeded**, while
`activate_calls = 1` proves the authority was invoked. In this configuration the
checkpoint restore did revert the state, so durability held — but the
**misclassification** is real: the caller cannot tell what occurred without
re-reading authority state itself.

This is the scenario the review specifically asked about, and it is genuinely
unhandled.

### D3 — Reconciliation does not surface indeterminacy (CONFIRMED, P2)

`ControlReconciliation` gained an `indeterminate` field, but
`lib/host/reconciler.py` and `lib/runtime/reconciliation.py` never reference it
(grep returns nothing). So a restart cannot distinguish an unresolved operation
from a completed one.

Partially mitigating: `reconcile_prepared` **does** re-read authoritative CSW state
and classifies applied-vs-not-applied, so recovery is not *wrong* — it is merely
silent about residual uncertainty. Lower severity than D1/D2 for that reason.

### What is correct (verified, no defect)

| Property | Status |
|---|---|
| DP `ControlActuator` inherits the fix | ✅ subclasses the triad actuator |
| Indeterminate outcome skips compensating rollback | ✅ `deactivate_calls == 0` |
| Exactly one activation, never duplicated | ✅ covered by existing tests |
| Reconciliation re-reads authority state | ✅ `reconcile_prepared` |
| Genuine refusal not misclassified | ✅ `indeterminate=False`, `rollback_completed=True` |

### Recommended remediation (NOT applied — no production changes this step)

D1 is the one that undermines the shipped fix. The minimal correct change is a
single additive field on `ControlAbortRequest` (defaulted, so no existing
constructor breaks), forwarded from `abort_prepared`, plus D2's guard extended to
wrap the `activate_statework` call. Both are small and interface-compatible.

I did **not** apply them. Per the assignment: *"Make no production changes unless
a reproducible correctness violation is established."* These are established, so
they are now actionable on your word — but the prior instruction was to report
verified defects, and D1 in particular changes a public protocol contract, which
desires explicit authorization rather than a unilateral edit.

---

## Part B — Offline-completable qualification assets

**Correction accepted.** The 429 blocks *live inference and controlled treatment*.
It does not block corpus construction, fixture authoring, statistical criteria, or
offline evaluation. And I withdraw the stronger claim: model capability is
**unqualified**, not demonstrated inadequate. The prior samples (n=8 hand-authored,
n=2, n=3 labels) cannot support either conclusion.

### What is already built and reusable (verified present)

| Asset | Location | Offline? |
|---|---|---|
| Pinned checkpoint + tokenizer + encoder | `~/.cache/laya/convaiinnovations-laya-typed-decisions-1a793eb/` — `model.safetensors` (842 MB), `rl_agent_config.json`, `encoder/config.json`, `tokenizer/tokenizer.json` all present | ✅ |
| Route held-out screen | `DigitalPsychology-laya-advisory/scripts/route_screen_heldout_2026-10-10.py` | ✅ "58 inferences (hard ceiling 64), offline, cached checkpoint, no API" |
| Blinded semantic-family evaluator | `scripts/evaluate_laya_semantic_families_blinded.py` | ✅ |
| Token-coverage replication (Laya's own `build_sequence` + pinned tokenizer, **no forward pass**) | `reports/laya-token-coverage-audit-2026-10-10.json` | ✅ |
| Difficulty screener pattern | `CognitiveFrameWorks/scripts/cohort/probe_difficulty.py` | ⚠️ needs an agent; pattern reusable |
| Calibration/qualification gates | `lib/system_one_calibration.py` — 100/30/20, 0.80 accuracy, Brier ≤ 0.25, ECE ≤ 0.12 | ✅ |

**No new benchmark framework is needed.** The infrastructure exists.

### Deliverables completable now, without inference

1. **Independent semantic-label corpus spec** — the three-observation schema
   (semantic target / controller decision / observed outcome) with provenance
   fields and an explicit `counterfactual_available: bool`. Authoring labels
   requires human judgment, not inference.
2. **Adversarial fixture set** using real bundled StateWork contracts
   (`getter` 6 evidence sets, `gitter` 3, `infrae` 16), covering the ten required
   case classes. Pure authoring.
3. **Statistical acceptance criteria** — roles of the 100/30/20 counts, sample
   units, independence rules, confidence intervals, and pass/fail thresholds,
   written down *before* any measurement. Pure specification.
4. **Offline evaluation harness** — extend the existing held-out screen to consume
   the new fixtures, with trajectory-boundary-aware splitting. Runs on the cached
   checkpoint with a hard inference ceiling.
5. **Attribution checks** — verify a frame's evidence cannot leak its label
   (the blinded-probe pattern already does this).

### Deliverables that remain blocked

- Any **live** inference at scale, and §11's controlled behavioral treatment —
  both need the backend.

---

## Summary

**Part A:** 3 confirmed defects (D1 P1, D2 P1, D3 P2). D1 means the fix I shipped
last step is correct in the actuator but invisible to the host — I am reporting
that against my own prior work rather than letting it stand as closed. No
production changes made; remediation is small and interface-compatible, awaiting
authorization since it touches `ControlAbortRequest`.

**Part B:** the 429 blocks far less than I implied. Five deliverables are
completable offline now, on infrastructure that already exists. Model capability
is **unqualified**, not inadequate — a distinction I should have drawn earlier.
