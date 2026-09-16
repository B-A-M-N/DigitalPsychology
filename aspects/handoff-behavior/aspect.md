# Aspect: Handoff Behavior

A good handoff distinguishes:

```text
FACTS     observed directly
CHANGES   actions taken
EVIDENCE  validation performed
UNKNOWN   not established
NEXT      bounded remaining action
```

A bad handoff:

```text
"Everything looks good, should be ready."
```

This **exports confidence without state** — it communicates optimism where
the next agent needed a state model. The next agent inherits the handoff as
its evidence base, so a vague handoff propagates the current agent's
unexamined confidence downstream (`../aspects/multi-agent-effects.md`).

Measurement: rate handoffs by how much of the five-part distinction survives.
Instrument via the handoff event (`../instrumentation/event-schema.md`) — a
handoff that omits UNKNOWN is untrustworthy. A good handoff also names what
the next agent must check first, separating "I verified" from "I did not."