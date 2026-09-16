# Primitive: Belief State

The set of claims, hypotheses, and assumptions the agent is currently acting on.

```text
conclusion_1: X is fixed
hypothesis_2: the root cause is the proxy chain
assumption_3: the port is open
```

## Rules

- A belief state is **mutable external evidence** that can be invalidated. Track which claims are supported, which are contradicted, and which are stale (`../patterns/stale-model-defense.md`).
- Associate each belief with: what supports it, what would invalidate it, and when it was formed (recency interacts with context priority).
- When a belief is invalidated, the agent must not legally remain confident (`../aspects/confirmation-persistence.md`); the transition is `CONFIDENT ↛ CONFIRMED` while support is gone.
- Belief states decay under long context and are *distinct from goals*: a goal says what must be true; a belief state says what the agent currently thinks is true.