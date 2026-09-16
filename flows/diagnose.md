# Flow: Diagnose (REPRODUCIBLE → HYPOTHESIZED)

Generate mechanism hypotheses.

## Mechanism classes

```text
instruction ambiguity          instruction conflict
context dominance              stale assumption persistence
proxy substitution             uncertainty avoidance
completion pressure            authority over-weighting
tool-result over-weighting     self-generated evidence
state-loss                     context compaction
architectural accretion        reward imitation
multi-agent interference
```

## Rules

- A diagnosis remains a **hypothesis** until intervention evidence supports it.
- Correlation is not mechanism: "the deploy preceded the failure" locates a trigger; it does not explain the information-processing pattern.
- Prefer the hypothesis that explains the *most observations with the fewest mechanisms* — but a single unifying story is not automatically correct.
- Check the named patterns catalog (`../patterns/`) against the observations before inventing a new mechanism class.
- Predict what the hypothesis forbids: a good mechanism hypothesis says what the agent should NOT do under specified conditions, which makes it testable.
