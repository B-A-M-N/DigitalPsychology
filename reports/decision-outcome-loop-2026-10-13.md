# Decision-Outcome Loop — Local Trace → Triad Record → Qualification Corpus

**Date:** 2026-10-13
**Status:** implemented and demonstrated on real local data. **No production code changed.**
**Answers the directive:** *can existing DP/CSW/CFW records express a common
DecisionOutcomeRecord?* — **Yes. Nothing new was needed.** This module is an adapter
that joins identifiers which already exist.

---

## 1. Tool-call completeness audit — my earlier claim was wrong

I previously reported `msgs w/ tools: 0` for the local corpus. That was a **bad
sample**: I audited `traces_unified.jsonl` (6,144 traces, claude + openai historical
exports). The tool-bearing data lives in `traces_code_only_merged.jsonl`.

Auditing the correct file, 3,732 traces:

| platform | traces | with tools | % | tool calls |
|---|---|---|---|---|
| claude_code | 199 | 184 | 92% | 29,006 |
| qwen_code | 43 | 41 | 95% | 4,062 |
| crush | 235 | 218 | 93% | 13,979 |
| claude_openrouter | 381 | 222 | 58% | 19,975 |
| **total** | **3,732** | **665** | **18%** | **67,022** |

**Your DATA_CARD's ~67k was right.** Both prior claims were partly correct: the "0"
came from the historical-export majority, the card's figure from the live-CLI minority.
Tool data is *concentrated*, not absent.

**Critically, outcomes are recorded too.** 19,236 `role=tool` messages, 13,898
`tool_results`, and calls link to results by `tool_call_id`:

```
CALL  name=ls      id=3c7e5291-6212-49ac-a
RES   keys=[content, data, is_error, metadata, mime_type, name, tool_call_id]
      tool_call_id=3c7e5291-6212-49ac-a  is_error=False
CALL  name=bash    id=a00fb3d1-21db-408f-a
RES   tool_call_id=a00fb3d1-21db-408f-a  is_error=True
```

Action *and* consequence, linked. That is what makes tier-1 labels derivable.

## 2. `DecisionOutcomeRecord` — the smallest adapter

`qualification/decision_outcome.py` (+14 tests). Joins identifiers that already exist:

| Field group | Existing source |
|---|---|
| task context, candidates, controller selection | DP `ControlSessionDescriptor` + prepared-decision basis |
| Laya shadow judgment, confidence, **counterfactual** | DP `SemanticInfluenceReceipt` |
| CSW activation, eligibility, revision | CSW `CSWBehaviorSnapshot` |
| CFW enforcement | CFW gate receipts on the DP commit receipt |
| observed outcome | DP `InterventionOutcome` / host evidence |

One locally generated `correlation_id` ties the episode together — no cross-dataset
join required.

### The three label tiers, structurally separated

| Tier | Derivable? | Mechanism |
|---|---|---|
| `contract_validity` | **Yes, automatic** | CSW legality + CFW gates + required evidence |
| `observed_outcome` | **Yes, automatic** | host-authoritative execution |
| `semantic_preference` | **No** | independent labeller only |

`semantic_preference` stays `None` unless independently labelled. A contract validator
can establish an action was legal; it **cannot** establish an otherwise-legal StateWork
was the best choice. `derive_contract_violations()` produces tier-1 labels from:
controller-selection-not-active, CSW-rejected transitions, CFW gate blocks, and
unsatisfied required evidence.

`AfterRecord` structurally forbids `intervention_improved=True` without
`counterfactual_available=True` — observing a route succeed does not prove the
deterministic route would have failed.

## 3. One complete episode — demonstrated on real data

Ran the full chain against a real `crush` trace (`ab99fc462e9bc58a`):

```
STEP 1  observe    : 10 linked tool calls, 0 errors, verbs {edit, glob, ls, view}
STEP 2  map        : controller selection 'observe' from first observed action
STEP 3  tiers      : contract_validity=illegal
                       - required evidence unsatisfied: ['repository_truth']
                     observed_outcome=unknown
                     semantic_preference=None        <-- honestly unlabelled
STEP 4  corpus     : corpus record built, leakage check PASS
STEP 5  replay     : correlation ID stable on re-derivation, 10 actions preserved
```

**The observation → label-tier → corpus path is closed for real local data.** Tier 1
was machine-derived (and flagged a genuine contract violation: an `edit` occurred with
no satisfied `repository_truth`). Tier 3 was left `None` because no independent label
exists — which is the correct behaviour, not a shortfall.

**Not proven:** Laya's accuracy. No Laya judgment existed for a historical trace, so
tiers 2 and 3 are unpopulated for it. That requires a **prospective instrumented
session**, not a join.

## 4. On the FI dataset

Agreed, and the analysis above changes the recommendation. The join is no longer
blocking, because it is no longer needed:

- **Local corpus** → semantics + action + outcome, for `DecisionOutcomeRecord`
- **FI traces** → a large behavioural *reference population* for validating that the
  failure detectors and retry patterns are representative, and for mining realistic
  loop/recovery sequences rather than inventing them

Not a session join; a cross-dataset validation of the detectors themselves. ~9 GB of
Parquet is enough for that; the 108 GB raw trace download is not justified.

## 5. Verification

| Suite | Result |
|---|---|
| DP `tests/` + `qualification/` | **593 passed, 6 skipped** (579 + 14) |
| Lint | 4 remaining `C408` — the `values = dict(...)` pattern already used in `test_fast_loop.py`, `test_policy_selector.py`, `test_system_one_runtime.py`; not a new deviation |
| Production code changed | **none** — tracked tree clean |

## 6. What remains

**Caveats carried forward from your own data card, and they matter:**
- The local release still requires manual privacy/source-rights review before any
  external use. Keep it private.
- You reported a credential-looking value in a preview not replaced by the expected
  redaction placeholder. That should be examined before anything leaves this host.
- Labels in the card are self-described weak labels — usable for filtering, not as
  ground truth. That is exactly why tier 3 is manual.

**Next step (prospective, not historical):** instrument one live FI-powered session at
the harness boundary to capture Laya's shadow judgment alongside execution. That
populates tiers 2 and 3 for a *new* record and is the only way to test Laya against
real outcomes. No advisory activation, no architecture expansion required.

**Then:** the four gates — observation → qualification → controlled intervention →
measured adaptation — with the causal comparison being the last and hardest part.
