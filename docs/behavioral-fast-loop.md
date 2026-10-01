# Behavioral fast-loop contract

DigitalPsychology (DP) owns the runtime host and the meaning and transition
policy of `BehaviorState`. `RuntimeHost` opens isolated `RuntimeSession`
instances; each session owns the behavioral event loop, DP state persistence,
revision checks, context projection, and coordination through explicit CFW and
CSW ports.

## Inputs and authority

For each host action, `RuntimeSession.process_action()` gathers:

- the current DP `BehaviorState` and its revision;
- bounded CFW signals, such as stale assumptions, equivalent retries, or
  support work displacing unfinished criteria;
- a bounded CSW snapshot, including the current active workflows and only
  StateWork choices CSW has declared eligible;
- objective criteria, recent host-observed outcomes, evidence references, and
  context pressure;
- an optional DP System-1 judgment. Laya is one optional provider; deterministic
  policy works when System-1 is disabled or unavailable.

DP returns a typed `BehavioralDecision` with a state at exactly the next DP
revision, directives, CSW-eligible StateWork preferences, and independently
assessed intervention outcomes. DP persists its behavior revision separately
from the CSW selection and workflow revisions.

The runtime's decision, directive application, and outcome assessment methods
are trusted-host APIs. They are not registered as agent-facing MCP tools. DP
persists its decision with compare-and-swap and keeps WARD mandatory. CFW
validates and applies component directives; CSW alone owns workflow selection,
legal steps, evidence validation, and workflow revisions. DP cannot mint
evidence, grant permissions, make an illegal CSW transition legal, or declare
a task complete.

StateWork preferences are ranked only from `CSWBehaviorSnapshot.eligible_stateworks`.
`CSWRuntimeAdapter` submits activation through CSW's revisioned selection API
and then refreshes its snapshot. A rejected or stale request cannot be copied
into DP's next view as active state.

## Intervention lifecycle

Every active intervention has:

- a stable intervention ID and cause;
- an expected effect;
- its starting DP revision and runtime scope;
- the settings it changed, with their previous values;
- an action-count expiry.

When the cause clears, DP emits directives that restore the prior settings and
closes the intervention as `withdrawn`; that is a lifecycle event, not a
success claim. Each intervention also records its target criteria, baseline
criterion status, host action index, concurrent interventions, and bounded
evaluation horizon. If that horizon expires without an independent assessment,
DP records `expired` with insufficient attribution, releases temporary
settings, and blocks automatic repetition for that cause.

DP does not infer intervention success from `progress_made`, a changed file,
an activated workflow, or the agent's claim. A host must obtain an applied
intervention receipt and ask the independent evaluator to resolve post-change
evidence through trusted CSW/validator authorities. See
[`intervention-outcome-credit.md`](intervention-outcome-credit.md).

## Python use

```python
from digital_psychology import CFWRuntimeAdapter, RuntimeHost
from digital_psychology.runtime.adapters import CSWRuntimeAdapter
from digital_psychology.mcp_server import DigitalPsychologyService

service = DigitalPsychologyService(event_db="/var/lib/dp/events.sqlite3")
host = RuntimeHost(service=service, state_path="/var/lib/dp/behavior-state.sqlite3")
session = host.open_session(
    identity=runtime_identity,
    objective="Render the requested page",
    objective_criteria=("criterion-visual",),
    cfw=CFWRuntimeAdapter(cfw_task.digital_psychology_adapter()),
    csw=CSWRuntimeAdapter(csw_authority, identity=runtime_identity,
                           bindings=csw_workflow_bindings),
    outcome_evidence_authority=host_evidence_authority,
)
turn = session.process_action(host_observation)
model_context = session.before_model_call(tool_capable=True)
```

`host_observation` and outcome evidence references are produced by the trusted
host. An agent cannot submit its own intervention receipt or success judgment.
The public MCP server remains the transport surface; host-only session APIs
stay in the DP runtime package.

The wire formats are versioned in
`digital_psychology/schemas/behavior-state-v1.schema.json` and
`digital_psychology/schemas/behavioral-decision-v1.schema.json`.

## Integration boundaries

DP owns cross-system behavioral orchestration. CFW retains its own task and
action enforcement, but exposes a typed DP component adapter for bounded
signals, reversible behavior directives, and context projection. CSW retains
the only authority over active StateWorks and their transitions. These ports
compose through public APIs; they do not replace or patch CFW runtime objects.
