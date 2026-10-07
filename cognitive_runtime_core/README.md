# Cognitive Runtime contracts

`cognitive_runtime_core` defines the harness-neutral lifecycle, capability,
action-binding, validator, validation-reuse, and completion contracts shared
by the triad. Its core modules own no behavioral policy, framework semantics,
workflow truth, or harness protocol; optional adapters live under integrations.
The runtime normalizes boundaries and coordinates transactions; it does not
reason, choose behavior policy, define workflow truth, or interpret framework
semantics.
`CompletionDecision` aggregates validator evidence
with host-selected authority receipts and fails closed on missing or denied
receipts. The host still checks receipt freshness and identity binding.

The core declares `jsonschema` for generic host-schema validation. It has no
dependency on DP, CFW, CSW, or any harness; Codex remains a separately
installed adapter.

```text
cognitive_runtime_core
  ├─ contracts and lifecycle
  ├─ ports and transactions → CFW, CSW, DP
  └─ integrations           → Codex, Athena, reference, other harnesses
```

Adapters translate their host's events and capabilities into these contracts.
DP, CFW, and CSW keep their own domain behavior and authorities behind
structural ports. The package owns generic transaction sequencing and the
cross-system CFW/CSW apply, rollback, and prepared-decision recovery
coordinator; connected ports supply application-specific projections,
requests, receipts, and DP-owned commit/abort reconciliation. DP retains its
private behavior-decision lifecycle and reconciles uncertain authority
receipts. It builds as the independent `cognitive-runtime-core` 1.5.0
distribution without dependencies on DP, CFW, CSW, or any harness. DP declares
a compatible runtime-core dependency and does not bundle these modules in its
application distribution. The source remains in the authorized DP workspace;
it is runtime plumbing, not a fourth cognitive application. Harness-specific
modules are imported explicitly; Codex is packaged separately, while the
Athena, reference, CFW lifecycle, and triad transaction integrations are
separate from the core contracts. Codex only translates its App Server
boundary into the neutral harness contract; it is not a runtime coordinator
or cognitive authority.

Model-boundary context and validator outcomes are projected as bounded
canonical fields. They contain no host event names or raw action arguments.
Capability gaps are explicit in adapter identity and boundary results; missing
action blocking or completion interception is reported as degraded support.

Benchmark qualification uses `HarnessRequirement` rather than harness-name
switches. The named levels are `OBSERVATION`, `BEHAVIORAL_OBSERVATION`,
`CONTROL`, and `FULL_CONTROL`; qualification checks the verified identity's
effective capabilities and fails before a trial runs when a required boundary
is unavailable. The current Athena adapter reaches behavioral observation.
Codex reaches full control for the exposed task/action/validation/completion
boundaries, without claiming visibility into internal planning.

Each serialized adapter identity records its adapter version, host version,
protocol version, declared capability set and hash, and any harness catalog
pin. Codex pins the reviewed feature-catalog digest; Athena pins its task-port
contract version. Unverified identities expose an empty effective capability
set so runtime claims degrade explicitly.

`EvaluationCohortIdentity` canonically hashes the harness and adapter build,
model and provider pins, feature catalog, reasoning setting, task and binding
digests, CFW/CSW/DP versions and source snapshots, and Laya mode/model/calibration
identity. Unknown values use explicit sentinels, and endpoint values are stored
as digests. A same-cohort comparison must call `require_same_cohort`; different
or missing identities cannot support a causal matched-pair claim.

The old `digital_psychology.runtime` imports remain compatibility shims while
consumers move to `cognitive_runtime_core`. The package name avoids colliding
with CFW's existing `cognitive_runtime` application API.
