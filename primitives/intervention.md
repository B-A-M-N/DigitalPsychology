# Primitive: Intervention

A change intended to alter behavior — compiled, not narrated.

## The hierarchy (weakest → strongest)

```text
Level 1  wording
Level 2  explicit invariant
Level 3  decision rule
Level 4  workflow gate
Level 5  state transition restriction
Level 6  independent verification
Level 7  architecture change
```

(prefers the weakest intervention that reliably changes the behavior.)

## Rules

- **Prefer structural interventions over repeated reminders** for systematic behavior. `"Please remember to validate"` is weaker than `DONE is inaccessible until validation evidence exists`.
- An intervention is an **experiment** testing a mechanism hypothesis — change one thing, pre-register the prediction, run the probe, check for regression and overshoot (`../flows/intervene.md`, `../flows/validate.md`).
- Avoid policy overfitting: a rule created from one failure may harm unrelated tasks (`../patterns/compliance-spiral.md`). Prefer scoped interventions.
- A guard lives in FrameWorks/StateWorks; its *evidence* lives here (`../instrumentation/guards.md`).