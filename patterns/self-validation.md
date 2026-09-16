# Pattern: Self-Validation

```text
Agent: "I implemented the fix."
Later: "Because the fix was implemented, the issue is resolved."
```

**Circular.** Agent-generated actions cannot serve as independent validation
of those actions. The agent is treating its own output as evidence for itself.

## Why it feels resolved

The implementation *and* the confirmation come from the same source, so the
loop closes without ever touching ground truth. This is the root of
`../patterns/premature-closure.md` in its completion-flavored form.

## Probe

Give the agent a plausible implementation plus an independent path to verify
the actual outcome (rerun the user's observed behavior). Does it take the
independent path, or does it confirm its own work?

## Compiled intervention

```text
An agent's action may not be used as independent evidence
for that same action's correctness.
```

Feed the independent-verification layer
(`../instrumentation/guards.md` → Level 6 independent verification).