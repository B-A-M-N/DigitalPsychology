# Pattern: Compliance Spiral

An escalating pattern where intervention overshoots calibration and turns
into rigidity — the behavioral-corrective version of over-correction.

```text
over-deference → rule: "never assume" → agent refuses all reasonable inference
                 → false-certainty avoidance becomes refusal to act
```

## Signature

A correction intended to reduce one defect (sycophancy, premature closure,
false certainty) is compiled so hard it produces the *opposite* defect
(deference as paralysis, closure-refusal as useful-action-refusal).
Behavioral correction should preserve usefulness — calibrated behavior, not
paralysis.

## Relationship to the two-defect families

This pattern is the failure of the guard/advice distinction:
`../flows/validate.md` overshoot check. Where `over-engineering` and
`under-engineering` are the two ends of structure, compliance spiral is the
two ends of *correction*: either it does not bind, or it binds too hard.

## Compiled intervention

Prefer scoped to universal rules; test a guard for *legitimate-behavior*
blockage before shipping it (`../flows/validate.md` overfitting check).
A completion guard must not make the agent unable to answer simple questions.