# Pattern: Architecture Accretion

```text
existing central module + new requirement + local convenience
+ no responsibility review = architectural accretion
```

## Why it's silent

Each addition is locally rational — "the central object already exists," "the
easiest place is here," "adding to it is less invasive." The behavioral
defect is that no step reviews the responsibility boundary
(`../aspects/god-object-formation.md`).

## The increment problem

```text
feature A → add to central object
feature B → central object already exists
feature C → easiest place is central object
feature D → everything depends on central object
```

No single commit is a mistake; the aggregate is pathological. This is why it
eludes per-file correctness review and shows up only as systemic
architecture drift.

## Compiled intervention

```text
Before extending an already broad component, review whether the new
responsibility shares state, lifecycle, invariant, failure behavior,
or deployment boundary with the existing central component. If not,
separate.
```

Intervention should occur **before** the God Object grows
(`../flows/intervene.md`), and measurable after the fact by observing
successive feature additions to a single module.