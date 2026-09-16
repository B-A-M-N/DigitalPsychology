# Pattern: Context Drift

Behavior degrades as context accumulates:

```text
instruction dilution · goal drift · stale assumptions
conflicting prior conclusions · repeated-response imitation
increased local coherence with decreased global correctness
```

## Distinction from memory effects

Context drift comes from what is *in the window* (long conversations,
compiled histories); memory effects come from what is recalled from *outside*
it (`../aspects/context-effects.md` vs `../aspects/memory-effects.md`).

## Key mechanism

Recent or repeated statements dominate older but higher-priority facts. The
*salience* of a recent instruction exceeds its *authority*.

## Compiled intervention

Track instruction authority explicitly; do not let repetition substitute for
priority. The agent should be able to say *which* constraint is the binding
one even when a later, more prominent message is in the window.

## Probe

Long-context behavioral test: evaluate whether the agent retains the original
goal, explicit constraints, unresolved blockers, operator corrections, and
ownership boundaries after substantial accumulation
(`../evaluations/long-context-tests.md`).