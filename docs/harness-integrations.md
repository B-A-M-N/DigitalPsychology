# Harness integrations

How DP connects to an agent harness. Integration is optional; DP runs
independently for behavioral analysis without any of this.

## The trusted host

DP is called by a **trusted host**, never by the agent. The host owns the agent
lifecycle, calls DP at task and action boundaries, and applies only the changes
DP's authority boundaries permit. See [`docs/architecture.md`](architecture.md).

## Generic loopback HTTP provider

Hosts that run the provider in a separate local process can select the generic
loopback adapter. Configuration is provided by the host; DP does not assume a
particular transport.

## Wiring the trusted runtime host

The private `control_mcp` entrypoint constructs the DP service from its
environment. `MCPControlClient` launches that process as a separate host-owned
child, so DP's decisions travel between processes without becoming agent-visible.
See [`docs/architecture.md`](architecture.md) and
[`docs/laya.md`](laya.md) for provider-specific wiring.

## Optional Codex adapter qualification

The Codex adapter and the Athena second-harness acceptance path exercise the same
runtime authorities through different hosts. Their qualification evidence is
recorded in the repository's evaluation and qualification reports.

## Private MCP transport

The optional MCP transport is not required for library-only use. When present it
carries host-only control traffic; agent-visible tools do not expose DP.

## Support and limits

* Linux is the supported platform. The repository does not claim Windows support.
* The automated release gate is defined by the sibling CFW repository's
  [`release-gates.json`](https://github.com/B-A-M-N/CognitiveFrameWorks/blob/master/release-gates.json).
* Real-agent efficacy is not established; see the README's status section.

## Optional Codex adapter qualification

CFW can use the separately distributed `cognitive-runtime-codex-adapter` to
connect to the regular packaged Codex CLI through the generic App Server
protocol. The adapter is selected by the trusted host; DP's core does not depend
on Codex or its protocol. The host registers CFW actions, and DP's private
control MCP stays on the host side, outside the model-visible tool registry.
Codex's internal inference and planning boundaries are not intercepted.

The four-defect coding fixture is an integration and non-regression check. Its
baseline reached 4/4 on all 10 trials in the recorded run, so it does not leave
room to measure general behavioral improvement. A later single development triad
run completed 4/4 with 24 host tool calls and 48 CFW gate receipts. It is one
noncanonical sample and does not establish an effect. See CFW's
[`live-results` index](https://github.com/B-A-M-N/CognitiveFrameWorks/blob/master/evaluation_fixtures/live-results/README.md)
for provenance and limitations.

ChatGPT-mode App Server attempts stopped during workspace-routing discovery
before the host or model turn began. The completed direct sample used
FreeInference's external-provider Responses route with the regular packaged Codex
CLI. The recorded host policy exposed zero DP tools to the model. These results
establish integration for the recorded fixture, not behavioral superiority; the
baseline was already at the fixture ceiling. Workspace-routing preflight is
specific to that Codex authentication/provider path; it is not a DP, CFW, or CSW
runtime requirement.

## Second-harness acceptance (Athena)

Run `python scripts/acceptance-athena-adapter.py` to exercise DP's structural
adapter against `AthenaService.in_memory()` through Athena's public task, event,
and result APIs. The script uses Athena's in-memory fake provider and does not
add Athena as a DP dependency. Set `ATHENA_ROOT` if its checkout is not the
sibling `Athena/` directory. This qualifies API interoperability and explicit
capability degradation; it does not claim control parity or behavioral efficacy.
Athena's current public surfaces cannot block actions or intercept completion,
and the local service instance has no independent host attestation, so the
adapter remains unverified during this check.

Codex and Athena demonstrate adapter neutrality at the shared-contract level.
Full-control portability remains unproven until a second harness exposes and
qualifies pre-action blocking, context projection, and completion interception;
any such harness can satisfy that requirement.

## Wiring the trusted runtime host

The host supplies explicit CFW and optional CSW ports to DP. It calls
`RuntimeHost.open_control_bridge(...)` once per scoped task, invokes
`before_tool_planning(...)` before asking the model for an action, and calls
`before_action_candidate(...)` after the host has parsed the proposed action. The
host may also pass a versioned `harness_adapter_identity`; DP persists the pin in
the session descriptor and applies only capabilities backed by a verified adapter
identity. Without a verified identity, the runtime records the adapter
declaration but treats its capabilities as unavailable.

The private MCP transport evaluates only a sanitized `ActionCandidate`. In CFW+DP
mode, pass the original CFW action request separately as `cfw_action_request`;
the bridge sends it only to CFW for a hard preflight and final gate. The returned
CFW action request ID correlates the eventual host tool result. If CFW rejects at
either gate, DP receives the rejection boundary and the tool does not execute.
After each completed host action, call `after_action(...)`. The bridge stays in
the trusted host and is never passed to the controlled model.
`RuntimeHost.open_session(...)` and `RuntimeSession.process_action(...)` remain
available for in-process deployments and compatibility.

For CSW integration without CFW, the trusted host can obtain bindings from CSW's
`cognitive_statework.host_binding.resolve_bindings(...)` and supply those
structural contract pins to DP's `CSWRuntimeAdapter`. CSW performs registry,
scope, and composition checks; DP only ranks the eligible snapshot and submits
requests back through CSW's authority.

For multi-criterion goals, the host sends the complete `GoalSetSnapshot` (bounded
to 256 criteria). DP leases attention to at most three criteria at a time. Its
service bound is `ceil(runnable_count / 3) * 3` actions, so the maximum is 258
only when all 256 criteria are runnable. The prompt may show the current
attention window, while the host keeps the full goal set for completion checks.

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
evidence. Agent claims and changed files alone do not establish success. Targeted
progress can earn credit only when the measured window also accounts for
regressions, newly blocked required work, and attention starvation. Overlapping
interventions remain unattributed when evidence cannot separate their effects.
See [intervention credit assignment](intervention-outcome-credit.md).

## Hard authority boundaries

DP chooses bounded behavioral adjustments from host-supplied eligible choices. It
cannot disable WARD, lower CFW component floors, authorize an action, bypass
CFW's preflight or final action gate, rewrite CSW evidence, or make an illegal
workflow transition valid. CSW validates each workflow activation, release, and
transition against its own registry and revision. The trusted host owns tool
execution and completion evidence. Optional Laya/Jev judgments can inform DP's
choice; they do not grant authority.
