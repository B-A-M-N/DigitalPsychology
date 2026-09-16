# Primitive: Recovery

Returning an agent from a pathological behavioral loop or stale state to a
functioning state.

## The recovery sequence

```text
STOP CURRENT STRATEGY
→ reconstruct objective
→ separate observations from assumptions
→ discard stale derived claims
→ re-establish current state
→ choose a bounded next action
```

(`../flows/recover.md`)

## Rules

- Recovery is a **behavioral** intervention: it interrupts loops (retry, review-chasing, correction-argument, escalation) that no evidence disambiguates.
- The loop-detection test: `what new information was gained since the previous iteration?` If none → stop and re-model (`../aspects/loop-formation.md`).
- Recovery differs from validation: validation prevents the bad state; recovery exits it. Both are first-class (contrast with `../flows/validate.md`).
- The compiled guard: `if an iteration adds no new evidence, the agent must stop and re-model rather than escalate.`
- Healthy recovery does not re-enter the loop — it changes the strategy or the model, not just the attempt count.