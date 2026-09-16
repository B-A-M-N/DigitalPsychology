# Pattern: Agent Collision

Two or more agents interfere because ownership is ambiguous.

## Signatures

- duplicated work
- conflicting edits
- one agent undoing another's change
- oscillating corrections
- review amplification (each agent's "improvement" destabilizes the others)
- responsibility diffusion (each assumes the other handled it)

## Mechanism

Ambiguous ownership produces behavioral collision
(`../aspects/multi-agent-effects.md`). Without explicit task/scope/owned-state
metadata, each agent acts on the same resource believing it is theirs.

## Variants

- **Retry amplification (multi-agent)**: several agents independently retry
  the same failing dependency, multiplying load.
- **Mutual validation**: agents confirm each other's work with no independent
  evidence — agreement does not add evidence
  (`../aspects/multi-agent-effects.md`).
- **Correction cascade**: A corrects B, B corrects A, both re-apply opposite
  fixes in a loop.

## Compiled intervention

```text
Assign explicit ownership before multi-agent work; each agent names its
task, scope, owned state, mutable resources, handoff target, and
completion criteria.
```

Measure via multi-agent tests (`../evaluations/multi-agent-tests.md`) — rate
collision frequency per ownership configuration.