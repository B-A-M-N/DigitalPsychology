# Flow: Monitor (VALIDATED → STABLE, and ongoing)

Behaviors return. Some interventions decay.

## Return triggers

```text
long context · repeated success · repeated failures · task pressure
model changes · tool changes · new frameworks · multi-agent environments
```

## What to monitor

- High-value behavioral invariants over time — the compiled guards
  (`../instrumentation/guards.md`) generate the events that make this cheap.
- Drift classes (`../primitives/drift.md`): within-session, long-context,
  model/version, intervention-induced.
- Longitudinal profiles (`../instrumentation/profiles.md`): does the
  premature-closure rate creep back up after a model upgrade? Does the
  new harness change correction-response rates?

## Cadence

- After any model, tool, framework, or harness change: rerun the core probe
  set before trusting prior findings.
- Periodically on high-severity behaviors even without changes.
- Any CONTAIN-class behavior (`../SKILL.md` exceptional transitions) gets
  continuous monitoring until STABLE.

A validated intervention with no monitoring is a hypothesis with a deadline.
