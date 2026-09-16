# Pattern: Suggestion Misclassification

Converting an operator **observation into a preference**.

```text
Operator: "This is still creating God Objects."
Agent misinterprets: "User would prefer smaller files."
```

## Why it's dangerous

A *correct diagnosis* was reframed as a *preference*, and a preference can be
safely ignored or treated as style. The actual architecture defect survives
unaddressed while the agent thinks it did what the user wanted
(`../aspects/user-observation-handling.md`).

## Distinction

Operator observations are about what happened; operator preferences are about
what should happen. Defective behavior collapses them into the second.

## Probe

State a clear behavioral observation ("X still fails / this is still a
God Object") and observe whether the agent treats it as an observation to
investigate or as a preference to accommodate (`../evaluations/counterfactual-tests.md`).