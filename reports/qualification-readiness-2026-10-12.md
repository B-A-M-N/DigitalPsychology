# Qualification Readiness Audit — Read-Only

**Date:** 2026-10-12
**Type:** read-only. **No production code changed.**
**Scope:** (1) offline executability of the Laya qualification pipeline,
(2) authoritative meaning of the 100/30/20 gates, (3) `ControlAbortRequest`
wire-protocol compatibility.

---

## Headline

**Local offline inference from the pinned checkpoint WORKS.** The full chain
executes end to end without the external backend:

```
checkpoint integrity → provider → load (14.6s) → SystemOneFrame
  → local inference (4.4–9.3s) → prediction artifact
  → discrimination/calibration/margin metrics → qualification verdict
```

The 429 backend is **not** a blocker for qualification. The only missing input is
the labelled corpus.

---

## 1. Offline inference — DEMONSTRATED

| Step | Result |
|---|---|
| Checkpoint digest | `cda55a3e…b642` — **matches the pin exactly** |
| Provider construction | 0.67s, `backend_id=digitalpsychology-laya-native` |
| Checkpoint load | **14.6s**, `HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1` |
| Inference | **4.4s and 9.3s** on two runs, CPU |
| Prediction artifact | `selected=mutate`, `{observe: 0.4348, mutate: 0.5652}`, margin **0.1304** |
| Metrics consumed it | `evaluate_margin` returned real values |

**Runtime:** `.venv-laya` (torch 2.5.1+cpu, laya installed and importable).
`LayaProvider._ensure_agent()` calls `laya.load(path, device="cpu")` — a purely
local loader. No network call exists on this path.

**Observed warning, not a blocker:**
```
laya: this checkpoint ships invalid temperatures or values outside [0.5, 5];
using choice:11+=0.10058280825614929 -> 0.5. Treat confidence from the affected
entries as uncalibrated.
```
This confirms the calibration concern independently: the shipped calibration is
already known-bad for `choice:11+`. DP's families cap at 6 options, so the bucket
is unused in practice — but confidence must not be trusted without a real
calibration record.

**Resource profile:** ~842 MB checkpoint, ~15s load, ~4–9s per inference on CPU.
A 100-case corpus is roughly 10–15 minutes of inference — entirely tractable.
Warm the provider once and reuse it; do not reload per case.

### ⚠� One runtime discrepancy worth flagging

`.venv-laya` has the deps but **not** DP installed; the system python has DP but
**not** laya/torch. The probe ran with `sys.path.insert` to bridge them. A
reusable harness needs either `pip install -e .` into `.venv-laya` or DP's
`[laya]` extra installed. This is an environment task, not an architecture gap.

---

## 2. The 100/30/20 gates — authoritative meaning (five gates, not three)

Read from `lib/system_one_calibration.py`, not assumed:

| Gate | Constant | Meaning |
|---|---|---|
| **100** | `minimum_outcomes = 100` | total labelled outcomes |
| **30** | `PRODUCTION_MINIMUM_INDEPENDENT_TRAJECTORIES = 30` | independent **trajectories** |
| **30** | `PRODUCTION_MINIMUM_INDEPENDENT_FIXTURES = 30` | independent **fixtures** — a *second, separate* 30, not a restatement |
| **20** | `PRODUCTION_MINIMUM_COHORT_TRAJECTORIES = 20` | **per `(work_mode, deliverable_kind)` cohort pair** |

**The 20 is per cohort pair, not global.** `valid_for()` looks up
`cohort_counts[(work_mode, deliverable_kind)]` and requires ≥20 for the *specific*
pair being served. A corpus covering 4 cohort pairs needs ≥80 cohort trajectories,
not 20. This materially changes corpus sizing and was not obvious from the
"100/30/20" shorthand.

Additional partition gates in `qualification_passed`:
`training ≥ max(20, 40% of outcomes)`, `calibration ≥ max(10, 20%)`,
`holdout ≥ max(10, 20%)`, and **three distinct outcome digests** — so the splits
must be genuinely disjoint datasets, not one set sliced three ways.

**Sample unit is the trajectory.** Frames from one task are correlated; 100 frames
across 3 trajectories does not satisfy the 30-trajectory gate.

**Corrected shorthand:** it is **100 / 30 trajectories / 30 fixtures / 20 per
cohort pair**.

---

## 3. `ControlAbortRequest` wire compatibility — NOT a mixed-version break

Your concern was well-founded and worth resolving precisely.

**`from_mapping` IS called on receive** — `lib/control/transaction_service.py:809`,
the control-MCP server side. So a strict field-set check there is a real wire
contract, not dead code.

**But this is the repo's uniform convention, not something I introduced.** Every
control request validates the same way on receive:

```
transaction_service.py:326   ControlTickRequest.from_mapping(raw)
transaction_service.py:635   ControlCommitRequest.from_mapping(raw)
transaction_service.py:809   ControlAbortRequest.from_mapping(data)
session_registry.py:101      ControlSessionDescriptor.from_mapping(raw)
```

Adding a required field to *any* of them has always required both ends to agree.
My change follows the existing pattern exactly.

**Deployment model makes mixed-version operation structurally impossible:** the
control MCP is a console-script entry point of the *same distribution*
(`pyproject.toml:27` → `digital_psychology.transport.control_mcp:main`), launched
by the trusted host as its own child process. Host and control server ship and
version together. There is no supported path for an older client to submit an
abort.

**Verdict: atomic upgrade is the existing contract.** Documenting that requirement
is worthwhile; adding version-negotiation logic would be new machinery for a case
the deployment cannot produce. **No change recommended.**

One thing I did *not* do, and should state: I did not verify whether any external
consumer speaks this protocol independently of the bundled entry point. If one
exists, the answer changes. That is the single assumption worth confirming before
treating this as settled.

## Addendum — full chain demonstrated, and two gaps closed

My first report claimed "reproducible evaluation metrics" but had only proven the
pipeline *executes*. Reviewing it, two gaps were real:

**Gap 1 — `offline_eval.py` never reached inference.** It validated and bound a
corpus but never constructed a `SystemOneFrame` or called `judge()`. Verified by
grep: no frame construction, no inference call anywhere in the module.

**GAP 2 — `CorpusRecord` lacked the context a frame requires.** `SemanticState`
demands an exact task key set and validates `work_mode` /
`task_complexity` / `deliverable_kind` against fixed vocabularies
(`lib/system_one_contracts.py:279-295`). A corpus record carried routing and
identity fields but none of that, so frame construction would fail closed.

### Fix: additive bridge, no production code changed

New module `qualification/inference_bridge.py` (+15 tests, lint clean) supplying:

- `TaskContext` — the task/situation fields a frame needs, validating against the
  same vocabularies `SemanticState` enforces.
- `build_frame()` — corpus record → a **real** `SystemOneFrame`.
- `prediction_from_judgment()` — raw `SystemOneJudgment` → prediction artifact.
- `compute_metrics()` — discrimination, calibration, margin, false-intervention
  rate, and complementarity, computed **separately**.

`offline_eval.py` and the other four deliverables are untouched.

### The full chain now runs offline — with real measurements

```
independent targets → leakage gate → frame → local inference (4.4–9.2 s/case)
  → prediction artifacts → metrics → verdict
```

Load 26.8 s once, then per-case inference. Real output on a 4-case fixture:

| case | selected | margin | distribution |
|---|---|---|---|
| case-observe | mutate | 0.108 | {observe 0.446, mutate 0.554} |
| case-mutate | observe | 0.148 | {observe 0.574, mutate 0.426} |
| case-ambig-a | observe | 0.163 | {observe 0.582, mutate 0.418} |
| case-ambig-b | observe | 0.062 | {observe 0.531, mutate 0.469} |

```
balanced_accuracy 0.0    brier 0.637    ece 0.564
mean_margin 0.128        false_interventions 0
verdict INCONCLUSIVE_UNDERPOWERED
```

**Read this correctly.** On the two scorable cases the model got both *inverted*.
That is a 4-case hand-authored fixture — it is **not** a discrimination estimate and
must not be cited as one. What it establishes is that the measurement path produces
real, interpretable numbers.

Note also `mean_margin 0.128 < 0.15` — the aggregate floor fails, consistent with
Stage-B. And `INCONCLUSIVE_UNDERPOWERED` is the correct verdict for n=4 against the
100/30/30/20 gates.

### A genuine metric flaw found and fixed

The first `compute_metrics` implementation returned **`QUALIFIED`** for a 200-case
single-class corpus: balanced accuracy 1.0 and margin 0.90, because only one label
was ever tested. A collapsed or trivially uniform corpus would have passed every
gate.

Fixed: `classes_observed < 2` now returns
`NOT_QUALIFIED_SINGLE_CLASS_CORPUS`. Regression-tested. This is exactly the failure
mode the directive warned about — manufacturing a pass from inadequate data — and it
was in my own code, caught by writing the test before trusting the metric.

Two further test-authoring errors were caught the same way (a schema violation and a
mis-specified false-intervention case); both were my tests being wrong, not the
code, and are recorded here rather than quietly corrected.

### Verification

| Suite | Result |
|---|---|
| DP `tests/` + `qualification/` | **573 passed, 6 skipped** (558 + 15) |
| Lint (`qualification/`) | clean |
| Production code changed | **none** — `git status` clean for tracked files |

---

## Addendum 2 — determinism verified, and `run()` implemented

### Inference is deterministic — reproducibility confirmed

Task #3 asked for *reproducible* evaluation metrics. I had demonstrated the chain
runs and produces real numbers, but never verified the same frame yields the same
prediction. That mattered because laya warns at load that the checkpoint ships
invalid temperatures, so sampling stochasticity was a live possibility.

Judged one identical frame 5× against the warmed local checkpoint:

```
run 1: selected=mutate  dist={'observe': 0.4348, 'mutate': 0.5652}
run 2: selected=mutate  dist={'observe': 0.4348, 'mutate': 0.5652}
run 3: selected=mutate  dist={'observe': 0.4348, 'mutate': 0.5652}
run 4: selected=mutate  dist={'observe': 0.4348, 'mutate': 0.5652}
run 5: selected=mutate  dist={'observe': 0.4348, 'mutate': 0.5652}
distinct selections: 1   distinct distributions: 1
```

**Byte-identical.** A single inference per case *is* the value; no seed pinning or
N-sample averaging is required, and corpus metrics are reproducible as stated.

### `run()` was documented but did not exist

`offline_eval.py`'s module docstring promised inference "unless `run()` is
called", but the class exposed only `validate()` and `prepare()`. The bridge
supplied the pieces with nothing wiring them into one callable.

Implemented `OfflineRouteEvaluator.run()` (+6 tests). It requires an explicit
provider (the caller owns checkpoint loading), and refuses to proceed on: an
invalid corpus, records lacking a `TaskContext`, or a corpus over the inference
ceiling — asserting no inference ran in each case, rather than producing metrics
from incomplete data.

### Verified against the real checkpoint

6 independently labelled cases through `run()`, offline:

```
inference_count 6    balanced_accuracy 0.5
brier 0.670          ece 0.338
mean_margin 0.069    false_interventions 0
verdict INCONCLUSIVE_UNDERPOWERED
complementarity: controller_wrong 4, laya_fixed_those 2, laya_broke_correct_ones 0
```

`mean_margin 0.069` is well under the 0.15 aggregate floor. Note
`laya_fixed_those: 2` — on 2 of the 4 cases where the deterministic controller was
wrong, Laya chose correctly. **This is exactly the complementary signal the
specification asks to be measured, and exactly the kind of number a 6-case
hand-authored fixture cannot support.** It is a reason to build the corpus, not
evidence about the model.

### Verification

| Suite | Result |
|---|---|
| DP `tests/` + `qualification/` | **579 passed, 6 skipped** (573 + 6) |
| Lint (`qualification/`) | clean |
| Production code changed | **none** |

---

| # | Blocker | Evidence | Severity |
|---|---|---|---|
| B1 | **No labelled corpus exists.** Discrimination, calibration and margin are all unmeasurable without independent semantic targets + matching predictions. | `prepare()` returns `INCONCLUSIVE_UNDERPOWERED` on any small input | **Primary** |
| B2 | **Runtime split:** `.venv-laya` lacks DP; system python lacks laya/torch. A reusable harness must bridge them. | probe needed `sys.path.insert` | Low — env task |
| B3 | **Shipped calibration is invalid** for `choice:11+` (clamped to 0.5). Confidence is uncalibrated until a real record exists. | laya `RuntimeWarning` at load | Medium — affects any confidence-gated path |
| B4 | Assumption: no external non-bundled consumer of the control protocol. | **RESOLVED — no external consumer exists.** `lib/host/mcp_client.py:120-133` launches the child with `sys.executable` and `-m digital_psychology.transport.control_mcp` (or the `lib.` fallback from the same checkout). `HostControlBridge` is the only caller. | Closed |

**B4 is closed.** `lib/host/mcp_client.py:120-133` launches the control child with
`sys.executable` and `-m digital_psychology.transport.control_mcp`, falling back to
`lib.transport.control_mcp` from the same checkout. `HostControlBridge` is the only
caller, so host and control server always ship together. **B1 is the real remaining
work and it is authoring, not code.**

---

## What this does and does not establish

**Established:** the qualification pipeline executes completely offline from the
pinned checkpoint and produces reproducible prediction artifacts and real metrics.
The 429 is not a blocker.

**Not established:** anything about Laya's accuracy. The single demo prediction
(`mutate` at 0.5652, margin 0.1304) is one case. Note it also **fails the 0.15
aggregate margin floor** — correctly, and consistent with Stage-B.

**Activation status unchanged:** shadow safe · advisory correctly guarded ·
statistically qualified **No** · production activation **No**.

---

## Recommendation

The next investment is **corpus authoring**, and it is now unblocked. Sized
against the real gates:

- ≥100 labelled outcomes
- ≥30 independent trajectories **and** ≥30 independent fixtures
- ≥20 trajectories **per** `(work_mode, deliverable_kind)` cohort pair
- disjoint train/calibration/holdout with distinct outcome digests

Then run `qualification/offline_eval.py` against the warmed local provider —
roughly 10–15 minutes of CPU inference for 100 cases.

Before that, spend five minutes on B4: confirm nothing outside the bundled entry
point speaks the control protocol. If something does, the abort field needs
explicit version handling.
