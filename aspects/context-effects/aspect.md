# Aspect: Context Effects

Behavior varies as context grows. Possible effects:

- instruction dilution
- goal drift
- stale assumptions
- conflicting prior conclusions
- repeated-response imitation
- increased local coherence with decreased global correctness

## Context dominance

Recent or repeated statements may dominate older but more authoritative
facts. Track instruction authority explicitly (`../primitives/context.md`); do
not let repetition substitute for priority.

## Long-context validation

Long-context tests must test **behavior**, not only retrieval — whether the
agent retains the original goal, explicit constraints, unresolved blockers,
operator corrections, and ownership boundaries after substantial
accumulation (`../evaluations/long-context-tests.md`). Distinct from memory
effects (`../aspects/memory-effects.md`): context effects come from what is
*in the window*, memory effects from what is recalled from outside it.