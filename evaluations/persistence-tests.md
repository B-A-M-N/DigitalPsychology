# Evaluation: Persistence Tests

Determine whether a behavioral correction **generalizes**.

## Structure

After correcting a behavior once via some intervention:

- re-introduce a later task with the **same trigger** (does the correction stick?)
- then a task with a **related trigger** (does the correction transfer?)
- then the **original failing case** (does the fix regress?)

## Measures

- retention of the correction across trials (first_contradiction_update_rate, direct_validation_rate as examples)
- whether the correction is **rule-like** (applies generally) or **episodic** (only reproduced on the exact original example)
- whether a new failure replaced the corrected one (whack-a-mole = the mechanism was wrong, `../flows/validate.md`)

## Discipline

> Behavior that disappears only on the original test case has not been fixed.

Persistence is the distinction between a deployed guard and a one-off patch.
Re-run persistence after any model/tool/framework/harness change
(`../flows/monitor.md`).