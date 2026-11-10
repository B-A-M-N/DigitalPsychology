# Correction: the training export did not match upstream Laya's API

**Date:** 2026-10-14
**Severity:** high — the exported rows would have been rejected outright by the real loader
**Status:** **fixed**, with regression tests
**Advisory mode:** closed. Nothing trained. No weights changed.

---

## What was wrong

`training_export.py` emitted questions as:

```json
{"t": "choice", "crit": {...}, "instruction": "..."}
```

Verified directly against the laya 0.4.7 wheel (`agent.py`), `Agent._check_question`
reads:

```python
t   = qdef.get("type")
ins = qdef["instructions"]
crit = qdef.get("criteria")
```

and `Agent._to_internal` then **produces**:

```python
q = {"t": t, "ins": ins, "crit": crit}
```

So `t`/`crit`/`ins` is the *output* of the transform, not its input. A row written in
that shape has none of the keys the public check reads, so `to_internal` raises and
`items_from_rows` records the row as skipped with reason `invalid_question`. Every one
of the 74 exported rows would have been dropped, and the schema they were written in
was described in my earlier report as "verified by reading the wheel."

That description was wrong, and the local pre-check could not catch it — the pre-check
validated the same wrong keys, so it and the exporter agreed with each other. Two
components sharing a wrong assumption is not verification.

## Second defect: train/serve input skew

The exporter rendered options as `"getter: requires intent, risk (missing: risk)."`
while production (`lib/control/statework_selector.eligible_statework_descriptions`)
renders `"Purpose: ... Domain: ... Behavior tags: ..."` and exposes **no** evidence
obligations at all.

Fine-tuning on one representation and serving with another teaches distinctions the
model cannot observe at run time.

## Third defect: judgment cache provenance

`decoupled_probe.py` cached predictions keyed on `case_id` alone. Nothing bound a cached
prediction to the checkpoint, frame shape, or inference config that produced it — so a
trained checkpoint would silently inherit the baseline checkpoint's answers, making the
exact comparison the next experiment exists to perform meaningless.

## Fixes

**1. Public schema.** The export now emits `{"type", "instructions", "criteria"}`, and
the pre-check validates **that** shape. The pre-check also now rejects a row written in
the internal shape explicitly (`test_internal_shape_is_rejected`), so the confusion
between the two forms fails a test rather than passing silently.

Two pre-check rules were also corrected while cross-reading `_check_question`:

| Rule | Old (wrong) | Now |
|---|---|---|
| `option_order` | rejected as invalid | **accepted** — it is legal public input, only popped internally |
| `instructions: 42` | rejected | **accepted** — `_to_internal` JSON-serialises numerics |
| `criteria: []` | rejected | **accepted** — a list becomes `{label: None}` |

Rejecting valid input is as much a defect as accepting invalid input: it would have
produced false rejections and sent me chasing a non-problem.

**2. One canonical input path.** `qualification/canonical_input.py` is now the single
source of the model-facing option text, used by the exporter and the probe. It emits
the **production form** (Purpose / Domain / Behavior tags) *plus* the required-evidence
obligation and its availability. Adding the obligation to the existing production form
is the point: the alternative — training on obligation-only text — would have introduced
the skew in the opposite direction.

`test_production_equivalence_is_superset` asserts the canonical text **starts with the
production form verbatim**, so production never shows the model less than training did.
`test_exporter_uses_the_canonical_path` fails if the exporter grows a private rendering.

Two further leaks were closed in the same pass:

- The state no longer contains the generator's `capability` field — a bookkeeping label
  production never provides, which the model could shortcut on
  (`test_state_does_not_leak_generator_bookkeeping`).
- Option labels are the StateWork ids (`getter`/`gitter`/`infrae`), not `A`/`B`/`C`,
  matching the production vocabulary.

**3. Cache fingerprinting.** Each cached judgment now carries a fingerprint of
`checkpoint_revision + checkpoint_content_hash + provider_protocol_hash + frame digest
+ inference config`. Entries whose fingerprint does not match the current evaluation are
**dropped**, not reused, and the drop is reported. A fresh checkpoint therefore forces
fresh inference, and the report distinguishes reuse from fresh execution.

## Verification

| Check | Result |
|---|---|
| `qualification/` tests | **140 passed** (was 123; +17) |
| pre-check on all splits | 74/74 rows clean, public schema |
| export question keys | `['criteria', 'instructions', 'type']` |
| export option labels | StateWork ids, matching gold keys |
| superset assertion | canonical text starts with production form |
| cache fingerprint | mismatched entries dropped, not reused |
| production code changed | **none** (0 modified tracked files) |
| baseline checkpoint | untouched |

## What this means for the plan

The blocker order changes. Fine-tuning is *not* the first thing to spend compute on
after all — the interface would have been wrong. With the schema corrected, `dry_run()`
becomes a formality rather than a discovery, and the canonical path means a trained
checkpoint is at least being asked the same question production asks.

The unresolved items are unchanged and still independent of code: laya >= 0.4.6 in an
isolated venv, GPU access, and a training corpus far larger than 74 synthetic cases
before any improvement claim.

## Boundaries

No training run. No weights modified. No install into `.venv-laya`. Advisory mode
closed. The pinned baseline remains the comparison baseline.
