# Aspect: God Object Formation

God Objects emerge behaviorally through **incremental convenience**:

```text
feature A → add to central object
feature B → central object already exists
feature C → easiest place is the central object
feature D → everything now depends on the central object
```

Each local decision looks reasonable; the global result is pathological.
This is an architecture *behavior* — it cannot be called "a mistake" at any
single step, so it is invisible to correctness review per-file.

## Behavioral guard (compiled form)

Before extending an already broad component:

```text
Does this responsibility share state ownership, lifecycle, invariant,
failure behavior, or deployment boundary with the central component?
If not, central placement requires justification.
```

## Measurement

Probe the agent across successive feature additions to one module
(`../patterns/architecture-accretion.md`): does it keep widening the central
object, or does it split along the real responsibility boundary? This is one
of the most objectively measurable architecture behaviors because the
boundary is determinable from the code.
