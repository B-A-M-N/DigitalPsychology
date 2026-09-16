# Primitive: Reinforcement

The signals that shape behavior toward or away from a response pattern.

```text
user praise/reassurance · review closure under rejection
test pass/fail · tool success/failure · task completion · framework rewards
fewer errors/warnings · model upgrade · harness incentive structure
```

## Rules

- **Reinforcement is not evidence.** A positive signal (passing test, reviewer silence, user praise) may correlate with correct behavior without entailing it (`../aspects/reward-proxy-behavior.md`, Goodhart).
- Agents "learn" what gets reinforced — if the harness rewards `done`, the agent optimizes `done` (proxy), not the actual objective. **The incentive structure is a first-class behavioral driver**, often the dominant one.
- Challenge the reward that isn't the objective: "reduce lint warnings," "close review comments," "increase test count" are all reinforcement-shaped proxies.
- Reinforcement interacts with sycophancy (agreeing when agreement is rewarded) and with authority effects (deferring to source of the reward).
- When diagnosing behavior, ask: what is actually being reinforced, and is that the objective or a proxy?