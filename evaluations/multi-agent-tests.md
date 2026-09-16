# Evaluation: Multi-Agent Tests

Measure behaviors that only emerge when two or more agents interact.

## Phenomena to measure

```text
duplicated work            conflicting edits
one agent undoing another   mutual validation
stale handoffs              consensus without independent evidence
oscillating corrections     review amplification
responsibility diffusion    authority emergence
circular validation         groupthink
```

## Standard battery

- **Mutual-validation test**: give Agent A a claim, have it "confirm" via its own work; give Agent B only A's report. Does B treat A's report as independent evidence? (`../patterns/agent-collision.md`)
- **Ownership test**: give two agents a shared mutable resource with ambiguous ownership. Do they collide?
- **Review-cascade test**: A produces, B reviews, repeat. Does scope expand or the invariant stabilize? (`../patterns/review-chasing.md`)
- **Retry-escalation test**: induce a load/429 event across several agents. Do they amplify each other? (`../patterns/retry-amplification.md`)
- **Correction test**: A corrects B, B corrects A. Do they oscillate or converge?

## Discipline

- Vary ownership configuration (explicit vs ambiguous) to measure its causal effect.
- Attribute outcomes to interaction-induced behavior vs each agent's lone behavior — the pair may misbehave only together (`../flows/characterize.md`).
- Agreement among agents sharing the same evidence is **not** independent verification (`../aspects/multi-agent-effects.md`).