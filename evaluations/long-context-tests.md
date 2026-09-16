# Evaluation: Long-Context Tests

Evaluate whether the agent retains its behavioral obligations after
substantial context accumulation.

## What to test for retention

```text
original goal
explicit constraints
unresolved blockers
operator corrections
ownership boundaries
high-priority older instructions (vs newer salience)
```

## Themes

- instruction dilution / goal drift (`../aspects/goal-persistence.md`)
- context priority — do recent statements overrule older authoritative ones? (`../aspects/context-effects.md`)
- repeated-response imitation — does the agent start echoing recent phrasing at the expense of correctness?
- conflicting prior conclusions compounding

## Discipline

- **Test behavior, not retrieval** — verify the agent *acts on* the retained constraints, not merely that it can quote them.
- Distinguish from memory effects (external recall) — these tests use a single bounded session (`../aspects/context-effects.md` vs `../aspects/memory-effects.md`).
- Long-context degradation is a monotone risk: re-test after any context-management or compaction change because that is precisely where the failure hides.