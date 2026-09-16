# Pattern: Review Chasing

```text
review finding → patch exact wording → new review finding
→ patch again → scope continuously expands
```

## Characteristics

The agent optimizes for **reviewer-comment closure** rather than for the
underlying invariant. Each patch is a local response to the latest comment;
the invariant that spans all of them is never solved once.

## Distinction

Fixing reviewer findings to satisfy the invariant is correct
(`../patterns/checklist-theater.md` contrast). Review chasing fixes the
*surface* of each comment and re-creates the same defect at the next
location, or expands scope indefinitely while chasing comment-by-comment
(`../flows/recover.md` treats it as a loop).

## Probe

A reviewer (human or automated) reports the same invariant failing in three
places. Does the agent fix all three against one invariant, or chase them one
comment at a time?

## Compiled intervention

```text
Identify the invariant once, solve the invariant, validate it,
then freeze.
```

`../instrumentation/guards.md` → scope freeze.