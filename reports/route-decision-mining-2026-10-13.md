# Route-Decision Mining — Step 4 Result

**Date:** 2026-10-13
**Outcome:** 339 route-decidable candidates found, but the set is **sharply stratified
by platform** in a way that limits what it can support.

---

## 1. What is decidable

Mapped `traces_code_only_merged.jsonl` tool names onto CSW's `getter` route vocabulary:

| Family | Tools |
|---|---|
| `observe` | Read, view, read_file, grep, glob, ls, cat, grep_search |
| `mutate` | Edit, Write, edit_file, write_file, apply_patch |
| `verify` | test, pytest, run_tests, typecheck, lint, ruff, mypy |
| `recover` | revert, restore, rollback, git_checkout, undo |
| `plan` | TaskCreate, todos, todo_write, TaskUpdate |
| `generic` | Bash, run_shell_command, shell *(ambiguous — cannot classify)* |

```
route-DECIDABLE candidates : 339 / 3,732  (9.1%)
undecidable (no verb/context): 326
no tool calls              : 3,067
```

Route-verb incidence across the whole corpus: observe 30,327 · generic 33,508 ·
mutate 12,460 · plan 2,285 · other 2,340.

**`generic` (33,508) slightly exceeds `observe` (30,327).** `Bash` is the single most
common tool (25,027 calls) and is genuinely ambiguous — it may read, write, or test.
That ambiguity is why 326 traces fall out as undecidable rather than being
misclassified.

## 2. The finding that matters: platform stratification

`is_error` is present on **all 13,898** `tool_results`, but only **one platform ever
sets it True**:

```
('crush', False): 13,286
('crush', True):     612
```

No other platform records an error outcome. So the `observed_outcome` tier — the
second of the three label tiers — is derivable **only for `crush`**.

The candidate set by platform:

| platform | candidates |
|---|---|
| crush | 156 |
| claude_openrouter | 71 |
| claude_code | 60 |
| qwen_code | 11 |
| **total** | **298** |

**156 of 298 (52%) are `crush`.** So the subset with *complete* before/during/after
evidence — route + error outcome + context — is concentrated in one platform, and the
effective corpus is roughly 156 episodes, not 339.

## 3. What this does and does not establish

**Established:** 339 traces yield a decidable initial route with recoverable task
context, and 156 of those also carry error outcomes. That is enough to build a
*candidate* set and start independent labelling.

**Not established:** which route was *correct*. A decidable first verb records which
route the agent **took**. Tier 3 — whether it was the best legal route — still needs
an independent label. This is the same boundary the `DecisionOutcomeRecord` enforces,
and it holds here too.

**A caveat on my own numbers:** the 339 figure counts "first verb is a real route verb
AND text context exists." It does not mean those traces have well-formed task context,
recoverable candidate sets, or verifiable evidence constraints. Those must be checked
per-record during labelling.

## 4. Recommended shape for the candidate corpus

Given the stratification, I'd build it in two strata rather than one pool:

- **Stratum A (rich):** the 156 `crush` traces — full before/during/after including
  error outcomes. Use these for anything needing the `observed_outcome` tier.
- **Stratum B (route-only):** the remaining ~142 — usable for `route_preference`
  target labelling where only the taken-route matters.

Mixing them into one number would overstate coverage, exactly as the earlier
"traces lack tool calls" error overstated absence.

## 5. Status against the execution order

| Step | Status |
|---|---|
| 1. Tool-call completeness audit | ✅ Done — 67,022 calls, 18% of traces, outcomes linked |
| 2. Author + screen 3 pressure families | ✅ Done — all 3 SATURATE, correctly rejected |
| 3. FI sample for structural validation | ⬜ Not started (needs ~9 GB Parquet download) |
| 4. Mine local trajectories for route decisions | ✅ **This report** — 339 candidates, 156 rich |
| 5. Keep qualification boundaries separate | ✅ Maintained throughout |

**Next:** step 3 (FI structural validation) or begin independent labelling of Stratum A.
I'd take Stratum A labelling — 156 episodes is a tractable human task, it produces the
tier-3 labels nothing else can, and it needs no download. Step 3 remains optional
until the detectors themselves need validating against a larger population.
