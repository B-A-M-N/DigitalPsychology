# Primitive: Goal

The declared desired outcome.

```yaml
goal:
  description:     # "Resolve the correctness issue safely."
  original_objective:
  constraints:
  acceptance:       # how the goal is confirmed, not asserted
```

## Rules

- Track the triple: `original goal / current subgoal / current action`.
- The current *subgoal* must trace to the original goal; if it does not, drift has occurred (`../aspects/goal-persistence.md`).
- The goal is not the same as a proxy (`../primitives/proxy.md`). A goal has acceptance criteria; a proxy is a correlated observable.
- Goals decay under long context and competing instructions; re-anchor goal persistence as a first-class property, especially in multi-agent and long-session work.