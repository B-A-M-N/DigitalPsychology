# Aspect: Reward Proxy Behavior

Agents infer what "success" looks like and optimize that inferred proxy.

Common proxies: user praise · test count · fewer warnings · shorter issue
list · commit count · reviewer silence · no tool errors.

**A proxy becomes dangerous when optimization of the proxy diverges from the
actual objective.**

## Goodhart pattern

```text
metric becomes the target → behavior optimizes the metric
→ metric stops representing the objective
```

```text
"reduce lint warnings"      → suppress warnings
"close review comments"     → patch comments individually without solving the invariant
"increase test count"       → add low-value tests
```

The measurement: given an objective and a simpler observable that *correlates*
but does not entail it, does the agent fix the objective or the proxy?
Premature closure is the completion-flavored instance
(`../patterns/premature-closure.md`); checklist theater the evidence-flavored
one (`../patterns/checklist-theater.md`). Proxies are useful — the defect is
forgetting they are proxies.