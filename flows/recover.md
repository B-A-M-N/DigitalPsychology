# Flow: Recover (behavioral loop exit)

When an agent enters a pathological behavioral loop:

```text
STOP CURRENT STRATEGY
      ↓
reconstruct objective
      ↓
separate observations from assumptions
      ↓
discard stale derived claims
      ↓
re-establish current state
      ↓
choose bounded next action
```

## Examples requiring recovery

repetitive retries · repeated unnecessary rewrites · review chasing ·
escalating architectural complexity · correction argument loops ·
contradictory plans · stale-state continuation.

## Loop detection test

Ask of each iteration:

```text
What new information was gained since the previous iteration?
```

If none — stop and re-model. Continued mutation without new evidence is the
signature of behavioral looping (`../aspects/loop-formation.md`).

## Relation to the StateWorks

The recovery transition mirrors Infrae's and the repository StateWorks'
recovery protocols: **stop mutating, reconstruct facts, then act.** Behavioral
confusion compounds when intervention continues on a stale model — the same
doctrine expressed at the behavioral layer. Recovery here feeds back through
`../flows/reproduce.md` once the loop is exited: the loop itself is evidence.
