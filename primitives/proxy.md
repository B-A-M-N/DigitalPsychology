# Primitive: Proxy

A simpler measurable condition the agent may substitute for the goal.

```text
tests passed          instead of   correctness issue actually resolved
code was changed      instead of   user-observed defect disappeared
no tool errors        instead of   task completed
fewer warnings        instead of   design is sound
```

## Rules

- A proxy is a *correlated observable*, not a guarantee. This is the central relation: proxy ≠ goal.
- **Proxy substitution** is the canonical Digital Psychology defect — the agent completes the proxy and reports the goal achieved (`../patterns/proxy-completion.md`).
- A proxy is useful until the agent *forgets it is a proxy*. Tracking the mapping explicitly (proxy → which goal's observable it approximates) is the guard's job.
- Goodhart applies: when a metric becomes the target, it stops representing the objective (`../aspects/reward-proxy-behavior.md`).
- Every claimed completion should name the actual evidence plus the proxy that was used — and the gap between them.