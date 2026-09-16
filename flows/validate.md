# Flow: Validate (INTERVENED → VALIDATED)

Validation requires **rerunning behavioral probes** — not rereading the new
instructions.

## Questions

```text
Did the target behavior improve?
Did it improve only on the original example?
Did another failure replace it?
Did the intervention create excessive rigidity?
Did the agent become slower or less useful?
Does behavior hold under context variation?
Does behavior hold after long interaction?
```

## Overshoot check

Interventions can overshoot. `Rule: never assume.` → `Agent refuses all reasonable inference.` Behavioral correction must preserve usefulness — the goal is calibrated behavior, not paralysis.

## Policy overfitting check

A rule created from one failure may harm unrelated tasks. Ask:

```text
Was the failure domain-specific?
Was the trigger general?
Would this rule block legitimate behavior elsewhere?
```

Prefer scoped interventions. A completion guard is a completion guard — it
should not also make the agent unable to answer simple questions.

## Regression check

Whack-a-mole is a finding: if suppressing behavior A produces behavior B
(suppressing premature closure produces excessive deferral), that is evidence
about the underlying mechanism and must be recorded. The intervention is not
VALIDATED until the probe set (`../evaluations/`) passes and no replacement
defect has appeared.
