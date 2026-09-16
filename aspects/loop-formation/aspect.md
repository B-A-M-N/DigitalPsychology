# Aspect: Loop Formation

## Loop signatures

```text
same command repeatedly · same explanation repeatedly ·
repeated small patches · repeated review/fix cycles ·
continued mutation without new evidence
```

## Detection test

```text
What new information was gained since the previous iteration?
```

If none:

> Stop and re-model.

## Compilation

This is the loop-detection rule that feeds recovery (`../flows/recover.md`)
and the completeness guard:

```text
If an iteration adds no new evidence, the agent must stop and re-model
rather than escalate.
```

Loops are usually not single-agent failures in isolation — review-chasing,
retry-amplification, and correction-argument loops each have distinct triggers
and escalations. Probe whether the loop tightens under requirement, pressure,
or repeated external prompts, and whether any of those inputs *releases* it.