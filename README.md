# DigitalPsychology

> **External adaptive behavioral controller and learning plane.**
> DP adapts optional behavior during a task and evaluates intervention outcomes across independent trajectories.

DigitalPsychology (DP) sits outside the controlled agent's runtime. A trusted
host calls it automatically at task and action boundaries; the agent does not
invoke DP. DP owns its adaptive `BehaviorState` and the policy for bounded
behavioral adjustments. CFW retains hard and local component/action authority;
CSW retains workflow state, evidence, and transition authority. DP's fast loop
uses runtime outcomes, CFW signals, CSW snapshots, and optional Laya/Jev
semantic evidence to choose bounded temporary interventions. Laya/Jev evidence
can inform a DP decision but never grants authority. Its slow loop evaluates
interventions over independent trajectories.

It was originally conceived as another behavioral skill pack. That became the wrong abstraction.

CognitiveFrameWorks already prescribed how an agent should behave. CognitiveStateWorks encoded what operational behavior and transitions were appropriate in particular workflows. Adding another set of instructions would have created more policy without answering a more fundamental question:

> **Why does the agent continue exhibiting a behavior under particular conditions — and what actually changes it?**

DP does not infer subjective experience. It uses observable behavior to decide which cognitive components and workflows should be active, and it records whether those changes helped.

## Current implementation

DP supplies the external adaptive behavioral controller and learning plane.
Its existing `lib/runtime/` and `lib/host/` trees hold the runtime contracts,
trusted lifecycle bridge, private client, and CFW/CSW adapters. The private
control MCP in `lib/transport/control_mcp.py` transports decisions from the DP
engine out of process. The trusted execution host owns the overall agent
session. `RuntimeSession` remains an in-process compatibility adapter. DP does
not grant permissions, define legal StateWork transitions, validate evidence,
or replace CFW's or CSW's authority.

The harness-neutral lifecycle, action, validator, reuse, and completion
contracts live in the separately versioned and installable
`cognitive_runtime_core` distribution. The legacy `digital_psychology.runtime`
paths re-export them for compatibility. Codex and Athena are harness adapters
to those contracts; neither defines cognitive authority. The package owns
generic transaction sequencing and cross-system CFW/CSW application with
rollback. DP still builds its private control requests, commits or aborts its
behavior decision, and reconciles uncertain authority receipts. Its source is
currently maintained in this repository.

Implemented capabilities include:

- **Authenticated event ingestion** with schema validation, host attestations, origin identity, replay safety, and privileged-category protection.
- **Trajectory reconstruction** that prevents repeated events from one trajectory from becoming independent samples.
- **Canonical experimental outcomes** (`PASS`, `FAIL`, `NOT_APPLICABLE`) with rejection of unknown values and explicit non-applicable bounds.
- **Predeclared routing experiments** with control, treatment, and holdout cohorts, host-evaluator provenance, receipt hashes, and independent-trajectory checks.
- **A bounded slow-loop controller** that derives candidates, validates receipts, promotes bounded profiles, observes lifecycle transitions, and records rejection reasons.
- **Authoritative lifecycle state** with explicit rollback/rejection transitions, durable deployment revisions, and known-good previous profiles.
- **Versioned advice-exchange contracts** so the host can record trusted context, eligible choices, requested adjustments, and the final effective routing decision.
- **A bounded fast-loop policy selector** that filters candidate behavior against host, CFW, CSW, and goal constraints, then compares validated receipt evidence with the deterministic controller's recommendation. Without an exact-scope promoted prior, the deterministic rule remains the fallback.
- **Condition-scoped intervention history** bound to target criteria, goal-set revision, workflow state, host evidence, and approach epoch. A failed intervention cannot suppress recovery after the host reports a new hypothesis or materially changed state.
- **Derived attention fairness** with a round-robin bound based on the current runnable criterion count and bounded lease size. The 258-action ceiling applies only to a full 256-item task set.
- **An independent intervention outcome evaluator** that accepts only scoped, post-intervention host/CSW/validator evidence and an exact applied-directive receipt. It distinguishes supported progress, regression, confounding, missing evidence, and expired windows; agent success claims are not evidence.
- **Intervention lifecycle records** with cause, expected effect, target criteria, baseline status, start action, bounded horizon, scope, expiry, and measured outcome; cleared or expired interventions emit explicit release directives.
- **Transactional intervention event outbox** that commits authenticated application and outcome events with the DP behavior revision, then retries delivery idempotently after restarts.
- **Constrained StateWork preferences** ranked only from the eligible choices supplied by CSW. DP cannot commit or legalize a CSW transition.
- **A versioned private MCP control protocol** with prepare/apply/commit/abort/reconcile transactions, exact directive receipts, and restart recovery against CFW and CSW revisions.
- **Harness-neutral runtime contracts** in `cognitive_runtime_core/`. `TaskContract`, `ActionProposal`, `RuntimeObservationProjection`, and `CompletionProposal` give product adapters one bounded interface for task intent, actions, outcomes, and completion evidence. DP owns behavioral policy and its private control port; adapters own only their product-specific projection.
- **Harness identity and capability negotiation** records adapter, host, protocol, and catalog pins. Unverified identities expose no effective capabilities, and unavailable boundaries produce explicit degradation claims. Typed action control payloads cross only when the verified adapter exposes both the pre-action boundary and action blocking; otherwise DP returns a degraded result without forwarding the payload.
- **Explicit product and harness adapters** normalize CFW/CSW host ports in DP and map Athena's public `TaskSpec` and durable events through `cognitive_runtime_core.integrations.athena`. The Athena adapter imports no cognitive application and cannot block actions, validate, project context, or intercept completion because Athena exposes no such public hooks. `DigitalPsychologyHarnessLifecycle` receives `component_adapter_factory` from the trusted composition root, asks the task handle to create that selection through its generic component port, and never imports or chooses a CFW implementation.
- **One sequenced control boundary** for task start, pre-model, pre-tool-planning, pre-action, post-action, validator, workflow, operator, and pre-completion observations. Pre-model and pre-tool-planning traces remain distinct at the same action index. Pre-action candidates are bounded and sanitized; non-action boundaries do not age intervention horizons, and action counters advance only after completed host actions.
- **Separate model and audit projections** so prompts receive replaceable goal guidance while intervention IDs, authority revisions, hashes, and reconciliation metadata remain host-private.
- **Host failure semantics** that retain the last committed behavior projection when DP is unavailable; CFW hard action gates and CSW legality remain active.

DP's telemetry, experiments, and deterministic controller can operate without CFW or CSW integrations. The cross-system `RuntimeHost` in the existing DP runtime uses explicit ports to coordinate them. CFW and CSW remain independently usable; the complete adaptive deployment runs CFW, CSW, and DP together, with each system retaining its own authority.

Runtime orchestration, directive application, and outcome assessment are trusted-host APIs and are deliberately not exposed through the model's tool registry. `RuntimeHost.open_control_bridge(...)` starts a private MCP child and returns the host lifecycle bridge; the bridge asks CFW to preflight the original action, evaluates a sanitized candidate through DP, then asks CFW for its final action decision and correlation ID. If either CFW gate rejects, DP receives a bounded rejection boundary and the action does not execute. The raw request stays in the host and CFW adapter. The bridge asks CSW to select only registered, eligible workflows. Independent outcome records require host evidence and an applied receipt. See [the fast-loop contract](docs/behavioral-fast-loop.md) and [intervention credit assignment](docs/intervention-outcome-credit.md).

The bridge retains a bounded receipt for the last System-1 query in its
host-only audit projection. The semantic answer is not copied into model
context.

The private `digital-psychology-control-mcp` is started by `HostControlBridge` and is not exposed in the controlled agent's model-visible tool registry. Before opening a bridge, the trusted host supplies that complete registry; DP rejects a registry that contains its private control or admin tools. An absent or partial registry cannot prove that every agent-facing tool is hidden. The legacy `digital-psychology-mcp` entry point fails closed unless explicitly enabled; `digital-psychology-admin-mcp` is for a trusted operator namespace. Keep the control process on the host side of the tool boundary.

The automated release gate covers structural and cross-system behavior. Claims about improving an external model's reasoning require the real-agent qualification harness with an explicitly configured external model command.

## Five-minute independent quick start

DP can ingest, reconstruct, experiment, and promote a routing profile without CFW or CSW installed.

Install the neutral runtime package and DP from this checkout, then configure
the MCP client to launch its stdio command:

```bash
python3 -m pip install -e ./cognitive_runtime_core
python3 -m pip install -e .
digital-psychology-admin-mcp
```

The command runs as a child process of an operator MCP client and waits for
protocol messages on stdin. Keep stdout reserved for MCP traffic. It exposes
DP's telemetry, advice, profile-validation, and slow-loop administration
tools. Register it only in a trusted operator namespace, never in the
controlled agent's model-visible tool list. The old
`digital-psychology-mcp` command fails closed unless
`DIGITALPSYCHOLOGY_ENABLE_LEGACY_MCP=1` is explicitly set. The private
`digital-psychology-control-mcp` process is launched by `HostControlBridge`
over a separate host-only connection.

The service stores ordinary state under `DIGITALPSYCHOLOGY_STATE_ROOT` (or the platform state directory). Ordinary, non-privileged events can be ingested without an authentication secret for local fixtures. In a deployment where agents can reach the MCP surface, configure `DIGITALPSYCHOLOGY_INGESTION_SECRET`; privileged categories (`validation`, `state_transition`, `completion`) and operator/repository-owner actors require host attestation.

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

The fixture uses independent control, treatment, and holdout trajectories. DP derives one candidate profile, builds a host-bound routing receipt, and promotes only after the receipt passes. Repeated events from one trajectory do not count as independent samples.

The slow loop is explicitly invoked; it is not an automatic background worker. In production, the MCP tool accepts only a protected `experiment_id` plus a host authorization, and the service loads event IDs from its authenticated SQLite store. The direct library API remains `BoundedLearningController.run(events, task_context)` for trusted host code; raw `events` and `task_context` are not accepted by the MCP boundary.

## Optional integrations

| Deployment | What runs | What remains authoritative |
| --- | --- | --- |
| **DP alone** | Event store, trajectory analysis, experiments, and slow-loop service | DP owns its telemetry and validated profiles; no CFW or CSW is required |
| **CFW alone** | CFW components, task policy, and action gates | CFW's component and action contracts |
| **CSW alone** | CSW workflow selection, durable state, and evidence checks | CSW's transition and evidence contracts |
| **CFW + DP** | CFW components and action gates with DP-controlled behavioral settings | CFW retains hard action authority |
| **CSW + DP** | DP may request a StateWork from CSW's eligible set | CSW retains workflow selection, evidence, and transition authority |
| **CFW + CSW** | CFW task-local enforcement with CSW workflows | CFW governs component/action policy; CSW governs workflow eligibility, state, evidence, and transitions |
| **CFW + CSW + DP** | The complete adaptive runtime | DP coordinates behavioral interventions; CFW and CSW retain their authorities |

The full three-system deployment is the intended setup when adaptive behavior
should be coordinated across components and workflows. CFW and CSW also have
independent standalone entry points and work together without DP installed.

## Hard authority boundaries

DP chooses bounded behavioral adjustments from host-supplied eligible choices.
It cannot disable WARD, lower CFW component floors, authorize an action, bypass
CFW's preflight or final action gate, rewrite CSW evidence, or make an illegal
workflow transition valid. CSW validates each workflow activation, release,
and transition against its own registry and revision. The trusted host owns
tool execution and completion evidence. Optional Laya/Jev judgments can inform
DP's choice; they do not grant authority.

## Optional Codex adapter qualification

CFW can use the separately distributed `cognitive-runtime-codex-adapter` to
connect to the regular packaged Codex CLI through the generic App Server
protocol. The adapter is selected by the trusted host; DP's core does not
depend on Codex or its protocol. The host registers CFW actions, and DP's
private control MCP stays on the host side, outside the model-visible tool
registry. Codex's internal inference and planning boundaries are not
intercepted.

The four-defect coding fixture is an integration and non-regression check. Its
baseline reached 4/4 on all 10 trials in the recorded run, so it does not leave
room to measure general behavioral improvement. A later single development
triad run completed 4/4 with 24 host tool calls and 48 CFW gate receipts. It is
one noncanonical sample and does not establish an effect. See CFW's
[`live-results` index](https://github.com/B-A-M-N/CognitiveFrameWorks/blob/master/evaluation_fixtures/live-results/README.md)
for provenance and limitations.

ChatGPT-mode App Server attempts stopped during workspace-routing discovery
before the host or model turn began. The completed direct sample used
FreeInference's external-provider Responses route with the regular packaged
Codex CLI. The recorded host policy exposed zero DP tools to the model. These
results establish integration for the recorded fixture, not behavioral
superiority; the baseline was already at the fixture ceiling. Workspace-routing
preflight is specific to that Codex authentication/provider path; it is not a
DP, CFW, or CSW runtime requirement.

## Second-harness acceptance (Athena)

Run `python scripts/acceptance-athena-adapter.py` to exercise DP's structural
adapter against `AthenaService.in_memory()` through Athena's public task,
event, and result APIs. The script uses Athena's in-memory fake provider and
does not add Athena as a DP dependency. Set `ATHENA_ROOT` if its checkout is
not the sibling `Athena/` directory. This qualifies API interoperability and
explicit capability degradation; it does not claim control parity or
behavioral efficacy. Athena's current public surfaces cannot block actions or
intercept completion, and the local service instance has no independent host
attestation, so the adapter remains unverified during this check.
Codex and Athena demonstrate adapter neutrality at the shared-contract level.
Full-control portability remains unproven until a second harness exposes and
qualifies pre-action blocking, context projection, and completion interception;
any such harness can satisfy that requirement.

### Wiring the trusted runtime host

The host supplies explicit CFW and optional CSW ports to DP. It calls
`RuntimeHost.open_control_bridge(...)` once per scoped task, invokes
`before_tool_planning(...)` before asking the model for an action, and calls
`before_action_candidate(...)` after the host has parsed the proposed action.
The host may also pass a versioned `harness_adapter_identity`; DP persists the
pin in the session descriptor and applies only capabilities backed by a
verified adapter identity. Without a verified identity, the runtime records
the adapter declaration but treats its capabilities as unavailable.
The private MCP transport evaluates only a sanitized `ActionCandidate`. In
CFW+DP mode, pass the original CFW action request separately as
`cfw_action_request`; the bridge sends it only to CFW for a hard preflight and
final gate. The returned CFW action request ID correlates the eventual host
tool result. If CFW rejects at either gate, DP receives the rejection boundary
and the tool does not execute. After each completed host action, call
`after_action(...)`. The bridge stays in the trusted host and is never passed
to the controlled model. `RuntimeHost.open_session(...)` and
`RuntimeSession.process_action(...)` remain available for in-process
deployments and compatibility.

For CSW integration without CFW, the trusted host can obtain bindings from
CSW's `cognitive_statework.host_binding.resolve_bindings(...)` and supply
those structural contract pins to DP's `CSWRuntimeAdapter`. CSW performs
registry, scope, and composition checks; DP only ranks the eligible snapshot
and submits requests back through CSW's authority.

For multi-criterion goals, the host sends the complete `GoalSetSnapshot`
(bounded to 256 criteria). DP leases attention to at most three criteria at a
time. Its service bound is `ceil(runnable_count / 3) * 3` actions, so the
maximum is 258 only when all 256 criteria are runnable. The prompt may show
the current attention window, while the host keeps the full goal set for
completion checks.

Host task-list items are required by default. The host may explicitly mark an
item optional, in which case DP excludes it from required-work attention and
completion. DP does not schedule optional items; the host owns any separate
best-effort budget or may omit them intentionally.

Every intervention records its cause, condition identity, target criteria,
expected effect, baseline, action horizon, and applied-directive receipt. The
condition identity includes the host's approach epoch, evidence basis,
target-criterion facts, and relevant CSW workflow state. Unrelated goal-set
revision changes do not reset a criterion's failure history. After the horizon,
DP evaluates objective progress from independent host, CSW, or validator
evidence. Agent claims and changed files alone do not establish success.
Targeted progress can earn credit only when the measured window also accounts
for regressions, newly blocked required work, and attention starvation.
Overlapping interventions remain unattributed when evidence cannot separate
their effects. See [intervention credit assignment](docs/intervention-outcome-credit.md).

### Optional Laya System-1 provider

Laya support is an optional dependency. Install it in a dedicated environment
with `python3 -m venv .venv-laya` followed by
`.venv-laya/bin/python -m pip install -e '.[laya]'` (the `system-one` extra is
an alias). This installs the Laya inference library without its separate HTTP
serving extras. Ordinary imports and the default DP service keep the provider
disabled; they do not import Laya, load PyTorch, or download model weights. The
Laya adapter uses the public `Agent.decide` API and is available only to a
trusted host through `SystemOneService`; it is not an agent-facing MCP tool.

The checkpoint must already exist on disk and be pinned by its library
revision and content hash. Set these variables before constructing the
service with `SystemOneService.from_environment()`:

```text
DIGITALPSYCHOLOGY_SEMANTIC_PROVIDER=laya-native
DIGITALPSYCHOLOGY_SYSTEM_ONE_MODE=shadow
DIGITALPSYCHOLOGY_LAYA_MODEL_PATH=/local/path/to/pinned-checkpoint
DIGITALPSYCHOLOGY_LAYA_MODEL_REVISION=1a793eb568e6718f15941d08f85432581df534e3
DIGITALPSYCHOLOGY_LAYA_MODEL_SHA256=cda55a3e793f4c4268a25066ceb1568aa302fd210d96656e3032756fd722b642
DIGITALPSYCHOLOGY_LAYA_DEVICE=cpu
DIGITALPSYCHOLOGY_LAYA_TIMEOUT_MS=30000
```

The semantic query planner invokes Laya only when host signals leave a
material ambiguity: a possible equivalent retry, a drift warning on a
consequential candidate, or new artifact/evidence without confirmed progress.
Host-verified failed-approach facts can also trigger a retry-relation query for
a candidate that changes its target or mechanism. Host-observed drift facts
can trigger a bounded goal-drift query when sustained activity has not advanced
the remaining criterion.
It skips routine reversible actions, host-confirmed progress/regression,
already-established repeated attempts, and critical WARD vetoes. At most one
question is sent per boundary; a disabled question family is never replaced
with a different one. For predictable CPU latency, set `OMP_NUM_THREADS=1`
and `MKL_NUM_THREADS=1` in the trusted host environment.

The private `control_mcp` entrypoint constructs `SystemOneService` from these
environment variables automatically. `MCPControlClient` launches that process
as a separate host-owned child; set the variables in the host process
environment and use the Python interpreter where `digital-psychology[laya]`
is installed. Set `DIGITALPSYCHOLOGY_CONTROL_MCP_PYTHON` to that interpreter
when the host's own Python environment differs. Host MCP calls have a bounded
60-second deadline to accommodate CPU inference; each Laya inference remains
bounded to 30 seconds.
By default, provider initialization runs in the background. A host that must
exclude model loading from trial timing can set
`DIGITALPSYCHOLOGY_SYSTEM_ONE_WARMUP_MODE=blocking`; the private DP process
then waits for model initialization and exposes the completed warmup duration
in its host-only provider attestation. Other values are rejected.

The ready-to-run checkpoint is `convaiinnovations/laya-typed-decisions` at
revision `1a793eb568e6718f15941d08f85432581df534e3`. Download that immutable
snapshot after installing the optional runtime:

```bash
python3 -m pip install -e '.[laya]'
python3 scripts/download_laya_model.py
```

The package requires Starlette 1.7.0 or newer. The host MCP transport and its
dependencies should be installed from the same locked environment used by the
trusted runtime process.

Set `DIGITALPSYCHOLOGY_LAYA_MODEL_PATH` to the downloaded snapshot directory. The SHA-256 pin
above covers the checkpoint, encoder configuration, and tokenizer files used
by the adapter; the checkpoint file itself has SHA-256
`4fa56de72383a9d3efa9cfa78955733c81b9fc8067a587ca4beb82c78107a24e`. The
Laya package extra installs the runtime library, while the command downloads
the model weights and their pinned metadata. Advisory mode requires at least
one active family calibration; unqualified families stay in shadow mode.
Private host control records the typed judgment in the durable prepared-tick
basis and adds an authenticated `system_one_decision` event to the durable
outbox only when the control tick commits. Aborted or stale preparations do
not produce committed decision events. Later host, validator, CSW, or operator
evidence can be recorded through
`DigitalPsychologyService.record_system_one_outcome`; the service accepts only
registered family labels and independent authority classes. The provider path
does not accept Laya answers, DP policy choices, or agent prose as outcome
evidence. Agent-visible tools do not expose it. The timeout defaults to 30
seconds for local CPU inference; a timeout causes one provider failure and
deterministic fallback.

Per-trial reports rebuild semantic telemetry from committed control transitions.
They distinguish planner opportunities, provider attempts, valid results, and
advisory use, and include query latency, receipt-linked interventions, and
independently observed outcomes grouped by question family. Outcomes resolved
later remain attached to the receipt that caused the intervention. Histories
without the new fields are reported as unobserved; these metrics do not read
the process-wide production counters.

A local pinned-checkpoint smoke on 2026-10-07 returned one typed
`progress_relation` answer through `SystemOneService` in shadow mode. The Laya
library warned about invalid checkpoint temperatures and uncalibrated
confidence; no calibration record was loaded. The
[smoke record](evaluations/laya-shadow-smoke-2026-10-07.json) proves checkpoint
loading and adapter output only; it does not establish accuracy or efficacy.
Laya support is structurally available, but Laya efficacy is not yet qualified:
the runtime is absent from the current qualification environment, no active
family calibration is available, and no matched advisory outcome study has
passed the required gates. Do not claim that Laya improves behavior until that
evidence exists.

Run `digital-psychology-laya-doctor` in the trusted runtime environment to
check the optional import, pinned checkpoint hash, provider protocol, current
mode, and per-family calibration status. It does not print credentials or
load model weights. Shadow readiness requires the Laya package and verified
checkpoint but does not require calibration. Advisory readiness is reported
per family: active qualified families may influence DP, while missing or
shifted families remain shadow-only.

The doctor exits according to the configured mode. Use `--require shadow` or
`--require advisory` to select an explicit gate. Advisory mode succeeds with
partial family coverage by default when at least one enabled family is active;
`--require-all-enabled-families` makes every configured family require an
active matching calibration. `digital-psychology-laya-smoke --json` loads the
pinned checkpoint and runs one bounded synthetic frame through a shadow
inference. The report separates warmup from inference latency and records
explicit runtime-loaded and typed-judgment flags. It reports
`efficacy_claim: false`; the smoke checks runtime loading and typed output only.

`digital-psychology-laya-qualify --event-db PATH --question-family FAMILY
--output CANDIDATE.json` reads only authenticated System-1 decision and
outcome events. It keeps the production minimums of 100 outcomes, 30
independent trajectories, and 20 trajectories in each applicable
work-mode/deliverable cohort. Training, calibration, and holdout data are
partitioned by trajectory. The command writes a mode-0600 candidate record;
activation still requires independent host review through
`SystemOneCalibrationLifecycle`. Threshold flags may make qualification
stricter, but cannot weaken those production minimums.

To prepare for a later efficacy study, also pass `--corpus-id ID
--corpus-manifest-out CORPUS.json` to `laya-qualify`. The private manifest
contains hashes of the task instances that contributed authenticated
prediction/label pairs and binds them to the candidate digest.

After independent host review, activate a candidate with
`digital-psychology-laya-activate --candidate CANDIDATE.json
--candidate-digest SHA256 --question-family FAMILY --lifecycle STATE.json
--output ACTIVE.json --authority-ref REVIEW_ID
--expected-prior-hash none`. For later updates, pass the current active bundle
hash instead of `none`. The command verifies the candidate digest, status,
qualification, provider protocol, and configured model pins; checks any
existing exported active bundle against lifecycle state; and emits a JSON receipt
on stdout after writing the active bundle atomically with mode 0600. Set
`DIGITALPSYCHOLOGY_LAYA_MODEL_REVISION` and
`DIGITALPSYCHOLOGY_LAYA_MODEL_SHA256` to the target checkpoint pins before
activation; the candidate identity must match them.

### Generic loopback HTTP provider

Hosts that run the provider in a separate local process can select the generic
loopback adapter:

```text
DIGITALPSYCHOLOGY_SEMANTIC_PROVIDER=loopback-http
DIGITALPSYCHOLOGY_SYSTEM_ONE_ENDPOINT=http://127.0.0.1:8000/decide
DIGITALPSYCHOLOGY_SYSTEM_ONE_MODEL_ID=<model-id>
DIGITALPSYCHOLOGY_SYSTEM_ONE_MODEL_REVISION=<immutable-revision>
DIGITALPSYCHOLOGY_SYSTEM_ONE_MODEL_SHA256=<model-content-digest>
DIGITALPSYCHOLOGY_SYSTEM_ONE_PROVIDER_PROTOCOL_SHA256=<protocol-digest>
```

The endpoint must be loopback HTTP. The old provider value `jev-http` remains
as a deprecated alias and emits a `DeprecationWarning`.

Advisory mode requires at least one active calibration record. The host builds a
candidate from later independent outcomes split by trajectory into training,
calibration, and holdout cohorts. It fits temperature on calibration rows and
qualifies accuracy, Brier score, log loss, calibration error, and per-class
precision/recall on holdout rows. A host reviews and activates the candidate
through `SystemOneCalibrationLifecycle`, then points
`DIGITALPSYCHOLOGY_SYSTEM_ONE_CALIBRATION_PATH` at the exported active bundle.
Bundles without the three-part split are rejected. Qualification and advisory
eligibility are per question family; a family without an active matching
record remains in shadow mode.
Calibration is bound to one question family, model-content identity, protocol,
and independent `(work mode, deliverable kind)` trajectory cohorts. Route
preference calibration also records the exact candidate count, candidate-set
digest, and domain-tag digest; a context shift falls back to shadow. Missing or
out-of-scope calibration or a per-question confidence below its threshold
leaves that question in shadow.

The diagram above describes the optional combined loop. It is not a requirement for DP ingestion or experimentation.

## The larger architecture

| System | Primary question | Character |
| --- | --- | --- |
| **DigitalPsychology** | **How should the behavioral configuration adapt to current evidence?** | Behavioral policy and learning |
| **CognitiveFrameWorks** | **Which cognitive components and signals are available?** | Component library and startup presets |
| **CognitiveStateWorks** | **Which workflow states and transitions are legal?** | Authoritative state, evidence, and legality |

Together they form a **closed-loop cognitive-behavioral control architecture**:

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

This makes the system adaptive without making it self-modifying in an uncontrolled way.

## Host-only control MCP

`digital-psychology-control-mcp` is a private stdio process launched by the
trusted host. It is separate from the public telemetry and advice server. The
controlled agent must not receive this server's tools. `HostControlBridge`
sends pre-action candidates and completed-action observations to DP, applies
prepared CFW and CSW directives through local adapters, and commits only after
those authorities confirm their revisions. CFW preflight and final action
gates run locally around each DP candidate tick.

The private transport requires MCP Python SDK 2.2 or newer. Install the project
and launch the host bridge from the same environment so the client and server
use the declared MCP version.

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
parses a proposed tool request, call `bridge.before_action_candidate(candidate,
cfw_action_request=raw_request)` in CFW+DP mode. The candidate contains only an
action class, proposed purpose, affected criterion IDs, target kind, and
optional hashed target reference; the raw request is passed to the local CFW
adapter and never to DP. Execute only when the bridge reports
`action_authorized: true` and includes the CFW request ID. CFW performs its
hard preflight before the DP tick and its final action gate afterward; DP never
returns an allow/deny authority decision. If CFW is absent, the bridge reports
`action_authorized: null`, and the host's local action authority remains
responsible for execution. Call `bridge.after_action(...)` after each completed
host-observed action. DP assesses intervention outcomes only from later host
evidence. `bridge.before_model_call(...)` adds the current committed behavioral
projection. The client launches the control MCP as a private child process; do
not add its control tools to the model-visible MCP configuration.
Model guidance is held separately from audit metadata and replaces one bounded
context slot. Internal behavior revisions with unchanged semantic guidance do
not rewrite that slot; intervention IDs, authority revisions, and reconciliation
details remain in the host audit projection.

Laya remains optional. Install it only when the deployment selects the native
Laya semantic backend:

```bash
pip install 'digital-psychology[laya]'
```

## What “why” means here

DigitalPsychology does **not** claim privileged access to a model's internal subjective experience. Statements such as `"I became confused"` or `"I thought the test was enough"` are behavioral outputs, not authoritative causal explanations.

“Why” means something more rigorous:

> **Under which measurable conditions does this behavior occur, which variables predict it, and which controlled interventions reliably change it?**

DP can therefore distinguish possibilities such as agent effect, model effect, harness effect, task effect, tool/environment effect, state/workflow effect, delegation effect, interaction effect, and policy effect without anthropomorphizing the system.

## Why DigitalPsychology is separate

If the system that creates a rule also decides whether the rule worked, confirmation bias becomes architecture.

```text
CFW:
"the agent should do X"

CSW:
"the workflow requires X before Y"

DP:
"does the agent actually do X?
 under which conditions?
 did introducing X improve the outcome?
 did the failure simply move somewhere else?"
```

DigitalPsychology remains independent so it can evaluate CognitiveFrameWorks and CognitiveStateWorks themselves.

## Behavioral telemetry

DP should not need to live inside the active model context. The host runtime emits small structured events describing what it already knows:

* decisions, observations, tool requests and tool results;
* contradictions, retries, delegations and corrections;
* state transitions, completion claims and evidence relationships;
* routing selections and routing suppressions.

Behavioral identity is hierarchical:

```text
agent instance
    └── session
         └── task
              └── attempt
                   └── segment
                        └── event
```

Multi-agent work may additionally include interaction, parent-session, delegator, delegate, delegation, and role identities. This prevents unrelated sessions from being collapsed into one behavioral history.

## From events to behavior

A single event rarely establishes a behavioral pattern. DP reconstructs behavior over progressively larger temporal units:

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

For example, a completion claim followed by an operator contradiction, defense of the prior conclusion, fresh tool use, and a revised conclusion is more meaningful than counting those events independently.

### Independent sessions are the experimental unit

Cross-session learning must not treat multiple log events as multiple independent observations. A promotion-grade trial is closer to:

```text
agent instance + session + task + attempt + behavioral subject
```

Events are reconstructed inside that trajectory first. Only then are independent trajectories compared statistically.

## Three classes of intervention

### Structural intervention

Change what the runtime permits:

```text
unsafe action → host gate blocks action
```

A deterministic fake-agent harness is useful here because the test asks whether the structural control works independently of model reasoning.

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

This also requires real-agent qualification. A structural test cannot prove that a cognitive or routing intervention improved model behavior.

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

A routing profile changes which **already legal** options should be preferred or suppressed for a particular context. It does not create authority, make an illegal StateWork transition legal, or remove mandatory safety policy.

```text
agent/model/harness/task-family
        + repeated unnecessary FLOW activation
        + controlled evidence showing no outcome benefit
        ↓
validated routing profile
        ↓
future matching tasks suppress optional FLOW activation
```

The goal is not endlessly accumulating rules. It is better behavior with the smallest effective active control surface.

## Learning should be subtractive too

Validated evidence may justify suppressing an unnecessary flow or optional specialist, avoiding ineffective delegation, reducing retry amplification, removing a stale guard, retiring a superseded intervention, or preferring a simpler legal composition.

## Real-agent behavioral experiments

A behavioral claim requires observing an actual agent performing an actual task. A useful experiment preserves:

* the same task goal and fixture/environment;
* comparable model, tool access, and host conditions;
* blinded cohort identity;
* explicit control and treatment policies;
* independent sessions and objective outcome evaluators.

Possible measurements include task success, correction cycles, redundant tool calls, unnecessary routing, premature completion, hypothesis revision after contradiction, retry amplification, context consumption, latency, token use, state-transition errors, and delegation failures.

An intervention should not be declared successful merely because one preferred metric improved. Task outcome and safety/regression metrics must remain acceptable.

## Multi-agent behavior

Multiple agents agreeing with one another is not necessarily independent evidence. They may share the same premise, source context, delegator, or failure mode.

DP therefore records interaction and delegation provenance and distinguishes individual-agent tendency, inherited premise, delegator effect, delegate effect, interaction effect, topology effect, circular validation, role drift, correction cascade, and duplicated investigation.

## Fast loop versus slow loop

DigitalPsychology owns both the bounded fast controller and the evidence driven slow learner.

```text
During a task:  host event
                → CFW and CSW provide bounded state
                → DP selects optional behavior changes
                → host applies only legal changes
                → host evidence scores each intervention

Across tasks:   DP → reconstruct → compare → experiment
                  → validate → affect future composition
```

Fast-loop changes carry bounded leases and independent outcomes. Slow-loop policy promotion requires matched, independent trajectories; one task cannot establish a durable behavioral prior.

## Core principles

> **Agent behavior is data.**

> **Agent action is not evidence that the action succeeded.**

> **Confidence is behavior, not proof.**

> **The agent's explanation of itself is another output to evaluate.**

> **One session is not a population.**

> **Many events from one session are not many independent trials.**

> **Multi-agent agreement is not independent verification when agents inherit the same evidence or premise.**

> **A repeated failure should become an experiment, not another reminder.**

> **The goal is not to humanize agents. The goal is to make their behavior measurable enough to engineer.**

## The three-system model

```text
DigitalPsychology
(optional external behavior controller)
              │ private bounded decisions
              ▼
       Trusted execution host
          /             \\
         ▼               ▼
        CFW             CSW
 component/action     workflow/state
 enforcement          authority
         \\               /
          \\             /
          Controlled agent
                 │
       host outcomes and evidence
                 └──────────────► DP
```

CFW provides cognitive components, signals, and hard/local enforcement. CSW provides authoritative workflow state and legal transitions. DigitalPsychology coordinates bounded cross-system behavioral adaptation and evaluates outcomes from evidence supplied by the trusted host.

Together, they form an **adaptive agent control stack**: not a single giant prompt, not an unconstrained self-modifying agent, but a layered system in which behavior can be prescribed, state can be governed, outcomes can be observed, and future policy can improve from evidence.

## Tests, limitations, and release status

Run:

```bash
python3 scripts/self-test.py
python3 scripts/test-routing-learning.py
python3 scripts/acceptance-runtime-triad.py
pytest -q tests
```

The automated release gate is defined by the sibling CFW repository's [`release-gates.json`](https://github.com/B-A-M-N/CognitiveFrameWorks/blob/master/release-gates.json). Its guard-policy causal check and private-MCP runtime-triad check are separate mandatory gates. The runtime triad exercises current DP, CFW, and CSW authorities together, including a private DP process restart, a CFW final-gate rejection, lease ownership, and twelve-criterion attention coverage. Real-agent efficacy has not been established: improvement claims require CFW's [`acceptance-real-agent.py`](https://github.com/B-A-M-N/CognitiveFrameWorks/blob/master/scripts/acceptance-real-agent.py) with an explicit external model command; deterministic DP tests do not establish that claim.

DP's promotion receipts are authoritative for its own experiment artifacts, but real model behavior, tool outcomes, and deployment-specific evaluator quality remain external dependencies. The optional MCP transport is not required for library-only use.

## License and contribution

These projects are released under the [MIT License](LICENSE). Contributions are welcome through repository issues and pull requests. Please include a focused regression or acceptance check for behavior changes, keep authority boundaries explicit, and do not claim model-performance improvements without the corresponding qualification evidence.

## Controlled knowledge sharing

Raw observations remain origin-scoped evidence. They do not become another agent's instructions merely by being stored.

Routing profiles declare `agent_private`, `application_shared`, or `cross_application` scope. Wider scope requires a candidate-bound, hash-authenticated promotion decision; cross-application sharing additionally requires explicit data-sharing authorization. CFW adoption remains a separate per-task decision tied to the exact profile hash.

Provenance identity and experimental comparison context are separate. Exact session, task, attempt, interaction, and delegation IDs identify causal evidence, while model, harness, role, task family, environment, and effective policy determine whether trials are comparable. Shared interaction evidence is rejected when it crosses experimental cohorts, and all treatment failures inherited from one shared premise cannot create an individual corrective profile.

## FreeInference Acknowledgement

This work benefited in some way from inference provided by [freeinference.org](https://freeinference.org/).

These are independent developments that are not reviewed, endorsed, or sponsored by FreeInference. If you find these projects useful, please consider donating to or sponsoring FreeInference, which provides a vital inference service.
