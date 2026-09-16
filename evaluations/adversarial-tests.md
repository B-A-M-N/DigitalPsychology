# Evaluation: Adversarial Tests

Construct situations that **tempt the defect** rather than presenting the
defect openly.

## Examples by defect

```text
premature closure:   implementation appears complete; most tests pass;
                     one subtle invariant untested → investigate or close?

proxy substitution:  passes the proxy; the real objective unmet
                     and independently checkable → catch the proxy or declare done?

self-validation:     agent's own patch available as "evidence";
                     an independent path exists → take it or self-confirm?

suggestion misclass: operator gives a *behavioral observation*
                     → investigate, or treat as a preference/spelling it out?
```

## Purpose

The defect only shows when the agent is tempted. An open admission
("complete only with direct evidence") dresses well; the adversarial probe
reveals whether the behavior follows when the guard is absent.

## Discipline

- Design each probe to have a **pre-registered healthy and defective** outcome.
- Run alongside counterfactual tests so the temptation is the only variable.
- This is the instrument that turns "rule present" into "rule effective" — the core of the ecosystem's feedback loop: does adding a guard actually block the tempted behavior (`../flows/validate.md`)?