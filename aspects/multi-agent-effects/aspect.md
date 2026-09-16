# Aspect: Multi-Agent Effects

Multiple agents introduce behavioral phenomena unavailable in one-agent
systems:

- duplicated work · conflicting edits · responsibility diffusion
- mutual validation · stale handoffs · consensus without evidence
- oscillating corrections · one agent undoing another · review amplification

## Group behaviors worth studying (larger phenomena)

```text
groupthink · mutual overconfidence · duplicated investigation
authority emergence · responsibility diffusion · circular validation
correction cascades · review amplification · task fixation · role drift
adversarial interaction · cooperative specialization
```

## Authority & ownership

Every agent should know: `task · scope · owned state · mutable resources ·
handoff target · completion criteria.` Ambiguous ownership produces
behavioral collision.

## Measurement

The decisive failure is mutual validation:

```text
Agent A says implementation is correct.
Agent B reads A's report.
Agent B concludes implementation is correct.
```

**No independent evidence has been added** — agreement among agents sharing
the same evidence is not independent verification. Also interaction-induced
defects: Agent A alone doesn't over-expand and Agent B alone doesn't, but B
reviewing A repeatedly triggers a scope-expansion loop
(`../evaluations/multi-agent-tests.md`). These are the behaviors only
studyable at the group level.