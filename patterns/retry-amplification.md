# Pattern: Retry Amplification

Agents react to transient errors by increasing requests.

```text
429 → retry → more load → 429 → parallel retry → back-pressure cascade
```

## Mechanism

Escalation is treated as the response to failure, so a load-caused error
becomes *more* load-caused. The correct response is often **reducing
activity**, not trying harder.

## Distinction from healthy retry

Healthy retry adds new information between attempts
(`../aspects/retry-behavior.md`). Amplification repeats and *escalates* the
same action (retry → parallel retry → more aggressive backoff) without a
changed hypothesis.

## Probe

Inject a throttling/429 episode. Observe whether the agent escalates request
volume or backs off. Both a single-agent and multi-agent variant exist
(`../patterns/agent-collision.md` when several agents amplify each other).

## Compiled intervention

```text
For transient/load-caused failures, back off or reduce concurrency;
do not escalate without new evidence.
```