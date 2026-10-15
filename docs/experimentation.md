# Experimentation

Trajectories, comparison methodology, intervention classes, and profile
promotion for DigitalPsychology.

## Standalone quick start detail

DP can ingest, reconstruct, experiment, and promote a routing profile without CFW
or CSW installed.

```bash
python3 -m pip install -e ./cognitive_runtime_core
python3 -m pip install -e .
digital-psychology-admin-mcp
```

The command runs as a child process of an operator MCP client and waits for
protocol messages on stdin. Keep stdout reserved for MCP traffic. It exposes DP's
telemetry, advice, profile-validation, and slow-loop administration tools.
Register it only in a trusted operator namespace, never in the controlled agent's
model-visible tool list. The old `digital-psychology-mcp` command fails closed
unless `DIGITALPSYCHOLOGY_ENABLE_LEGACY_MCP=1` is explicitly set. The private
`digital-psychology-control-mcp` process is launched by `HostControlBridge` over
a separate host-only connection.

The service stores ordinary state under `DIGITALPSYCHOLOGY_STATE_ROOT` (or the
platform state directory). Ordinary, non-privileged events can be ingested
without an authentication secret for local fixtures. In a deployment where agents
can reach the MCP surface, configure `DIGITALPSYCHOLOGY_INGESTION_SECRET`;
privileged categories (`validation`, `state_transition`, `completion`) and
operator/repository-owner actors require host attestation.

Run the independent experiment example:

```bash
python3 scripts/quickstart-standalone.py
```

Expected output:

```text
independent trajectories: 30
candidates: 1
receipt decision: pass
promoted profiles: 1
rejections: []
```

The fixture uses independent control, treatment, and holdout trajectories. DP
derives one candidate profile, builds a host-bound routing receipt, and promotes
only after the receipt passes. Repeated events from one trajectory do not count
as independent samples.

The slow loop is explicitly invoked; it is not an automatic background worker. In
production, the MCP tool accepts only a protected `experiment_id` plus a host
authorization, and the service loads event IDs from its authenticated SQLite
store. The direct library API remains
`BoundedLearningController.run(events, task_context)` for trusted host code; raw
`events` and `task_context` are not accepted by the MCP boundary.

## Behavioral telemetry

DP should not need to live inside the active model context. The host runtime
emits small structured events describing what it already knows:

* decisions, observations, tool requests and tool results;
* contradictions, retries, delegations and corrections;
* state transitions, completion claims and evidence relationships;
* routing selections and routing suppressions.

### Behavioral identity is hierarchical

```text
agent instance
    └── session
         └── task
              └── attempt
                   └── segment
                        └── event
```

Multi-agent work may additionally include interaction, parent-session, delegator,
delegate, delegation, and role identities. This prevents unrelated sessions from
being collapsed into one behavioral history.

## From events to behavior

A single event rarely establishes a behavioral pattern. DP reconstructs behavior
over progressively larger temporal units:

```text
Event → Episode → TaskTrajectory → SessionTrajectory
                                      │
                                      ├── interaction analysis
                                      ▼
                         context-stratified profile
                                      │
                                      ▼
                                  Experiment
                                      │
                                      ▼
                              validated finding
```

For example, a completion claim followed by an operator contradiction, defense of
the prior conclusion, fresh tool use, and a revised conclusion is more meaningful
than counting those events independently.

### Independent sessions are the experimental unit

Cross-session learning must not treat multiple log events as multiple independent
observations. A promotion-grade trial is closer to:

```text
agent instance + session + task + attempt + behavioral subject
```

Events are reconstructed inside that trajectory first. Only then are independent
trajectories compared statistically.

## Three classes of intervention

### Structural intervention

Change what the runtime permits:

```text
unsafe action → host gate blocks action
```

A deterministic fake-agent harness is useful here because the test asks whether
the structural control works independently of model reasoning.

### Cognitive or prompt intervention

Change what the model sees or how it is instructed to reason:

```text
explicit contradiction framing → lower stale-hypothesis persistence
```

This requires real-model behavioral evaluation.

### Routing or composition intervention

Change which optional behavioral/workflow components are activated:

```text
specific model + harness + task family
→ unnecessary specialist repeatedly increases work
→ controlled suppression experiment
→ same success, lower correction/tool/context burden
```

This also requires real-agent qualification. A structural test cannot prove that
a cognitive or routing intervention improved model behavior.

## Deployable output

Validated findings can become two different runtime products.

### Behavioral guards

```text
observed recurring failure
        ↓
candidate → experiment → validated → canary → active guard
```

Example:

```text
unresolved contradictory evidence
→ completion remains illegal
```

### Behavioral routing profiles

A routing profile changes which **already legal** options should be preferred or
suppressed for a particular context. It does not create authority, make an
illegal StateWork transition legal, or remove mandatory safety policy.

```text
agent/model/harness/task-family
        + repeated unnecessary FLOW activation
        + controlled evidence showing no outcome benefit
        ↓
validated routing profile
        ↓
future matching tasks suppress optional FLOW activation
```

The goal is not endlessly accumulating rules. It is better behavior with the
smallest effective active control surface.

## Learning should be subtractive too

Validated evidence may justify suppressing an unnecessary flow or optional
specialist, avoiding ineffective delegation, reducing retry amplification,
removing a stale guard, retiring a superseded intervention, or preferring a
simpler legal composition.

## Real-agent behavioral experiments

A behavioral claim requires observing an actual agent performing an actual task.
A useful experiment preserves:

* the same task goal and fixture/environment;
* comparable model, tool access, and host conditions;
* blinded cohort identity;
* explicit control and treatment policies;
* independent sessions and objective outcome evaluators.

Possible measurements include task success, correction cycles, redundant tool
calls, unnecessary routing, premature completion, hypothesis revision after
contradiction, retry amplification, context consumption, latency, token use,
state-transition errors, and delegation failures.

An intervention should not be declared successful merely because one preferred
metric improved. Task outcome and safety/regression metrics must remain
acceptable.

## Multi-agent behavior

Multiple agents agreeing with one another is not necessarily independent
evidence. They may share the same premise, source context, delegator, or failure
mode.

DP therefore records interaction and delegation provenance and distinguishes
individual-agent tendency, inherited premise, delegator effect, delegate effect,
interaction effect, topology effect, circular validation, role drift, correction
cascade, and duplicated investigation.

## Fast loop versus slow loop

DigitalPsychology owns both the bounded fast controller and the evidence driven
slow learner.

```text
During a task:  host event
                → CFW and CSW provide bounded state
                → DP selects optional behavior changes
                → host applies only legal changes
                → host evidence scores each intervention

Across tasks:   DP → reconstruct → compare → experiment
                  → validate → affect future composition
```

Fast-loop changes carry bounded leases and independent outcomes. Slow-loop policy
promotion requires matched, independent trajectories; one task cannot establish a
durable behavioral prior.

## Controlled knowledge sharing

Raw observations remain origin-scoped evidence. They do not become another
agent's instructions merely by being stored.

Routing profiles declare `agent_private`, `application_shared`, or
`cross_application` scope. Wider scope requires a candidate-bound,
hash-authenticated promotion decision; cross-application sharing additionally
requires explicit data-sharing authorization. CFW adoption remains a separate
per-task decision tied to the exact profile hash.

Provenance identity and experimental comparison context are separate. Exact
session, task, attempt, interaction, and delegation IDs identify causal evidence,
while model, harness, role, task family, environment, and effective policy
determine whether trials are comparable. Shared interaction evidence is rejected
when it crosses experimental cohorts, and all treatment failures inherited from
one shared premise cannot create an individual corrective profile.
