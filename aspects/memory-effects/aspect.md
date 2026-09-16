# Aspect: Memory Effects

Persisted memory can improve continuity — and produce:

- stale assumptions
- incorrect personalization
- inappropriate transfer between tasks
- overconfidence from old state

**Memory must be treated as potentially stale evidence.** Ask when reading
affects a decision:

```text
Was this recollection derived from stale state?
Does it still match the current task and current evidence?
```

The behavior to measure: does the agent *verify* remembered facts against
current state when they matter, or act on them as if current? A
stale-model-freeze is the memory analog of stale-model-defence
(`../patterns/stale-model-defense.md`). Memory also interacts with
adaptation — an agent that over-personalizes one user's preference into
every task has made the generalization error of treating episodic history as
a rule.