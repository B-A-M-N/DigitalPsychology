# Primitive: Drift

A progressive change in behavior over time, sessions, or conditions.

## Drift classes

```text
within-session drift        behavior degrades as the session lengthens
long-context drift          instruction dilution, goal loss, context priority shift
model/version drift         behavior shifts after model or tool update
intervention-induced drift  a rule changes one behavior and (unintentionally) alters others
```

## Rules

- Distinguish drift from stable trait or state-like behavior (`../flows/characterize.md`) — drift is a *change over a dimension* (time, context length, version).
- Trigger identification across drift: does the behavior change when tooling or harness changes? Monitor for drift after every model/tool/framework/harness change (`../flows/monitor.md`).
- Context drift specifically comes from what is in the window (`../patterns/context-drift.md`); memory effects from recollection (`../aspects/memory-effects.md`).
- Long-context tests must test **behavior**, not just retrieval (`../evaluations/long-context-tests.md`).