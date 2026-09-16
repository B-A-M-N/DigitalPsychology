# Flow: Observe (UNKNOWN → OBSERVED)

Observe behavior before prescribing changes.

## Collect

```text
task · instructions · context · agent output · tool actions · tool results
· operator correction · agent response · subsequent behavior
```

**Do not immediately rewrite prompts based on one isolated example** unless
the behavior is severe.

## Sources

- Behavioral telemetry, when instrumentation exists
  (`../instrumentation/event-schema.md`) — the reliable path.
- Transcripts and tool logs, when it does not — weaker; reconstruct events
  carefully and mark confidence.
- Live observation of an ongoing session.

## Distinguish during observation

- what the agent was told from what it actually did
- what the agent did from what it claims it did
- a single occurrence from a recurring one

An observation is not yet a characterization; record trigger conditions as
they appear, not retrofitted.
