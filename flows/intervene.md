# Flow: Intervene (HYPOTHESIZED → INTERVENED)

**Interventions are experiments.** The purpose is to test the mechanism
hypothesis: if the hypothesis is right, the intervention should change the
behavior in the predicted way.

## Intervention hierarchy — weakest that works

```text
Level 1  wording
Level 2  explicit invariant
Level 3  decision rule
Level 4  workflow gate
Level 5  state transition restriction
Level 6  independent verification
Level 7  architecture change
```

The runtime equivalent ladder:

```text
reminder → explicit instruction → decision rule → workflow requirement
→ state guard → tool restriction → independent verifier → architectural separation
```

Weak: `"Please remember to validate."`
Stronger: `DONE transition is inaccessible until validation evidence exists.`

**Prefer the weakest intervention that reliably changes the behavior.** Do
not solve every behavioral issue by adding paragraphs of instructions — and
do not respond to a systematic defect by loading more material into every
agent; compile the finding into the smallest rule or guard that kills the
behavior (see `../instrumentation/guards.md`).

## Experimental discipline

- Change one thing at a time where practical; note confounds when not.
- Pre-register the prediction: what behavior change, under which probe, counts as support?
- Decide the failure criterion in advance: what result would falsify the hypothesis?
- Structural interventions (guards) over repeated reminders when the behavior is systematic.

## Guard vs advice

Advice: "Try not to declare completion too early."
Guard: `DONE requires: blockers = 0 · relevant invariant tested · contradictory observations resolved.`

Systematic defects need guards — but the guard is exported to the runtime;
the *evidence that it worked* is recorded here.
