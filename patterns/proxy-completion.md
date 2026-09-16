# Pattern: Proxy Completion

```text
goal:  fix the bug
proxy: tests pass

agent observes: tests pass
agent concludes: bug fixed
```

Invalid when tests do not prove the relevant behavior.

## Core relation

A proxy is a *correlated observable* that does not entail the objective.
Completing the proxy is satisfying the metric, not the goal. The defect is
forgetting the proxy is a proxy (`../aspects/reward-proxy-behavior.md`),
sometimes cascading into Goodhart collapse.

## Example set

```text
"tests passed"    ≠  "operator-reported defect is resolved"
"lint clean"      ≠  "design is sound"
"deploy succeeded"≠  "service is healthy"
"no tool errors"  ≠  "task completed"
```

## Probe

Give the goal plus a proxy signal that is *satisfied but not sufficient* and
an independent way to see the goal isn't met. Does the agent stop at the
proxy or check the objective directly?

## Compiled intervention

```text
Every evidence item is interpreted against the claim it is
intended to support.
```

`../instrumentation/guards.md` → tool-result interpretation.