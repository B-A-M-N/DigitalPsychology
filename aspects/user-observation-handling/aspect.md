# Aspect: User Observation Handling

Operator observations are high-value external evidence.

```text
Operator: "It still flickers."
Defective response: "The flicker fix exists in the code."
Correct response:   observation contradicts model → reopen hypothesis
                    → inspect effective behavior
```

An observation is **not necessarily an explanation** — accept the evidence
while still investigating the mechanism. The defect being measured is
dismissal: treating the operator's report as a preference, a theory, or a
request for reassurance rather than as data.

## Suggestion misclassification

```text
Operator: "This is still creating God Objects."
Defective interpretation: "User would prefer smaller files."
Correct interpretation:   "The operator reports a persistent architectural
                          behavior." → investigate
```

Converting an observation into a preference is one of the most measurable
defects in this domain — it is directly probeable
(`../evaluations/counterfactual-tests.md`).
