# Aspect: Correction Response

Healthy correction behavior:

```text
receive contradiction → identify invalidated assumption
→ reinspect evidence → update model → continue
```

Unhealthy:

```text
receive contradiction → defend previous answer
→ explain why the previous answer should be correct
```

## Measurement

- `first_contradiction_update_rate` — fraction of contradictions that produce model revision on first presentation
- `defensive_persistence_rate` — fraction answered with defense rather than reinspection

The interesting probe is response *sequence*: an agent that defends once and
then revises differs behaviorally from one that requires repeated correction
(`../evaluations/persistence-tests.md`). Correction response is also where
sycophancy shows its inverse — over-correction, adopting the operator's
theory wholesale without evaluation (`../aspects/sycophancy.md`).
