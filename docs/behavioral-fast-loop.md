# Behavioral fast-loop contract

DigitalPsychology (DP) owns the runtime host and the meaning and transition
policy of `BehaviorState`. `RuntimeHost` opens isolated `RuntimeSession`
instances; each session owns the behavioral event loop, DP state persistence,
revision checks, context projection, and coordination through explicit CFW and
CSW ports.

## Inputs and authority

For each host action, `RuntimeSession.process_action()` gathers:

- the current DP `BehaviorState` and its revision;
- the complete host-owned task set, bounded to 256 criteria, with per-criterion
  status, priority, dependencies, retry count, and stall age;
- a DP attention lease covering at most three runnable required criteria for
  at most three actions. Oldest-attended criteria take precedence over priority,
  so priority cannot starve required work; all 256 criteria are selected within
  the 258-action fairness bound;
- bounded CFW signals, such as stale assumptions, equivalent retries, or
  support work displacing unfinished criteria;
- a bounded CSW snapshot, including the current active workflows and only
  StateWork choices CSW has declared eligible;
- objective criteria, recent host-observed outcomes, evidence references, and
  context pressure;
- an optional DP System-1 judgment. Laya is one optional provider; deterministic
  policy works when System-1 is disabled or unavailable.

The private MCP host bridge also supports a sanitized `ActionCandidate` at the
tool-planning boundary. Its purpose and target are marked `model_proposed` in
the System-1 frame, so Laya can assess the planned action before execution
without treating the proposal as observed progress. A pre-action control tick
may apply a behavior change, but it does not advance the completed-action
index, age existing interventions, or assess intervention outcomes. The later
host action result supplies the evidence needed for outcome attribution.

For a live System-1 path, configure `RuntimeHost(system_one_frame_factory=...)`
with a host-side builder that returns a validated `SystemOneFrame` for the
current runtime identity, DP state, observation, CFW signals, and CSW snapshot.
The DP session dispatches that frame through its internal judge before the fast
loop runs. Provider selection stays explicit through
`SystemOneService.from_environment()`; without a configured builder and judge,
the deterministic policy continues to operate without semantic augmentation.

DP returns a typed `BehavioralDecision` with a state at exactly the next DP
revision, directives, CSW-eligible StateWork preferences, and independently
assessed intervention outcomes. DP persists its behavior revision separately
from the CSW selection and workflow revisions.

The fast loop compares at most two configurations per tick: continuation of
the current configuration and the one bounded change proposed by the existing
deterministic rules. Host/operator requirements, mandatory WARD, available CFW
components, CSW eligibility, and goal-contract state filter candidates before
scoring. A promoted behavior policy can affect the choice only when its exact
candidate, subject, context, condition, sharing authorization, and passing
independent experiment receipt match. If no such prior applies, the existing
deterministic recommendation is used. Laya may improve semantic observations;
it does not select or authorize behavior policies.

On the private MCP path, each configured System-1 result is captured in the
durable prepared-tick basis with its status, model identity, and typed answers.
This records shadow predictions for later independent review while ensuring
uncalibrated answers cannot affect the decision. The controlled agent does not
receive this host-only audit basis.

The DP state revision, applied-directive receipts, and pending intervention
events commit together in the local SQLite state store. Event delivery uses
stable IDs and is acknowledged only after the event sink accepts the same
payload. A restart retries pending events without losing or duplicating the
intervention record.

The whole task set remains the completion authority. A failed or stalled
criterion does not block runnable peers; DP enters `BLOCKED` only when no
required criterion can run. The task is ready to complete only when every
required criterion is host-verified. Prompt projection includes aggregate
counts, the current attention lease, the next ready criteria, and the oldest
starved criterion rather than injecting an unbounded criterion list.

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

A StateWork activated as part of a temporary DP intervention is leased to that
intervention. When the intervention is assessed, withdrawn, or expires, DP
emits a typed release directive; the host deactivates it through CSW's
revisioned API and compensates if the rest of the decision cannot be committed.

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
from digital_psychology.system_one_service import SystemOneService

system_one = SystemOneService.from_environment()
service = DigitalPsychologyService(
    event_db="/var/lib/dp/events.sqlite3", system_one_service=system_one)
host = RuntimeHost(
    service=service,
    state_path="/var/lib/dp/behavior-state.sqlite3",
    system_one_frame_factory=trusted_frame_builder,
)
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

The same session evaluates each closed intervention against its starting task
set and the current host snapshot. Target progress receives credit only when
required work has no regression and remains inside the starvation bound.
Progress on one criterion does not conceal regression on another.

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
