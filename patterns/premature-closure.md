# Pattern: Premature Closure

```text
agent takes action → positive local signal → agent closes hypothesis space
→ contradictory evidence ignored
```

## Characteristics

The agent stops investigating once a plausible explanation matches a partial
signal. The positive signal may be a passing test, a clean compile, or the
absence of an immediate error — none of which establish closure of the
original claim.

## Observable signatures

- "done" before full validation
- partial implementation treated as complete
- residual blockers ignored
- operator contradiction under-weighted

## Probe

Adversarial behavioral test: an interface that *looks* complete with most
tests passing and one subtle invariant untested — observe whether the agent
investigates or closes (`../evaluations/adversarial-tests.md`).

## Compiled intervention

```text
Completion requires direct evidence exercising the original claim.
```

A guard, not a reminder (`../flows/intervene.md`).