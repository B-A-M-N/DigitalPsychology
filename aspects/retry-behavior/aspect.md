# Aspect: Retry Behavior

Healthy retry:

```text
failure → new evidence → changed hypothesis → changed action
```

Unhealthy:

```text
failure → same action → same failure → same action harder
```

**Repeated execution without a model change is behavioral looping.**

## Measurement

Track retries per (task, tool, failure): does the action, the hypothesis, or
the strategy change between attempts, or only the intensity? An unchanged
retry rate that stays high across contexts is the signature of
perseveration (`../patterns/retry-amplification.md`). The correct response to
transient errors is sometimes *less* activity — reframing a 429 storm as
reduction, not escalation. Distinguish retry from legitimate repetition that
gathers new information each pass.