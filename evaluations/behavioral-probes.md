# Evaluation: Behavioral Probes

The primary instrument for reproducing behavior and testing intervention
success. Tests vary more than wording — they vary task complexity, ambiguity,
context length, tool reliability, operator disagreement, partial success,
contradictory evidence, multiple agents, time pressure, and missing
information.

## Design

For each behavior, define:

```yaml
probe:
  behavior:            # what is being measured
  task:
  independent_variable:   # the trigger held variable
  measurement:            # the observable outcome, pre-registered
  expected_healthy:
  expected_defective:
  confidence_gate:        # how many/trials confirm the tendency
```

## Key probes

- **Contradiction probe** — feed directly conflicting evidence and measure whether the agent revises, defends, or reinterprets (`../aspects/confirmation-persistence.md`).
- **Counterfactual pair** — present the same task with and without a key condition (e.g., tests pass vs tests pass + operator says defect remains) and observe the different responses (`../evaluations/counterfactual-tests.md`).
- **Uncertainty gradient** — increase ambiguity stepwise and watch for false certainty or paralysis (`../aspects/uncertainty-behavior.md`).
- **Completion probe** — a plausible-but-incomplete state; does the agent close or investigate (`../patterns/premature-closure.md`).

## Discipline

- Pre-register expected healthy vs defective so measurement is not hindsight.
- Run enough trials to separate a single response from a reliable tendency (`../flows/reproduce.md`).
- Vary one variable at a time where practical; attribute effect size to agent / environment / task / tool / interaction (`../instrumentation/profiles.md`).
- **Tri-state outcomes.** Every probe result is PASS / FAIL / NOT_APPLICABLE.
  An episode containing no contradiction must never count as a passing
  contradiction trial; no retries must never inflate a retry probe. Only
  APPLICABLE episodes enter numerator and denominator
  (`lib.feedback_loop` `ProbeRunner.validate`).
- **Profile applicable episodes, not event counts.** One evaluator failure on
  a 100-event window is one violating episode — never a 99% effective guard
  (`aggregate-profiles.py` computes rate / sample count /
  confidence interval).