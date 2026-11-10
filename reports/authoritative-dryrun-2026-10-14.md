# The authoritative check: upstream `dry_run` accepts the export

**Date:** 2026-10-14
**Subject:** step 1 of the improvement sequence — "run the official upstream dry-run and make that test authoritative"
**Status:** **done.** Real `laya.train.dry_run` accepts all 3,440 exported rows
**Advisory mode:** closed. Nothing trained. No weights changed.

---

## Why this was worth doing

The export previously passed only my own reimplementation
(`laya_contract_precheck.py`). That is not proof — it is exactly the failure mode that
produced the internal-shape defect: the exporter and the pre-check agreed with each
other because they shared the same wrong assumption about the schema.

`dry_run` was believed to need a GPU. It does not. It reads only the tokenizer and
`rl_agent_config.json`, never the model weights, so it runs on this CPU-only host.

## The isolated environment

```
/tmp/dp-audit/venv-laya047   <- NEW, laya 0.4.7 (torch 2.x + deps)
~/.venv-laya                <- untouched, laya 0.3.22 (no train module)
```

The pinned `.venv-laya` is byte-identical afterwards; the baseline checkpoint is
untouched. The isolation is the point: upgrading in place would have changed the
environment every earlier offline measurement ran against.

## Result — the authority speaks

```
TRAIN        2064 rows read, 2064 valid items, skipped: {}
CALIBRATION   688 rows read,  688 valid items, skipped: {}
HOLDOUT       688 rows read,  688 valid items, skipped: {}

max_len=1024, head_max_len=256   (the checkpoint's own budgets)
effective batch 64 (micro_batch 8 x grad_accum 8)
train file: 1858 train items / 206 calibration items -> 120 optimizer updates
```

**Zero skipped rows across all three splits.** The public `{type, instructions,
criteria}` schema is correct, the StateWork-id labels resolve, and the targets
normalise. This is the check that could have been wrong and was not.

## A discovery about how upstream fails

An internal-shape row is **not** reported as a skip. It is silently dropped, leaving
zero items, and `dry_run` then raises:

```
ValueError: n_items must be a positive integer, got 0
```

So the authority distinguishes the shapes by *failing*, not by reporting a reason.
The test asserts the raise, with a positive control asserting the same row in the
public shape succeeds — otherwise the rejection could pass for an unrelated reason.

## Tests

`qualification/test_authoritative_dryrun.py` (3 tests) runs real upstream in a
subprocess:

| Test | Asserts |
|---|---|
| `test_upstream_dry_run_accepts_every_exported_row` | all splits report `skipped={}` and `rows_read == valid_items` |
| `test_upstream_dry_run_rejects_the_internal_shape` | internal `{t,crit,ins}` row makes upstream raise |
| `test_upstream_dry_run_accepts_the_public_shape` | positive control: the same row in public shape is accepted |

Interpreter discovery probes candidates with a version-gated import (`>= 0.4.6`) and
takes the first that satisfies it. When none qualifies, the tests **skip** rather
than fail, so the pinned 0.3.22 environment stays green.

Verified in both interpreters:

```
pinned (.venv-laya, laya 0.3.22) : 161 passed, 3 skipped
isolated (venv-laya047, laya 0.4.7) : 3 passed   (authority active)
```

## Verification

| Check | Result |
|---|---|
| upstream `dry_run` on all splits | **3,440 rows, 0 skipped** |
| internal-shape rejection | proven against real upstream (raises) |
| public-shape acceptance | positive control passes |
| pinned env suite | **161 passed, 3 skipped** |
| ruff | only pre-existing C408/PLC0206 |
| `.venv-laya` modified | **no** |
| baseline checkpoint | untouched |

## What this does and does not establish

Establishes: the export format is right. The interface defect is closed, and the
next failure mode would no longer be schema-shaped.

Does **not** establish: that a trained checkpoint improves anything. Training still
needs the GPU (absent on this host) and a corpus check beyond 3,440 synthetic rows
before any improvement claim.

## Recommendation

Treat `dry_run` as the gate for any future export change: run
`qualification/authoritative_dryrun.py` before believing the local pre-check. The
local pre-check remains a fast convenience for the edit loop, not the authority.
