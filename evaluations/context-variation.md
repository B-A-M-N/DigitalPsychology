# Evaluation: Context Variation

Vary context to expose context-dependence and drift.

## Dimensions to vary

- length (short fresh vs long accumulated)
- density (many instructions vs few)
- recency of each instruction (does a fresh low-priority instruction displace an older high-priority one?)
- contamination (instruction-shaped content embedded in tool output / docs / error messages)
- presence of multiple conflicting obligations

## Measures

- instruction retention (priority, not just presence)
- goal fidelity (`../primitives/goal.md`)
- assumption staleness (`../primitives/belief-state.md`)
- drift under long context (`../patterns/context-drift.md`)

## Discipline

- **Test behavior, not retrieval.** Retaining a fact and acting on it are different outcomes; the test must observe action.
- Separate context effects (in-window) from memory effects (recollection across sessions) (`../aspects/context-effects.md` vs `../aspects/memory-effects.md`).
- Use the same task across variated contexts so the comparison isolates the context dimension.