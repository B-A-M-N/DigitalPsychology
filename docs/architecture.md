# Architecture

Runtime boundaries, host lifecycle, authority ownership, and adapters for
DigitalPsychology.

## Where DP sits

DP sits **outside** the controlled agent's runtime. A trusted host calls it
automatically at task and action boundaries; the agent does not invoke DP.

```
the agent ──▶ trusted host ──▶ DP (out of process) ──▶ trusted host ──▶ the agent
                 │                     │
                 └─ CFW / CSW signals  └─ bounded intervention requests
```

The agent never sees DP, its decisions, or its evidence. DP's adaptive
`BehaviorState` and its policy for bounded behavioral adjustments are owned by
DP; the host decides what to execute.

## Authority ownership

| System | Owns |
|---|---|
| DigitalPsychology | `BehaviorState`, adaptive policy, intervention receipts, outcome attribution |
| CognitiveFrameWorks | Hard and local component/action authority |
| CognitiveStateWorks | Workflow state, evidence requirements, transition authority |

Laya/Jev semantic evidence can inform a DP decision but **never grants
authority**.

## The three-system model

CognitiveFrameWorks already prescribed how an agent should behave.
CognitiveStateWorks encoded what operational behavior and transitions were
appropriate in particular workflows. Adding another set of instructions would
have created more policy without answering a more fundamental question:

> **Why does the agent continue exhibiting a behavior under particular
> conditions — and what actually changes it?**

DP does not infer subjective experience. It uses observable behavior to decide
which cognitive components and workflows should be active, and it records whether
those changes helped. See
[`docs/triad-overview.md`](triad-overview.md) for how the three authorities fit
together.

## Runtime components

DP supplies the external adaptive behavioral controller and learning plane. Its
`lib/runtime/` and `lib/host/` trees hold the runtime contracts, trusted
lifecycle bridge, private client, and CFW/CSW adapters. The private control MCP
in `lib/transport/control_mcp.py` transports decisions from the DP engine out of
process. The trusted execution host owns the overall agent lifecycle.

## Host-only control MCP

The private control MCP carries DP's decisions between processes without
exposing them to the agent. See
[`docs/behavioral-fast-loop.md`](behavioral-fast-loop.md) for the fast-loop
contract and
[`docs/intervention-outcome-credit.md`](intervention-outcome-credit.md) for how
outcomes are attributed.

## Wiring the trusted runtime host

See the integration sections for Codex and Athena, and
[`docs/harness-integrations.md`](harness-integrations.md) for generic harness
capabilities.

## Controlled knowledge sharing

Controlled knowledge sharing is governed by the trusted host, not by the agent.
Shared behavioral knowledge crosses an explicit, host-enforced boundary; nothing
an agent can write is trusted as knowledge input. See the
[authority boundaries](#authority-ownership) above.

## Implemented capabilities

These mechanisms are implemented and exercised by the repository's tests. They
establish that the structure works as specified; they do **not** by themselves
establish that an external model behaves better.

- **Authenticated event ingestion** with schema validation, host attestations,
  origin identity, replay safety, and privileged-category protection.
- **Trajectory reconstruction** that prevents repeated events from one trajectory
  from becoming independent samples.
- **Canonical experimental outcomes** (`PASS`, `FAIL`, `NOT_APPLICABLE`) with
  rejection of unknown values and explicit non-applicable bounds.
- **Predeclared routing experiments** with control, treatment, and holdout
  cohorts, host-evaluator provenance, receipt hashes, and
  independent-trajectory checks.
- **A bounded slow-loop controller** that derives candidates, validates receipts,
  promotes bounded profiles, observes lifecycle transitions, and records
  rejection reasons.
- **Authoritative lifecycle state** with explicit rollback/rejection transitions,
  durable deployment revisions, and known-good previous profiles.
- **Versioned advice-exchange contracts** so the host can record trusted context,
  eligible choices, requested adjustments, and the final effective routing
  decision.
- **A bounded fast-loop policy selector** that filters candidate behavior against
  host, CFW, CSW, and goal constraints, then compares validated receipt evidence
  with the deterministic controller's recommendation. Without an exact-scope
  promoted prior, the deterministic rule remains the fallback.
- **Condition-scoped intervention history** bound to target criteria, goal-set
  revision, workflow state, host evidence, and approach epoch. A failed
  intervention cannot suppress recovery after the host reports a new hypothesis
  or materially changed state.
- **Derived attention fairness** with a round-robin bound based on the current
  runnable criterion count and bounded lease size. The 258-action ceiling applies
  only to a full 256-item task set.
- **An independent intervention outcome evaluator** that accepts only scoped,
  post-intervention host/CSW/validator evidence and an exact applied-directive
  receipt. It distinguishes supported progress, regression, confounding, missing
  evidence, and expired windows; agent success claims are not evidence.
- **Intervention lifecycle records** with cause, expected effect, target criteria,
  baseline status, start action, bounded horizon, scope, expiry, and measured
  outcome; cleared or expired interventions emit explicit release directives.
- **Transactional intervention event outbox** that commits authenticated
  application and outcome events with the DP behavior revision, retries delivery
  idempotently after restarts, and retains attempt, failure-class, delivery, and
  authentication status for trusted trial reports.
- **Constrained StateWork preferences** ranked only from the eligible choices
  supplied by CSW. DP cannot commit or legalize a CSW transition.
- **A versioned private MCP control protocol** with
  prepare/apply/commit/abort/reconcile transactions, exact directive receipts,
  and restart recovery against CFW and CSW revisions.
- **Harness-neutral runtime contracts** in `cognitive_runtime_core/`.
  `TaskContract`, `ActionProposal`, `RuntimeObservationProjection`, and
  `CompletionProposal` give product adapters one bounded interface for task
  intent, actions, outcomes, and completion evidence. DP owns behavioral policy
  and its private control port; adapters own only their product-specific
  projection.
- **Harness identity and capability negotiation** records adapter, host,
  protocol, and catalog pins. Unverified identities expose no effective
  capabilities, and unavailable boundaries produce explicit degradation claims.
  Typed action control payloads cross only when the verified adapter exposes
  both the pre-action boundary and action blocking; otherwise DP returns a
  degraded result without forwarding the payload.
- **Explicit product and harness adapters** normalize CFW/CSW host ports in DP
  and map Athena's public `TaskSpec` and durable events through
  `cognitive_runtime_core.integrations.athena`. The Athena adapter imports no
  cognitive application and cannot block actions, validate, project context, or
  intercept completion because Athena exposes no such public hooks.
  `DigitalPsychologyHarnessLifecycle` receives `component_adapter_factory` from
  the trusted composition root, asks the task handle to create that selection
  through its generic component port, and never imports or chooses a CFW
  implementation.
- **One sequenced control boundary** for task start, pre-model,
  pre-tool-planning, pre-action, post-action, validator, workflow, operator, and
  pre-completion observations. Pre-model and pre-tool-planning traces remain
  distinct at the same action index. Pre-action candidates are bounded and
  sanitized; non-action boundaries do not age intervention horizons, and action
  counters advance only after completed host actions.
- **Separate model and audit projections** so prompts receive replaceable goal
  guidance while intervention IDs, authority revisions, hashes, and
  reconciliation metadata remain host-private.
- **Host failure semantics** that retain the last committed behavior projection
  when DP is unavailable; CFW hard action gates and CSW legality remain active.

## Runtime orchestration

Runtime orchestration, directive application, and outcome assessment are
trusted-host APIs and are deliberately not exposed through the model's tool
registry. `RuntimeHost.open_control_bridge(...)` starts a private MCP child and
returns the host lifecycle bridge; the bridge asks CFW to preflight the original
action, evaluates a sanitized candidate through DP, then asks CFW for its final
action decision and correlation ID. If either CFW gate rejects, DP receives a
bounded rejection boundary and the action does not execute. The raw request stays
in the host and CFW adapter. The bridge asks CSW to select only registered,
eligible workflows. Independent outcome records require host evidence and an
applied receipt.

The bridge retains a bounded receipt for the last System-1 query in its host-only
audit projection. The semantic answer is not copied into model context.

The private `digital-psychology-control-mcp` is started by `HostControlBridge`
and is not exposed in the controlled agent's model-visible tool registry. Before
opening a bridge, the trusted host supplies that complete registry; DP rejects a
registry that contains its private control or admin tools. An absent or partial
registry cannot prove that every agent-facing tool is hidden. The legacy
`digital-psychology-mcp` entry point fails closed unless
`DIGITALPSYCHOLOGY_ENABLE_LEGACY_MCP=1` is explicitly set;
`digital-psychology-admin-mcp` is for a trusted operator namespace. Keep the
control process on the host side of the tool boundary.

The harness-neutral lifecycle, action, validator, reuse, and completion contracts
live in the separately versioned and installable `cognitive_runtime_core`
distribution. The legacy `digital_psychology.runtime` paths re-export them for
compatibility. Codex and Athena are harness adapters to those contracts; neither
defines cognitive authority. The package owns generic transaction sequencing and
cross-system CFW/CSW application with rollback. DP still builds its private
control requests, commits or aborts its behavior decision, and reconciles
uncertain authority receipts. `RuntimeSession` remains an in-process
compatibility adapter.

The automated release gate covers structural and cross-system behavior. Claims
about improving an external model's reasoning require the real-agent
qualification harness with an explicitly configured external model command.

## The larger architecture

| System             | Primary question                                             | Character                        |
| ------------------ | ------------------------------------------------------------ | -------------------------------- |
| DigitalPsychology  | How should the behavioral configuration adapt to evidence?   | Behavioral policy and learning   |
| CognitiveFrameWorks | Which cognitive components and signals are available?       | Component library and presets    |
| CognitiveStateWorks | Which workflow states and transitions are legal?             | Authoritative state and legality |

```text
Public MCP transport ── telemetry/advice tools

Private DP control MCP (lib/transport/control_mcp.py)
  └── versioned prepare / commit / abort / reconcile API

Harness-neutral lifecycle contracts (cognitive_runtime_core/)
  ├── action, validator, evidence-reuse, and completion contracts
  └── lifecycle events, capability negotiation, and adapter identity

DP controller/host runtime (lib/runtime/ and lib/host/)
  ├── CSW: authoritative state snapshots and legal transitions
  ├── CFW: component signals and validated cognitive actuators
  └── DP: BehaviorState, bounded attention, intervention lifecycle
          ├── optional Laya/Jev semantic judgments
          └── slow-loop experiments and validated behavioral priors
  applies DP preferences only after CFW/CSW revision checks and local hard-policy resolution
  scores bounded interventions using independent host evidence
                       │
                       ▼
                     Agent
                       │
                actions / results
                       └────────────── feedback to MCP and DP
```

This makes the system adaptive without making it self-modifying in an
uncontrolled way.

## What "why" means here

DigitalPsychology does **not** claim privileged access to a model's internal
subjective experience. Statements such as "I became confused" or "I thought the
test was enough" are behavioral outputs, not authoritative causal explanations.

"Why" means something more rigorous:

> **Under which measurable conditions does this behavior occur, which variables
> predict it, and which controlled interventions reliably change it?**

DP can therefore distinguish possibilities such as agent effect, model effect,
harness effect, task effect, tool/environment effect, state/workflow effect,
delegation effect, interaction effect, and policy effect without anthropomorphizing
the system.

## Why DigitalPsychology is separate

If the system that creates a rule also decides whether the rule worked,
confirmation bias becomes architecture.

```text
CFW: "the agent should do X"

CSW: "the workflow requires X before Y"

DP:  "does the agent actually do X?
      under which conditions?
      did introducing X improve the outcome?
      did the failure simply move somewhere else?"
```

DigitalPsychology remains independent so it can evaluate CognitiveFrameWorks and
CognitiveStateWorks themselves.

## Host-only control MCP

`digital-psychology-control-mcp` is a private stdio process launched by the
trusted host. It is separate from the public telemetry and advice server. The
controlled agent must not receive this server's tools. `HostControlBridge` sends
pre-action candidates and completed-action observations to DP, applies prepared
CFW and CSW directives through local adapters, and commits only after those
authorities confirm their revisions. CFW preflight and final action gates run
locally around each DP candidate tick.

The private transport requires MCP Python SDK 2.2 or newer. Install the project
and launch the host bridge from the same environment so the client and server use
the declared MCP version.

The host process can open a bridge directly:

```python
from digital_psychology.host import HostControlBridge

bridge = HostControlBridge(
    identity=runtime_identity,
    objective=objective,
    objective_criteria=criterion_ids,
    initial_goal_set=goal_set,
    cfw=cfw_port,
    csw=csw_port,
)
await bridge.open()
```

Call `bridge.before_tool_planning(...)` before the model plans. Once the host
parses a proposed tool request, call
`bridge.before_action_candidate(candidate, cfw_action_request=raw_request)` in
CFW+DP mode. The candidate contains only an action class, proposed purpose,
affected criterion IDs, target kind, and optional hashed target reference; the raw
request is passed to the local CFW adapter and never to DP. Execute only when the
bridge reports `action_authorized: true` and includes the CFW request ID. CFW
performs its hard preflight before the DP tick and its final action gate
afterward; DP never returns an allow/deny authority decision. If CFW is absent,
the bridge reports `action_authorized: null`, and the host's local action
authority remains responsible for execution. Call `bridge.after_action(...)` after
each completed host-observed action. DP assesses intervention outcomes only from
later host evidence. `bridge.before_model_call(...)` adds the current committed
behavioral projection. The client launches the control MCP as a private child
process; do not add its control tools to the model-visible MCP configuration.

Model guidance is held separately from audit metadata and replaces one bounded
context slot. Internal behavior revisions with unchanged semantic guidance do not
rewrite that slot; intervention IDs, authority revisions, and reconciliation
details remain in the host audit projection.

Laya remains optional. Install it only when the deployment selects the native
Laya semantic backend:

```bash
pip install 'digital-psychology[laya]'
```
