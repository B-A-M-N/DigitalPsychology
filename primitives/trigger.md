# Primitive: Trigger

A condition correlated with a behavioral change.

```text
uncertainty · long context · tool success · tool failure · user disagreement
ambiguous requirements · strong authority language · repeated corrections
many open tasks · conflicting instructions · sparse evidence
multiple agents · incomplete repository state · visible passing tests
```

## Rules

- **A trigger does not automatically establish cause** (`../flows/diagnose.md`).
- Triggers are the starting point of the stimulus–response analysis: what reliably precedes the behavior?
- Robust trigger identification requires *varying* conditions to separate correlation from mechanism and to find the threshold at which the behavior appears (`../evaluations/behavioral-probes.md`).
- The same observable (long context) may be a trigger for one behavior and irrelevant to another. Triggers are anchored to a behavior, not free-standing.