# Instrumentation: Behavioral Event Schema

Layer 1 of the runtime relationship — **always on, tiny, no analysis**.
Instrumentation does not need Digital Psychology loaded into the agent; the
runtime needs to emit enough structured behavioral data that analysis can
reconstruct what happened later.

## Design rule

Do **not** have every model narrate its internals ("I am experiencing
moderate uncertainty…"). Instrument the **externally meaningful cognitive
events**, and capture automatically what the runtime already knows — tool
invocation, tool result, retry, handoff, state transition, blocker, operator
contradiction, completion claim, validation result. Only ask the model to
explicitly emit events that cannot otherwise be inferred, such as a
load-bearing assumption or a decision boundary.

## The schema is a JSON Schema, not prose

The canonical schema is versioned and machine-checkable:

```text
schemas/behavior-event.schema.json  (v2.1.0)
```

Every event is a JSON object with a **broad category** and an **exact
event_type** — never one flat enum mixing both levels:

```yaml
category: completion
event_type: completion_claim
```

The category enum is:

```text
observation · assumption · inference · decision · action · tool_result
validation · correction · contradiction · retry · state_transition
handoff · completion · blocker
```

`event_type` names the exact event within the category (e.g.
`operator_contradiction` under `contradiction`, `completion_claim` under
`completion`). The old prose vocabulary named events inconsistently — `BLOCKER`
disappearing, `STATE_CHANGE` becoming `state_transition`,
`COMPLETION_CLAIM` becoming `completion` — which is exactly why this file is
now a generated-bound reference to the schema instead of a competing
enumeration.

## Envelope

```yaml
schema_version: 1.0.0
event_id:
timestamp:
task_id:
agent_id:
parent_event:
category:        # broad category enum, above
event_type:      # exact event within the category
subject:
input_state:
output_state:
evidence_refs:   # completion claims REQUIRE these
uncertainty:     # 0..1, optional
authority_source:
scope:
result:
supersedes:
invalidates:     # contradictions REQUIRE this
```

Not every event carries every field — but completion claims carry
`evidence_refs`, and contradictions carry `invalidates`. These rules are
enforced by the schema's conditional constraints, not by prose good intentions.

## Worked examples (all validate against the schema)

Every example lives as a fixture under `fixtures/events.ndjson` and validates
against `schemas/behavior-event.schema.json`. Run:

```bash
python3 scripts/validate-events.py
```

or validate by hand:

```bash
python3 - <<'PY'
import json, jsonschema
schema = json.load(open('schemas/behavior-event.schema.json'))
for line in open('fixtures/events.ndjson'):
    jsonschema.validate(json.loads(line), schema)
PY
```

Agent does `tests → 206 passed → "Fixed."` — the runtime records:

```json
{"category": "tool_result", "event_type": "test_suite_result", "result": "206 passed"}
{"category": "completion", "event_type": "completion_claim",
 "subject": "issue_resolved", "evidence_refs": ["evt-1"]}
```

Later — operator contradiction:

```json
{"category": "contradiction", "event_type": "operator_contradiction",
 "subject": "trusted proxy issue", "invalidates": "evt-2"}
```

And the response:

```json
{"category": "decision", "event_type": "agent_response_to_contradiction",
 "result": "reinspect"}
```

## What this enables

Queries like:

- How often does Agent X declare completion after a passing test but before direct validation?
- How does Agent X respond when the operator contradicts a prior high-confidence claim?
- Does long-context operation increase scope expansion?
- Which agents accrete central abstractions after feature 3+ lands in an existing module?

## Episodes

Individual events group into **behavioral episodes** (see
`flows/characterize.md` and `primitives/episode`):

```text
Episode: PR review repair
1. reviewer identifies race
2. agent patches exact location
3. tests pass
4. agent declares resolved
5. reviewer finds same invariant elsewhere
6. agent patches second location
7. tests pass
8. agent declares resolved
9. reviewer finds third manifestation
```

Classification: `pattern: local-comment chasing` ·
`characteristic: failure to generalize reviewer concern into system invariant`.

Episodes — not single responses — are the unit for profiles.
