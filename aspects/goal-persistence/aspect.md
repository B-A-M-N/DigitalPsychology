# Aspect: Goal Persistence

Agents can lose the original objective during long execution.

Track the triple:

```text
original goal / current subgoal / current action
```

Periodically verify:

```text
Does this action still advance the original goal?
```

Goal drift often appears as **productive-looking work** — commits, tests,
refactors — none of it aimed at the actual objective. Distinguish drift from
legitimate subgoal decomposition: decomposition still traces to the goal;
drift does not. Long-context tests (`../evaluations/long-context-tests.md`)
probe whether original goal, constraints, unresolved blockers, and operator
corrections survive context accumulation.
