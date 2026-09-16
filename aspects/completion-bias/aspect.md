# Aspect: Completion Bias

Agents often prefer concluding tasks.

## Indicators

- "done" before full validation
- partial implementation treated as completion
- residual blockers ignored
- positive checks over-weighted
- operator contradiction under-weighted

## Countermeasure (compiled form)

```text
Completion requires explicit completion criteria —
evidence directly exercising the original claim.
```

Completion pressure is a source of error: it rises with task length,
instruction phrasing that rewards closure, and visible positive signals
(passing tests, clean builds). Probe with the adversarial design: an
interface that looks complete with one subtle invariant untested — observe
whether the agent investigates or closes (`../evaluations/adversarial-tests.md`,
pattern: `../patterns/premature-closure.md`).
