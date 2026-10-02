# Convergence Audit Handoff

**Scope:** DigitalPsychology (DP), CognitiveFrameWorks (CFW), and CognitiveStateWork (CSW).

**Source:** `/home/bamn/.codex/attachments/7b9350eb-bd62-475b-b5ca-b3da2fd5617c/pasted-text-1.txt`.

This handoff records what still needs implementation review, regression evidence, and production qualification. The audit has already driven a large set of changes in all three worktrees. Do not rebuild the host-only control MCP, prepare/apply/commit/reconcile protocol, pre-action boundary, CFW final gate, action-candidate boundary, bounded task set, DP component overlay, CSW lease provenance, replace-only model projection, or the provider boundary. Verify those paths and extend them only when a requirement-level check finds a gap.

## Current state and evidence

- DP source now has bounded candidate generation and selection, explicit abstention/evidence classes, semantic candidate support, ambiguity-driven Laya queries, StateWork preference over eligible choices, mode leases, material-progress attention extensions, starvation-risk calculation, and criterion-free diagnostic candidates. Focused regression tests exist for these behaviors.
- CFW source has a neutral workflow-planner port, resolver responsibility modules, a `runtime_session` package, model-visibility metadata, standalone attention selection, and separate standalone/external behavior authority types. The current edit makes standalone services lazy and lets the DP adapter bind before they are created.
- CSW source has a workflow planner, registry-owned flow contracts/metadata, explicit lease expiry, extracted stores/authority/controller modules, and provider-owned legal composition.
- CFW full-suite run with `COGNITIVE_STATEWORK_ROOT=/home/bamn/CognitiveStateWork`: **139 passed, 1 failed**. The failure was the test that asserts default startup ignores a configured provider, while that environment variable was set. Rerunning that test with the variable unset passed. Package-resource validation passed. The new authority boundary tests passed.
- Previously recorded suite results: DP **151 passed** including the 1000-intervention soak; CSW **31 passed**. Rerun all project suites after final integration changes before sign-off.
- No qualifying real-agent behavioral cohort has been produced. The DP benchmark explicitly returns `qualification_eligible: false`; the CFW external-agent harness is a single fixture task and does not run the full DP/Laya treatment matrix.

Implementation symbols and tests are evidence that work exists; they do not by themselves close the cross-system requirements below.

## Audit issue ledger

The audit's numbered findings are retained here so none disappear during handoff. “Implemented; verify” means the current worktree contains the named design, but the corresponding final-worktree acceptance evidence is still required.

### P0 findings

| ID | Project | Required outcome | Handoff status |
|---|---|---|---|
| P0-1 | DP | Generate several applicable legal behavior candidates outside the fast-loop facade | Implemented in `control/candidate_generator.py`; verify candidate set and bounds |
| P0-2 | DP | Abstain on weak/ambiguous evidence and use an information-gathering fallback | Implemented; verify ambiguous and insufficient-evidence cases |
| P0-3 | DP | Keep unknown evidence unknown; rank with explicit validated rates and conservative bounds | Implemented in candidate evidence/profile contracts; verify receipt and lower-bound paths |
| P0-4 | DP/CFW | Use Laya action/progress relation as advisory candidate evidence; CFW remains final gate | Implemented in semantic scoring; prove before-execution behavior and authority boundary |
| P0-5 | DP/CSW | Let Laya rank only CSW-eligible workflows | Implemented in bounded StateWork selector/frame; verify provider-backed integration |
| P0-6 | DP | Query Laya when candidate ambiguity could change the decision | Implemented through `PolicyAmbiguity`; verify suppression and useful-call cases |
| P0-7 | CFW | Expire only target-scoped controls and preserve unrelated live controls/WARD | Implemented in component overlay/lifecycle; verify partial expiry and outage together |
| P0-8 | CFW | Keep canonical DP state in `RuntimeSession`; store only lease metadata in overlay authority | Implemented; verify no duplicated effective control state remains |
| P0-9 | DP | Apply mode hysteresis with legal hard interruptions | Implemented through `ModeLease`; verify weak oscillation and hard interruption |
| P0-10 | DP | Extend attention only for bounded material progress; rotate on no progress/fairness pressure | Implemented; verify productive extension and cap |
| P0-11 | DP | Score starvation from attention age/fairness bound, not task-set size | Implemented; verify exact age-bound behavior |
| P0-12 | DP/CFW | Allow unbound diagnostic/support candidates without false criterion attribution | Implemented; verify unknown relation remains unknown and mutation stays bound |
| P0-13 | DP | Make the legacy generic MCP entrypoint fail closed unless explicitly enabled | Implemented; rerun installed-entrypoint/launcher check |
| P0-14 | DP/CFW | Enforce that the actual model-facing request contains no DP control/admin tools | Fixed request boundary and regression test exist; production model cohort still required |

### P1 findings

| ID | Project | Required outcome | Handoff status |
|---|---|---|---|
| P1-1 | CFW/CSW | CSW owns eligibility, composition, dependency closure, flows, and packet prerequisites | Provider planner delegates legal composition to CSW; verify CFW only validates/pins results |
| P1-2 | CFW | Describe provider as optional CSW-owned capability, not CFW-owned composition | Contract comment/API now reflects provider ownership; check docs and compatibility surfaces |
| P1-3 | CFW | Decompose resolver responsibilities behind `resolve-runtime.py` compatibility facade | Resolver modules exist; review any remaining substantive logic in the facade |
| P1-4 | CFW | Decompose runtime responsibilities behind `runtime.py` compatibility facade | `runtime_session/` modules exist; review boundaries and public re-exports |
| P1-5 | CFW/DP | Select standalone or external behavior authority without constructing both | Lazy authority boundary added; verify fresh/resumed DP sessions and immutable selection |
| P1-6 | CFW | Bound standalone criterion attention, retry locally, yield to runnable criteria, preserve full completion coverage | `StandaloneAttentionPolicy` is integrated; verify multi-criterion fairness and completion |
| P1-7 | CFW | Declare and enforce component model visibility independently of lifecycle activation | `model_visibility` metadata and projection checks exist; verify dynamic activation cases |
| P1-8 | CSW | Keep `control_plane.py` a facade; stores/authority/controller own their responsibilities | Extracted modules exist and facade is small; review for residual implementation |
| P1-9 | CSW | Make `CompiledRegistry` the single owner of flow contract parsing/metadata | Registry methods and provider delegation exist; verify no adapter duplicate remains |
| P1-10 | DP | Keep `DigitalPsychologyService` an orchestration/composition facade | Service collaborators exist; review remaining substantive behavior |
| P1-11 | DP | Keep `HostControlBridge` a composition facade over lifecycle, transaction, recovery, projection, and degradation collaborators | Collaborators exist; verify full host path still uses them |
| P1-12 | DP | Run in-process and MCP operation through the same host bridge/control protocol | `InProcessControlClient` exists and shares bridge path; retain parity test |
| P1-13 | DP | Cache model projection digest and skip unchanged prompt replacement | Implemented; verify stable digest/no-op test |
| P1-14 | DP | Omit duplicated objective from projection when host context already carries it | Host flag and projection behavior exist; verify both flag settings |
| P1-15 | CSW/DP | Prove DP lease expiry cannot release operator/other-session StateWorks | Ownership metadata and tests exist; run cross-owner integration scenarios |
| P1-16 | CSW | Expire workflow leases through explicit mutation API; keep snapshot reads pure | Explicit expiry operation exists; verify snapshot has no expiry side effect |
| P1-17 | DP | Select semantic criteria by attention, candidate impact, starvation age, and relevance before the eight-item cap | Priority selection/test exists; verify all priority sources and cap |
| P1-18 | DP | Supply truthful task/deliverable kind to semantic provider | Host-derived task/deliverable fields exist; verify non-analysis task kinds |
| P1-19 | DP | Supply truthful optional artifact/verification counters or omit unknown values | Optional counters exist; verify unknown stays null/absent, never synthetic zero |
| P1-20 | DP | Keep StateWork ranking in a selector over legal candidates | `control/statework_selector.py` exists; verify eligibility cannot be expanded by Laya |
| P1-21 | DP | Keep detector/intervention/candidate/directive responsibilities outside the fast-loop facade | Extracted modules exist; review `fast_loop.py` for residual responsibilities |

### P2 finding

| ID | Project | Required outcome | Handoff status |
|---|---|---|---|
| P2 | DP | Use `SemanticProvider` for the interface and `Laya` for its implementation; retain legacy `SystemOne*` names only for compatibility | Still needs public-surface terminology review |

## Continuation record (2026-10-02)

Test commands and observed results for the final three-project worktree:

| Project | Command | Result |
|---|---|---|
| DigitalPsychology | `python -m pytest tests -q` | **159 passed** (was 152; +5 naming, +2 gate) |
| CognitiveFrameWorks | `COGNITIVE_STATEWORK_ROOT=/home/bamn/CognitiveStateWork python -m pytest tests -q --deselect tests/test_p0_boundaries.py::test_cfw_does_not_discover_optional_provider_during_default_startup` | **143 passed, 1 deselected** |
| CognitiveFrameWorks | `env -u COGNITIVE_STATEWORK_ROOT python -m pytest tests/test_p0_boundaries.py -q` | **14 passed** (includes the default-provider assertion) |
| CognitiveStateWork | `python -m pytest tests -q` | **31 passed** |

The one previously reported CFW failure is fully explained and closed. The
deselected assertion reads `COGNITIVE_STATEWORK_ROOT`, so it can only pass when
that variable is unset; the resolver deliberately honors an explicitly
configured root and otherwise refuses to discover an installed provider.
Implementation behavior is correct in both modes.

Do **not** run the whole CFW suite with that variable unset. Most of it resolves
real workflows, so it legitimately fails with `StateWork capability is required by
this task but no registry is installed` (26 failures). The correct split is the
full suite with the root configured, and `test_p0_boundaries.py` on its own
without it. That two-command split is deliberate, not an environment quirk.

### Resolved in this pass

- **P2 naming cleanup (done, committed `d0606f5`).** `SemanticProvider` is now the
  interface name and `LayaProvider` the implementation name.
  `SystemOneBackend`, `LayaSystemOneBackend`, and `BehavioralJudge` remain as
  aliases, so no host is forced to migrate. Schema, protocol-artifact, and
  persisted-event field names are unchanged, so stored receipts still validate.
- **`subsystem_matrix` (done, committed CFW `a9c6eb7`).** New
  `tests/test_subsystem_matrix.py` proves all four supported compositions start
  independently and that each cell never constructs the subsystems it does not
  name. Cells run against isolated durable stores.
- **CFW process-group test race (fixed, committed `a9c6eb7`).** The descendant
  wrote its pid marker non-atomically, so the parent's `exists()` check could win
  before the write landed. It now publishes via `os.replace` and the parent waits
  for parseable content. `PersistentAgent.close()` was already correct.
- **`laya_disabled_parity` and `laya_semantic_gain` (done, committed `d0606f5`).**
  Provider-free determinism and paired baseline/treatment evidence are now
  asserted directly, using the authoritative `BEHAVIOR_MODES` set.
- **`no_cross_implementation_imports` (verified).** Static scan across all three
  projects shows zero implementation-module imports in either direction.

### Real-agent cohort harness (committed CFW `b0222f9`, `3c48673`)

The `real_agent_behavioral_cohort` gate previously had no harness at all. One
now exists and has been exercised against real agents:

- `scripts/cohort/run_cohort.py` runs the benchmark across all five required
  configurations with host-assigned blinded cohort labels, holdout trials
  reported separately from treatment results, and per-task-family aggregation
  of completion rate, evidence quality, latency, and regressions.
- `scripts/cohort/agent_codex.py` drives a real `codex exec` subprocess per turn.
- Evidence comes only from each task module's host evaluator. The report is
  hardcoded `qualification_eligible: false` with recorded blockers, so the
  harness cannot self-certify the gate it measures.

Two adapter protocol bugs were found and fixed before any result was trusted.
The evaluators call `json.loads(response["answer"])`, so the answer must be a
JSON string rather than a nested object. And `execute_benchmark` only ends a
trial on an explicit `complete` flag, so an adapter that omits it burns the
full model-call budget and every trial is recorded as a resource-limit failure.
The first full run scored 0.00 across all 70 trials for exactly this reason
while showing 5-68s of real latency; that run was discarded, not reported.
`tests/test_cohort_harness.py` now pins both protocol facts.

### Cohort adapter defects found by running real agents

Three separate protocol mismatches made the agent look far worse than it was.
Each was found by inspecting per-family results rather than the aggregate,
because the aggregate only showed a uniform zero.

1. **Nested answer object.** The evaluators call `json.loads(response["answer"])`,
   so `answer` must be a JSON string. Emitting an object scored every correct
   trial as a failure.
2. **Missing completion flag.** `execute_benchmark` ends a trial only when
   `response["complete"] is True`. Without it every trial exhausted its
   model-call budget and was recorded as a resource-limit failure. The first
   70-trial run scored 0.00 across all cells while showing 5-68s of real latency.
3. **Discarded workspace and wrong field names.** The harness passes
   `task["workspace"]`, but the adapter dropped it, so a task requiring the
   agent to write `fixed.py` could never pass. Separately, the adapter asked for
   field names several evaluators never read, so correct answers parsed to
   `{}` and scored zero.

The general lesson: a zero that is identical across every configuration is a
harness defect, not a result. `tests/test_cohort_harness.py` now derives its
expectations from the benchmark contract and the evaluator sources, so these
fail in CI rather than as a silent all-zero cohort.

### First real cohort run (2026-10-02, 70 trials) - SUPERSEDED

The results below were produced before the workspace and field-name fixes and
are retained only to document the adapter defects. Do not read them as
behavioral evidence.

#### First real cohort run (2026-10-02, 70 trials)

Command, run from `CognitiveFrameWorks`:

```
python3 scripts/cohort/run_cohort.py \
  --configurations baseline,cfw,cfw_csw,cfw_csw_dp,cfw_csw_dp_laya \
  --trials-per-cell 2 --holdout-fraction 0.5 \
  --report-out build/cohort-report.json --label-out build/cohort-labels.json
```

Five blinded cohorts, 14 real agent trials each, 70 total, evidence from host
evaluators only. Whole-task completion rate:

| Blinded label | Configuration | Treatment | Holdout | Regressions |
|---|---|---|---|---|
| cohort-A | baseline (CFW off) | 0.714 | 0.714 | 4/14 |
| cohort-B | CFW | 0.429 | 0.714 | 6/14 |
| cohort-C | CFW + CSW | 0.571 | 0.571 | 6/14 |
| cohort-D | CFW + CSW + DP | 0.429 | 0.571 | 7/14 |
| cohort-E | CFW + CSW + DP + Laya | 0.714 | 0.714 | 4/14 |

**This run does not support the hypothesis.** There is no monotonic improvement
across the ladder, and cohort-B (CFW alone) scored below the baseline control.
At 2 trials per cell a single flipped trial moves a family rate by 0.5, so
these gaps are noise, not signal. The honest reading is that this matrix is
**inconclusive**, not that CFW or DP harms outcomes, and not that they help.

Two families scored 0.0 in every cell including baseline: `code_repair` and
`multi_agent_delegation`. That is an evaluator/task-authoring gap to resolve
before any comparison between configurations is meaningful.

A further confound: the Laya cell ran with
`DIGITALPSYCHOLOGY_SEMANTIC_PROVIDER=disabled`, because no pinned local Laya
checkpoint is available on this host. Cohort-E therefore exercised the same
composition as cohort-D with no semantic provider and must not be read as a
Laya treatment result.

The gate is still **not** closed. Completion on task outcome alone does not
establish the required whole-task correctness improvement without increased
starvation or mode thrash, and the current evaluators do not measure
starvation, mode thrash, equivalent retries, busywork, redundant tests,
premature completion, operator corrections, or context growth. Closing it needs
those evaluators plus a multi-backend run, not another harness iteration.

### Note on local checkout state

`CognitiveStateWork/state/` holds untracked runtime residue from earlier runs and
is not part of the repository. Its `evidence.json` contains a record whose
`attestation_digest` no longer matches its content, so CSW correctly fails
closed when a host points at that path. This is stale local data, not a defect;
the suite passes because tests use isolated stores. Delete it before running any
harness against the live checkout root.

## Remaining work by project

### DigitalPsychology

- [ ] **P2 naming cleanup:** introduce provider-neutral `SemanticProvider` terminology for new interfaces; keep `SystemOne*` compatibility names where renaming would create churn. Use `Laya` for the implementation name. Search public APIs, schemas, and docs for remaining terminology drift, then update only the non-compatibility surface.
- [ ] **P1-10 facade review:** `DigitalPsychologyService` delegates to extracted attestation, ingestion, prior-store, intervention-event, and experiment services. Review the remaining service code and keep it to construction/orchestration and compatibility forwarding; move any substantive policy or persistence behavior still owned there.
- [ ] **P1-11 bridge review:** `HostControlBridge` delegates lifecycle, transaction, reconciliation, projection, and degradation work to host collaborators. Verify each helper owns its operation and the bridge remains a composition facade; preserve one end-to-end host path for both MCP and in-process clients.
- [ ] **DP-specific regression closeout:** rerun the full suite after cross-project changes, including semantic action relation before execution, ambiguous and insufficient-evidence abstention, statework-fit bounds, projection digest/no-op behavior, task fairness and lease extension, outage lease decay with WARD preserved, restart reconciliation, and the 1000-intervention soak.

### CognitiveFrameWorks

- [ ] **P1-5 authority closeout:** verify the lazy `StandaloneBehaviorAuthority` / `ExternalBehaviorAuthority` selection on fresh and resumed tasks. A DP-bound task must leave the CFW metacognitive controller, semantic store, and System-1 projector unconstructed; standalone use must construct them lazily. Keep the selection immutable once standalone activity begins.
- [ ] **P1-1/P1-2 authority closeout:** maintain the optional neutral provider port. CSW owns workflow eligibility and legal composition; CFW may validate and pin the returned plan but must not recreate StateWork matching, DAG, flow, or packet-prerequisite rules. Keep the provider ownership comment accurate and retain the no-direct-implementation-import test.
- [ ] **P1-3/P1-4 decomposition review:** resolver and runtime-session modules now exist and compatibility entrypoints remain. Review responsibility boundaries and public re-exports; extract only any remaining substantive resolver/session responsibility found by that review.
- [ ] **P1-6/P1-7 behavior proof:** verify standalone attention rotates among all runnable criteria with bounded local retries and complete final coverage; verify `model_visibility` is enforced during prompt construction, including dynamically active components.
- [ ] **CFW regression closeout:** run the complete suite with an explicitly configured CSW provider while excluding the default-provider assertion, then run `test_cfw_does_not_discover_optional_provider_during_default_startup` with `COGNITIVE_STATEWORK_ROOT` unset. Run package-resource validation after the final module changes.

### CognitiveStateWork

- [ ] **P1-8 decomposition review:** `control_plane.py` is now a small facade and stores, workflow authority, planner, controller, and standalone host have extracted modules. Confirm the remaining facade contains no store or registry implementation that belongs in those modules.
- [ ] **P1-9 provider review:** confirm `cfw_provider.py` delegates flow parsing, validation, and metadata to `CompiledRegistry.flow_contract()` / `flow_metadata()` and adds no parallel interpretation of workflow semantics.
- [ ] **P1-15/P1-16 lease proof:** retain explicit `expire_workflow_leases(scope, action_index)` separate from read-only behavioral snapshots. Run ownership-isolation cases: DP expiry removes only DP-owned activation, operator activation survives, and another DP session cannot release it.
- [ ] Rerun the full CSW suite and package/plugin integration checks after any provider-contract change.

## Mandatory acceptance gates

The audit names all 20 gates below. Existing focused tests cover most local invariants; each gate still needs a recorded pass against the final three-project worktree. “Test exists” is not a pass for the integrated matrix.

| Gate | Required evidence | Current disposition |
|---|---|---|
| `pre_action_semantic_interception` | Legal but semantically equivalent failed approach is changed before execution; CFW still decides authorization | Focused DP selector test exists; repeat in integrated run |
| `agent_tool_visibility` | Actual model request/tool registry contains no DP control/admin surface | Fixed-request harness invariant test exists; include in production harness run |
| `projection_replace_only` | 100 revisions leave one DP guidance slot | DP projection regression test exists; include final suite |
| `projection_semantic_stability` | Same semantic guidance does not change the projected prompt on a revision-only update | DP projection no-op regression test exists; include final suite |
| `mode_hysteresis` | Weak oscillating evidence cannot thrash modes; hard interruption still works | DP mode-lease regression test exists; include final suite |
| `partial_cfw_lease_expiry` | Expiring a target preserves unrelated live controls and WARD floor | CFW partial-overlay test exists; include final suite |
| `dp_disconnect_lease_expiry` | Temporary controls decay while DP is unavailable and WARD remains mandatory | Host outage and CFW lease tests exist; verify together |
| `statework_owner_isolation` | DP releases only its own StateWorks; other sessions/operators remain intact | CSW/DP ownership tests exist; verify cross-system path |
| `20_task_fairness` | A stuck item cannot prevent runnable peers receiving attention within the fairness bound | 256-criterion fairness coverage exists; explicitly report the 20-task scenario |
| `productive_focus_extension` | Material progress earns bounded continuation | DP attention extension tests exist; include final suite |
| `microprogress_starvation` | Tiny/non-material progress cannot retain focus indefinitely | DP attention/fairness tests exist; confirm the exact adversarial case |
| `laya_disabled_parity` | Disabling Laya preserves deterministic safety/correctness | Run and record the no-provider parity path |
| `laya_semantic_gain` | Laya improves ambiguous retry/drift decisions without taking authority | DP semantic scoring tests exist; show paired baseline/treatment evidence |
| `statework_semantic_fit` | Laya reorders only CSW-eligible choices | DP statework-fit tests exist; include provider-backed integration case |
| `subsystem_matrix` | Every valid CFW/CSW/DP combination works independently | Partial subsystem tests exist; complete the explicit matrix below |
| `no_cross_implementation_imports` | No project imports another project's implementation modules | Static import test exists in DP; run against all three projects |
| `pre_action_post_action_indexing` | Pre-action preparation/rejection does not advance completed-action index | DP host-bridge regression exists; include final suite |
| `restart_between_apply_commit` | Restart reconciles a prepared/applied transaction without duplicate actuation | DP host-bridge restart test exists; include final suite |
| `1000_intervention_soak` | Hot-path time and intervention history stay bounded | DP soak test exists and previously passed; rerun after final changes |
| `real_agent_behavioral_cohort` | Whole-task correctness improves without increased starvation or mode thrash | **Outstanding: no qualified report** |

### Required production qualification matrix

Run actual agent tasks in each applicable configuration, with host-assigned blinded cohorts, holdouts, independent evidence, and per-trial budgets:

1. Baseline.
2. CFW.
3. CFW + CSW.
4. CFW + CSW + DP.
5. CFW + CSW + DP + Laya.

Report, per cohort and task family: whole-task completion and correctness, untouched required criteria, equivalent retries, busywork, redundant tests, premature completion, operator corrections, maximum starvation age, context growth, tool count, latency, and regressions. The evaluator must use host/validator evidence rather than agent self-report. Separate holdout behavior from treatment success. Do not label the current fake-agent three-system test or single-fixture external-agent run as this qualification.

## Cross-project invariants to preserve

- CFW must not import CSW implementation modules. The host/provider integration is the boundary; CSW owns workflow legality and composition.
- DP/Laya recommends among host-eligible choices. It cannot authorize tools, manufacture CSW eligibility, or lower WARD's floor.
- Runtime state, audit receipts, and model-visible guidance remain separate. Internal revisions and control/admin tool names do not enter model context.
- No accepted outcome or progress claim may be inferred from an agent's self-report; use independent host evidence.
- Keep existing local edits across the three worktrees. Do not broad-revert or redo already implemented architecture.

## Recommended continuation order

1. Close the CFW external-authority regression after the current lazy-injection edit; run the proper CFW suite commands above.
2. Verify DP and CSW full-suite results and map each mandatory gate to a passing requirement-level test.
3. Complete any concrete facade-boundary gaps found in the DP/CSW/CFW reviews; avoid file-length-only refactors.
4. Build and run the actual-agent five-configuration cohort. The existing DP benchmark is explicitly non-qualifying and the CFW fixture harness does not exercise the integrated DP/Laya path.
5. Update this handoff with test commands, report locations, and unresolved failures. The convergence audit remains open until the actual-agent cohort and all mandatory gates pass.
