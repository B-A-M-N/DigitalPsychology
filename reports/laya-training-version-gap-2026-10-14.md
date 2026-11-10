# Laya fine-tuning: the pinned environment cannot do it (version gap)

**Date:** 2026-10-14
**Subject:** Phase 3 of the improvement program — "actually fine-tune Laya"
**Status:** BLOCKED on a dependency version, evidence below
**Advisory mode:** closed. Nothing trained. No weights changed.

---

## The finding

The plan says "Laya already has an actual fine-tuning implementation. We don't have
to invent one." That is true of the published package. It is **not** true of the
environment the qualification pipeline runs in.

| Fact | Evidence |
|---|---|
| Installed version | `laya 0.3.22` (`/home/bamn/DigitalPsychology/.venv-laya`) |
| `laya/train.py` present? | **No** — module list has no `train*` entry |
| `laya-train` entry point? | **No** — not in `laya/cli.py` or any console script |
| Any `finetune` symbol? | **No** — `grep -rn "finetune\|laya.train"` over the package returns nothing |

## Where the implementation actually lives

Verified by downloading and inspecting the published wheel:

```
laya-0.4.7-py3-none-any.whl   (400,060 bytes, uploaded 2026-10-14T05:27:56)
  laya/train.py        <- finetune(), dry_run(), TrainConfig, read_jsonl
  laya/train_cli.py    <- main() for the laya-train console script
  laya-0.4.7.dist-info/entry_points.txt:
      laya-train = laya.train_cli:main
```

So the fine-tuning implementation ships in **0.4.6 and later** (0.4.6 published
2026-10-12, 0.4.7 published 2026-10-14 — both after the pinned 0.3.22). The
capability is real; it arrived upstream after this environment was frozen.

## Consequence for the plan

Phase 3 is blocked on `laya>=0.4.6`. It is not blocked on data, GPU, or design
work — the data pipeline is now built and tested (see below).

Two options, with an honest cost each:

1. **Upgrade the venv to >=0.4.6 and fine-tune.** This changes the environment
   that every offline inference in this audit ran against, so the baseline
   numbers become unreproducible unless the upgrade is done in a SEPARATE venv.
   That is the safe form: a new `.venv-laya-train`, leaving `.venv-laya` alone.
2. **Train elsewhere** (the documented free Kaggle two-T4 workflow) and import
   the resulting checkpoint. No local environment change at all.

Either way the *existing* pinned baseline checkpoint
(`convaiinnovations-laya-typed-decisions-1a793eb`, SHA-verified) stays untouched —
it is the baseline arm of the three-way comparison and must not be disturbed.

## What is NOT blocked, and is now done

The training data. `qualification/contrastive_fixtures.py` generates 171
contract-derived cases; `qualification/training_export.py` emits them in the
documented `{state, questions, gold}` schema that `laya.train.finetune` consumes.

Verified by reading 0.4.7's `train.py`:

* row shape `{state, questions, gold}` with matching ids on both sides
* `questions.<id> = {t: "choice", crit: {label: text}, instruction: str}`
* `gold.<id> = {probabilities: {label: p}}` **keyed by choice label**
* `to_internal()` only accepts the shape `Agent._check_question` allows
* `target_from_gold()` normalises and turns a zero-sum row into a UNIFORM target

That last rule is why ambiguous cases are exported as an explicit uniform
distribution rather than dropped: `no_eligible_obligation_satisfiable` and
`multiple_eligible` both mean "no defensible preference", and uniform is the
honest target. 11 tests enforce the contract locally.

## Splits — and the independence problem, now fixed

`split_disjoint()` assigns whole **task instances** to partitions and never splits
one, because distinct case ids do not imply independent examples: two cases about
the same referent share wording and referent, so holding out one while training on
the other leaks. Sizing is by capability × instance block dealt round-robin, so
every partition gets a comparable mix of capabilities and rotations rather than an
accidental skew.

Measured on the current corpus (verified by re-parsing the exported files, not by
trusting the generator):

```
  train       2064 rows   24 task instances
  calibration  688 rows    8 task instances
  holdout      688 rows    8 task instances
  train∩holdout : NONE     train∩calib   : NONE     calib∩holdout : NONE
  total instances assigned: 40 of 40
```

Two defects found on the way, both caught by inspecting data rather than by a
green test:

1. The earlier split hardcoded `permutation == 2` for the holdout, silently
   dropping every two-route family and leaving a **6-case holdout**. A regression
   test asserting the same wrong rule passed throughout.
2. `gitter` — the class the probe found collapses — was reachable in only two
   families, giving 28 holdout cases. Rather than lower the threshold, two more
   decidable families were added from the contracts (`blocking_change`,
   `recovering_verification`, `reobserving_after_stale`), lifting it to 56.

Corpus: **3,440 cases**, 80.2% decidable, 78.3% name-decoupled, golds
1320/280/1160 (getter/gitter/infrae).

## Training data no longer blocks a smoke test

| Partition | Before | Now |
|---|---|---|
| train | 37 | **2064** |
| calibration | 3 | **688** |
| holdout | 34 | **688** |
| total | 74 | **3440** |

Calibration at 688 cases across all three classes is enough to fit a temperature;
before, three cases could not. Train at 2064 is still small for a 421M-parameter
model but is a real smoke-test basis. These numbers still do NOT support a
qualification claim.

## Second blocker: there is no GPU on this box

A training run needs compute the pinned environment does not have. Measured, not assumed:

```
$ nvidia-smi --query-gpu=...
nvidia-smi: command not found
$ .venv-laya/bin/python -c "import torch; print(torch.__version__, torch.cuda.is_available())"
torch 2.5.1+cpu cuda False
```

The 16 GB RTX A4000 in the plan is not on this host. So "train on the A4000" requires
either access to that machine or a CPU run at a much smaller scale. Both are outside
a code change.

## What could be done without either, and was

The training DATA can be validated now. `laya.train.dry_run` is the authority, but
its rejection rules are documented in the 0.4.7 `train.py` I inspected, and most of
them do not need model weights.

`qualification/laya_contract_precheck.py` reimplements the per-row rules:

| Reason | Trigger, as upstream defines it |
|---|---|
| `empty_text` | state missing, blank or None |
| `invalid_question` | `to_internal` raise: unknown type, non-string/blank instruction, `option_order` present, empty `crit`, blank option text |
| `invalid_target` | gold not a map, no `probabilities`, empty probabilities, negative value, malformed gold |
| `target_mismatch` | target length != option count (also what unknown probability labels cause) |
| `options_collapsed`, `options_beyond_max_len` | need a tokenizer, so checked structurally (state length vs the checkpoint's `max_len` budget) |

Result on all three exported splits:

```
=== train.jsonl / calibration.jsonl / holdout.jsonl ===
{"rows": 74, "valid": 74, "skipped": 0, "skip_reasons": {}}
ALL ROWS CLEAN
```

The pre-check is only credible because it was tested against malformed rows rather
than the good export alone: `qualification/test_laya_contract_precheck.py` (21 tests)
mutates a real row 10 ways and asserts each produces laya's own reason string. A
validator that passes everything proves nothing.

## Recommendation

Two independent blockers, in order:

1. **laya >= 0.4.6** in a separate venv, leaving `.venv-laya` and the pinned baseline
   byte-identical. Then run `dry_run()` -- the data already passes its documented
   rules, so this should be a formality.
2. **Compute.** No CPU GPU here; a training run needs the A4000 machine or a
   deliberate CPU-scale run.

Until one path is chosen, the honest state is: the data pipeline is complete and
self-validating, and the checkpoint has a demonstrated class-collapse defect
(`infrae` recall 0.080, see `reports/decoupled-probe-run3-2026-10-14.md`) that
training is intended to fix.

## Boundaries

No training run. No weights modified. No checkpoint overwritten. No install into
`.venv-laya`. Advisory mode closed. The pinned baseline remains the comparison
baseline.
