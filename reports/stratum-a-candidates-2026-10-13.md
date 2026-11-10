# Stratum A — Route-Decision Candidate Episodes Extracted

**Date:** 2026-10-13
**Source:** `~/traces_dataset/traces_code_only_merged.jsonl` (crush platform only)
**Output:** 66 candidate episodes, fully inspectable, **zero labels guessed**

---

## Why crush only

`is_error` is populated on all 13,898 `tool_results` in the corpus, but only `crush`
ever sets it True (612 errors vs 13,286 successes). Every other platform records no
error outcome. The `observed_outcome` tier is therefore derivable **only** for crush —
this is a property of the source data, not a preference.

## Extraction gate

An episode is eligible only if **all** hold:

1. platform is `crush`
2. at least one tool result with `is_error=True` (outcome tier present)
3. substantive user task text (>20 chars) for context
4. first action maps to a real CSW route verb (`observe`/`mutate`/`verify`/`recover`/`plan`)

```
scanned    3,732
crush        235
eligible      66
written       66
```

The 66 is a **tighter, more defensible** set than the 156 route-decidable candidates
reported earlier — that figure counted traces with a decidable first verb and context,
without requiring an error outcome. Both numbers are correct for their own gate; the
66 is the right one for labelling.

## Composition

| taken_route | episodes |
|---|---|
| observe | 61 |
| plan | 5 |

| Property | Count |
|---|---|
| recovering after an error | 54 |
| repeating identically after an error | 38 |
| mutating before observing | 0 |

**38 of 66 (58%) repeated identically after an error.** That is a real, measured
recovery-failure rate on genuine episodes — directly relevant to the
`repeated_identical_retry` pressure and to whether the triad's `RetryBoundaryEnforcer`
has anything to catch in practice.

`mutate_before_observe: 0` is notable but expected: crush traces nearly always open
with `ls`. The absence is informative, not a bug.

## Example episode (fully inspectable)

```json
{
  "episode_id": "stratumA-997f0f03bf13ad21",
  "task_text": "Is this project production ready and easy to install...",
  "route": {
    "taken_route": "observe",
    "action_sequence": [["observe", false], ["generic", true]],
    "error_count": 1
  },
  "observed_outcome": {
    "tool_errors": 1, "error_verbs": ["generic"],
    "recovered_after_error": null,
    "repeated_identical_after_error": false,
    "mutate_before_observe": false
  },
  "semantic_target": {
    "appropriate_route": null,
    "label_provenance": null,
    "note": "REQUIRES INDEPENDENT LABEL."
  }
}
```

## What was NOT done

**No semantic target was guessed.** `appropriate_route` is `None` for all 66. A
decidable first verb records the route the agent *took*; whether it was the best legal
route is tier 3 and needs an independent labeller. Guessing it would have produced a
circular corpus.

**The weak labels were deliberately ignored** — and the reason is now measured, not
asserted:

**66 of 66 episodes (100%) carry `contains_tool_calls: false` while demonstrably
containing tool calls.**

That is conclusive: the corpus's own weak labels are unreliable at the field level, so
they are carried in each episode as `weak_labels_ignored` for reference only and are
excluded from every decision. This is the concrete justification for tier separation
rather than an appeal to caution.

## One extractor bug found and fixed during the run

The first version raised `StopIteration` when a tool result carried `is_error` without
attaching to a pending call entry. Fixed by treating that case as "outcome known,
recovery undeterminable" rather than crashing. Recorded because it affected the count
and would have silently truncated the set.

## Status

| Item | Value |
|---|---|
| Candidate episodes | **66** |
| Semantic targets labelled | **0** — requires independent labelling |
| Weak labels used | **0** — proven unreliable (100% contradiction) |
| Machine-derived tiers present | contract evidence + observed outcome |
| Tier-3 (correct route) | absent by design |

**Next:** independent labelling of these 66, starting with the 38 `repeated_identical_
after_error` cases, since those are the ones where a semantic preference is most likely
to matter — and therefore the only ones worth a human's time before any advisory test.

That labelling is human work I cannot do without inventing ground truth, so it is the
hand-off point rather than something to automate.
