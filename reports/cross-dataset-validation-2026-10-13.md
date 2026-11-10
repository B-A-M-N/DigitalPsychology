# Cross-Dataset Structural Validation — Local Corpus vs FreeInference

**Date:** 2026-10-13
**Answer to the question:** yes, this is worth doing — but only as a **representativeness
check**, not a label source. And it surfaced a real bias in the local subset.

**Method:** compared observable structure across the two datasets with **no session
identity assumed**. No join was attempted or needed.

---

## What was downloaded

Not 9 GB. One week of `processed/` Parquet — **22 MB total** — because the per-week
tables are far smaller than the full release. `pyarrow` installed into a scratch dir.

| File | Size | Contents |
|---|---|---|
| `tool_calls.parquet` | 0.17 MB | 11,388 rows, one per tool call |
| `sessions.parquet` | 0.01 MB | 125 sessions, harness + parent linkage |
| `requests.parquet` | 22 MB | 10,738 LLM requests with timing/tokens |

FI tool_calls carry **`outcome`** ∈ {ok, error, timeout} and a **`category`** taxonomy
(`Read`, `*Search`, `*File Ops`, `Edit`, `Write`, `*Build/Test`, `*Git/VCS`, `*Network`,
`*Interpreter`, `Subagent`, `Ask User`, `*Wait/Poll`). That category layer is the most
useful thing the FI release offers here.

## The comparison

| metric | local (crush, 66 episodes) | FreeInference (1 week, 125 sessions) |
|---|---|---|
| actions | 7,901 | 11,388 |
| errors | 289 | 1,079 |
| **error rate** | **0.0366** | **0.0947** |
| episodes with an error | 56 | 45 |
| recovered after error | 54 | 45 |
| **repeat rate after error** | **0.7037** | **0.4889** |
| mutate before observe | 0 | 2 |
| episodes with verify | 0 | 16 |

## What the numbers say

**Error rate: local is ~2.6× lower (0.037 vs 0.095).** Same order of magnitude, but
materially different. `crush` sessions succeed more often per action than the FI
population average across five harnesses.

**Repeat-after-error: local is ~1.4× higher (0.70 vs 0.49).** This is the more
interesting one — it suggests the local crush subset is *harder* or more prone to
unchanged retries than the FI average, not easier.

**`episodes_with_verify: 0` local vs 16 FI.** Local crush traces essentially never
invoke a build/test category. Combined with `mutate_before_observe: 0` (crush always
opens with `ls`), the local subset is **verification-poor**.

## Conclusion: what FI is actually good for here

The two datasets are measuring the same behaviours on different populations, and the
FI category taxonomy (`*Build/Test` vs `*File Ops` vs `*Git/VCS`) is a cleaner
observability surface than my hand-rolled verb mapping.

**FI's real value is as a calibration reference for the detectors**, exactly as
intended:

1. The `repeat_rate_after_error` detector can be tuned against a 125-session FI
   population rather than 66 local episodes.
2. The **0.70 local vs 0.49 FI** gap means a threshold calibrated on local data alone
   would be miscalibrated for the wider population. This is the concrete finding.
3. `episodes_with_verify: 0` is a genuine blind spot in the local subset — anything
   about verification behaviour must come from FI or from prospective capture, not
   from these traces.

**What FI cannot supply:** any semantic label. It has no message text, so no
`SemanticTarget`. Confirmed rather than assumed.

## Status — all five execution-order steps

| # | Step | Status |
|---|---|---|
| 1 | Tool-call completeness audit | ✅ 67,022 calls; outcomes linked; 18% of traces |
| 2 | Author + screen 3 pressure families | ✅ All 3 SATURATE, correctly rejected |
| 3 | **FI sample structural validation** | ✅ **22 MB, one week; 2 biases found** |
| 4 | Mine local trajectories for route decisions | ✅ 339 candidates → 66 Stratum A episodes |
| 5 | Keep qualification boundaries separate | ✅ Maintained throughout |

## What this changes

Before this step the plan was "label the 66 Stratum A episodes." That is now
qualified in two ways:

1. **They are verification-poor** (`episodes_with_verify: 0`), so they cannot support
   any claim about verification behaviour — only routing and recovery.
2. **Their error and repeat rates are biased** relative to the reference population,
   so any threshold tuned on them needs an FI-side sanity check before use.

So the honest scope for Stratum A is: **routing and recovery behaviour only**, on a
subset whose rates are documented as ~2.6× lower error and ~1.4× higher repeat than
the reference population.

## Remaining work

- **Independent labelling of the 66** — scoped now to routing/recovery, not
  verification. Human work; automating it invents ground truth.
- **Optional:** more FI weeks (each ~22 MB) to tighten the reference distribution
  before tuning any detector threshold. Cheap if wanted.
- **Blocked on external infra:** nothing further. The 429 has cleared and the FI
  download needed only 22 MB.

**Advisory status unchanged:** statistically qualified **No**, production activation
**No**. No labels exist yet, so no qualification claim is possible.
