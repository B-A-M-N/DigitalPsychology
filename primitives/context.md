# Primitive: Context

Everything in the agent's effective working window that can steer behavior.

```text
instructions · command history · tool output · prior agent output
operator messages · retrieved content · state snapshots
```

## Rules

- Distinguish observable context (what is *in the window*) from permanent knowledge/recollection (`../aspects/memory-effects.md`).
- Context is not uniformly weighted: recent/repeated statements may dominate older but higher-priority facts (`../aspects/context-effects.md`). Track instruction authority explicitly; do not let repetition substitute for priority.
- Track provenance — instruction-shaped content can arrive embedded in tool output, docs, or code (`../aspects/adversarial-instruction-effects.md`).
- Context *density* and *length* are themselves behavioral triggers; context is not just the backdrop but a driver of drift over long sessions.