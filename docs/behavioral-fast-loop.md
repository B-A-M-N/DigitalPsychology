# Behavioral fast-loop contract

DigitalPsychology (DP) owns the meaning and transition policy of
`BehaviorState`. A trusted MCP runtime host owns the live instance, persistence,
revision checks, hard-policy resolution, and application of the returned
directives.

## Inputs and authority

The host calls DP with:

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
revision, directives, eligible StateWork preferences, and closed intervention
outcomes. The CSW workflow revision remains separate from the DP behavior
revision.

The `DigitalPsychologyService.decide_behavior()` method is a trusted-host API.
It is intentionally not registered as an agent-facing MCP tool. The caller
must compare `expected_revision` with persisted state, intersect the decision
with hard constraints, and persist the result with compare-and-swap. WARD stays
mandatory. DP cannot mint evidence, grant permissions, make an illegal CSW
transition legal, or declare a task complete.

StateWork preferences are ranked only from `CSWBehaviorSnapshot.eligible_stateworks`.
The host passes a requested choice through CSW's own transition and evidence
checks before activating it. A rejected request is recorded as rejected and is
not copied into the next authoritative CSW snapshot.

## Intervention lifecycle

Every active intervention has:

- a stable intervention ID and cause;
- an expected effect;
- its starting DP revision and runtime scope;
- the settings it changed, with their previous values;
- an action-count expiry.

When the cause clears, DP emits directives that restore the prior settings and
closes the intervention with an observed outcome. If the action limit expires
without progress, DP records `expired`, releases the temporary settings, and
blocks automatic repetition for that cause. The host reports later outcomes
with host evidence references so the DP slow loop can distinguish improvement
from coincidence.

## Python use

```python
from digital_psychology import (
    BehaviorState,
    CSWBehaviorSnapshot,
    FastLoopInput,
    FrameworkSignal,
    StateWorkOption,
)
from digital_psychology.mcp_server import DigitalPsychologyService

context = FastLoopInput(
    scope="tenant/app/agent/session/task/attempt",
    state=BehaviorState(),
    csw_snapshot=CSWBehaviorSnapshot(
        revision=12,
        eligible_stateworks=(
            StateWorkOption("repair", behavior_tags=("recovery", "repair")),
        ),
    ),
    objective_criteria=("criterion-2",),
    cfw_signals=(FrameworkSignal("owl", "stale_assumption"),),
)

service = DigitalPsychologyService(event_db="/var/lib/dp/events.sqlite3")
decision = service.decide_behavior(context)
# The MCP host performs CAS, hard-policy checks, CSW validation, and application.
```

The wire formats are versioned in
`digital_psychology/schemas/behavior-state-v1.schema.json` and
`digital_psychology/schemas/behavioral-decision-v1.schema.json`.

## Integration status

The DP contracts and deterministic fast-loop policy are implemented. CFW's
existing `RuntimeSession` still contains host/session behavior, local lifecycle
activation, and direct CSW integration. Migration of those responsibilities
to an MCP runtime host and conversion of CFW to signal-producing components
remain required before the full target architecture is complete.
