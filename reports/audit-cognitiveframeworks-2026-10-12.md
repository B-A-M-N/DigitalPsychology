# CognitiveFrameWorks — Individual Audit Report

**Audit type:** read-only source audit
**Date:** 2026-10-12
**Commit audited:** `49cd4e5` (branch `master`), 488 tracked files, 214 Python / 118 Markdown / 117 JSON
**Test evidence (all verified 2026-10-12):** `tests/` → **141 passed** (34.04 s).
Full combined suite `tests/` + `integration_tests/` with CSW and DP wired for real
(`COGNITIVE_STATEWORK_ROOT=/home/bamn/CognitiveStateWork`,
`PYTHONPATH=/home/bamn/DigitalPsychology`, `TMPDIR=/tmp`) → **366 passed**
(224.17 s). Cross-repo subset — `test_current_cross_repo_wire_contracts.py` +
`test_lease_boundary_contract.py` → **25 passed**.

> **Environment note (not a code defect).** Under Hermes' default
> `TMPDIR=~/.hermes/cache/scratch`, `integration_tests/test_routing_identity.py::test_shared_profile_artifact_requires_host_verifier`
> fails with `RoutingProfileError: routing profile parent directory is not a
> protected trust boundary` (`scripts/runtime_contract/routing.py:356`). The test
> writes to pytest's `tmp_path` under `$TMPDIR`; the parent `~/.hermes/cache` is mode
> `0o775` without the sticky bit, so the trust-boundary check correctly rejects it and
> the test's `except ValueError` never sees the assertion it expects. With
> `TMPDIR=/tmp` (sticky `1777`) it **passes** (13/13 in that file). The security
> control is working as designed.

---

## 1. What CognitiveFrameWorks Is

One sentence: **CFW is the cognitive component and enforcement layer — it answers
"which cognitive components and enforcement rules apply while this agent works,"
and it holds hard authority over whether an action is allowed at all.**

CFW did not start as a framework library. It started from an observation: agent
failures are *recurrent and categorical*. Models repeatedly act before inspecting,
overclaim what evidence proved, widen scope, persist with failed approaches, use
tools unsafely, lose state across long sessions, and communicate with more
confidence than the evidence justifies. CFW's response was to convert each
recurrence class into an explicit, reusable, **enforceable** mechanism.

That framing matters for this audit: CFW's value is not that it gives the agent good
advice. It is that it makes bad action *structurally impossible* regardless of what
the model decides. Advice is advisory; the gate is not.

### The seven frameworks

Each is a self-contained protocol with its own SKILL.md, `runtime.md`, `adapters/`,
`examples/`, and `references/`. Roles are cleanly separated — I verified each owns a
distinct integrity domain with no overlap:

| Framework | Domain | The question it answers |
|---|---|---|
| **OWL** | Reasoning integrity | Is the reasoning sound? (9 principles, silent by default) |
| **ANCHOR** | State integrity | Is operational state coherent across the session? (8 principles) |
| **DOX** | Documentation contract | Is the work contract current? (AGENTS.md hierarchy) |
| **FUSE** | Execution integrity | What to run, how, and what does the evidence prove? (8 principles) |
| **WARD** | Authority & blast radius | Am I *permitted* to, and what happens if I do? (8 principles) |
| **FLOW** | Operational efficiency | Does this change create avoidable drag? (8 principles) |
| **SISPIS** | Communication integrity | How should findings be communicated, calibrated? |

The FUSE/WARD split is the sharpest boundary in the set and is stated exactly:
*"FUSE answers should I call this tool? WARD answers am I permitted to, and what
happens if I do?"* FUSE selects; WARD gates. WARD can veto, constrain, or require
confirmation for a FUSE-selected action. **CONFIRMED** in `WARD/SKILL.md`.

Default kernel observed in the standalone quickstart:
`owl -> anchor_open -> fuse -> ward -> anchor_closeout -> sispis`

### Two-mode discipline (verified across frameworks)

Every framework runs **silent by default** and surfaces only when a signal of
medium-or-higher severity would change what the user does or expects. Explicitly:
*"Surface mode is not a tone shift. It is a notification."* This is a deliberate
anti-noise design and it is uniformly applied. It is also what makes the stack
tolerable in practice — 7 protocols of unsolicited narration would be unusable.

### Architectural positioning (verified)

| Deployment | CFW role | Other-system requirement |
|---|---|---|
| CFW alone | Components, deterministic standalone behavior policy, evidence, action gates | Host supplies task contract |
| CFW + CSW | Task-local components + enforcement | CSW owns workflow state/evidence/transitions |
| CFW + DP | Supplies signals, validates & applies component directives | DP is an optional private host process |
| CFW + CSW + DP | Supplies bounded signals, validates/applies directives | DP coordinates; CSW authorizes |

Critically: **CFW and CSW are optional integrations, not dependencies.** CFW-only
startup does not discover or import an installed CSW provider. A host injects
`WorkflowPort`. This keeps the systems independently testable and independently
deployable — the correct call for a triad.

---

## 2. Where CFW Actually Breaks Loops

This is the user's core requirement, and CFW — not DP — is where the hard
loop-breaking authority lives.

### 2.1 `RetryBoundaryEnforcer` (`scripts/runtime_contract/convergence.py:547`)

The enforcement logic, verified in source:

```python
if state.disposition in TERMINAL_DISPOSITIONS:        return GATE_BLOCK
if state.support_actions_since_goal_progress >= support_action_limit:  return GATE_GOAL_REANCHOR
if state.recovery_epochs_since_goal_progress >= tracker.max_recovery_epochs_without_progress:
    tracker.terminate_cause(cause_id, disposition="BLOCKED", ...); return GATE_BLOCK
unchanged = (action_fingerprint == ... and hypothesis_fingerprint == ... and premise_digest == ...)
if unchanged:
    if state.last_reinspection_revision:              return GATE_CHANGED_HYPOTHESIS
    if state.same_approach_retries >= max_same_approach_retries: return GATE_CHANGED_HYPOTHESIS
    return GATE_REINSPECT
...
if state.same_approach_retries >= max_same_approach_retries: return GATE_CHANGED_HYPOTHESIS
return GATE_ALLOW
```

Four distinct escalation gates, each with a different remedy:

| Gate | Meaning | Escalation |
|---|---|---|
| `GATE_REINSPECT` | Identical approach, not yet at limit | Re-inspect before retrying |
| `GATE_CHANGED_HYPOTHESIS` | Same approach at retry limit, or repeated failure after reinspection | **Must change hypothesis** — retry refused |
| `GATE_GOAL_REANCHOR` | Support-work budget exhausted | Redirect to runnable criteria |
| `GATE_BLOCK` | Recovery epochs exhausted or terminal | Cause terminated `BLOCKED` |

The ladder is the important property. It does not simply cap retries (which breaks
legitimate persistence); it **demands a specific kind of change** at each rung.
`GATE_CHANGED_HYPOTHESIS` is precisely the anti-loop primitive the user wants: the
agent may not repeat, but it *may* continue if it can show the premise moved.

**CONFIRMED, HIGH confidence.** Identity is execution-scoped (`AttemptDescriptor`
keyed by namespace/application-instance/workflow/cause/premise), so independent
agents never share retry budgets — verified separately by
`acceptance-concurrent-agent-isolation.py`.

### 2.2 Durable, crash-safe convergence state

`SharedBranchLedger` persists premise/failure state in SQLite WAL with
`PRAGMA synchronous=FULL` and `chmod 0600`. `_record_convergence()` journals state
so **resume cannot erase the retry budget**. This closes a real and commonly-missed
hole: a loop broken by exhausting a budget would otherwise reset on session resume
and recur indefinitely. **CONFIRMED.**

### 2.3 CFW deliberately does *not* own global adaptation

`RetryBoundaryEnforcer`'s own docstring is explicit: *"This gate can reject an unsafe
or unbounded retry. It does not choose the session's global mode, focus, recovery
strategy, or adaptive behavior."* And `metacognition.py` is reduced to a
compatibility shim that carries **no metacognitive controller at all**:

```python
MetacognitiveState = TaskEvidenceState   # "carries no metacognitive controller or semantic provider state"
```

This is a significant and correct architectural retreat. CFW used to host a
"metacognition" module; it was deliberately gutted and its semantic responsibilities
handed to DP. There is now exactly one owner for global behavior selection. **This is
the single most important structural decision in the triad** and it is what prevents
two systems fighting over the same decision.

Corroborating evidence: `scripts/acceptance-metacognitive-ablation.py` is titled
*"Retired CFW System-1 experiment; semantic qualification belongs in DP."*

---

## 3. The Live-Results Evidence — And What It Actually Shows

`evaluation_fixtures/live-results/README.md` is the most candid benchmark
documentation I have seen in a codebase of this kind. It explicitly declares
**"Canonical results: None yet"** and explains that a result becomes canonical only
after clean working trees and exact commit SHAs. Every row is labelled noncanonical
with its tree state and its claim limitation.

Two rows deserve direct quotation because they are the only real
cross-configuration comparisons that exist:

> `coding-eval-20261006T051814Z.json` — 50 trials; 10 each for baseline, CFW,
> CFW+CSW, triad, triad-multipath; `deepseek-v4-flash`. **Baseline 10/10 at 4/4;
> CFW 7/10 at 4/4 (mean 2.8); each combined condition 10/10 at 4/4.** Dirty trees.
> The three CFW-only misses scored 0/4 and ended with `require_goal_reanchor`...
> The normalized artifact records zero gate receipts while a legacy invariant says
> `cfw_action_gate_observed=true`, so gate execution is not substantiated by receipts.

> `coding-eval-20261005T204440Z.json` — 40 trials; `gpt-6-luna`. **Baseline 10/10,
> CFW 6/10**, combined conditions 10/10 at 4/4. "Historical score and
> completion-policy evidence; not a clean comparison."

**Read this carefully: across two independent cohorts and two different models, the
unaided baseline scored 10/10 and CFW-only scored 6–7/10.** The combined
configurations recovered to 10/10.

This is not evidence that CFW hurts — the documentation correctly identifies a
ceiling effect (baseline at fixture maximum leaves no headroom) and notes the
three failures all terminated via `require_goal_reanchor`, i.e. **CFW's own
loop-breaker firing correctly**. But it is also *not* evidence that CFW helps. What
it establishes is:

1. CFW's gates are **live and blocking** (they demonstrably stopped runs).
2. CFW's loop-breaking ladder **engaged exactly as designed** under retry pressure.
3. **The fixture cannot measure improvement** because the baseline is saturated.

And one honest defect the author flags against himself: the artifact recorded zero
gate receipts while a legacy invariant asserted `cfw_action_gate_observed=true` —
*"gate execution is not substantiated by receipts."* Volunteering that a claimed
invariant is unproven by the artifact is the correct instinct.

### The benchmark was measuring nothing (Pass 5)

From DP's HANDOFF, verified against `scripts/cohort/runtime_harness.py`:
`acceptance-real-agent-benchmark.py` spoke JSON to an agent subprocess and **never
constructed CFW or bound DP**. Every earlier cohort run set only environment
variables. Those runs measured the model, not the systems. The author discarded them
as non-measurements rather than reporting them as evidence.

The fixed harness now builds real compositions (`baseline` / `cfw` / `cfw_csw` /
`cfw_csw_dp`), and the Laya arms are properly gated — a configuration without live
provider attestation is `INVALID_TREATMENT`, not a silent zero.

### Difficulty screening (a genuinely good practice)

`scripts/cohort/probe_difficulty.py` runs a candidate family against the real
unassisted agent and reports DISCRIMINATES or SATURATES **before** it is registered.
Four candidates screened; three saturated and were discarded:

| Candidate | Baseline | Outcome |
|---|---|---|
| `regression_trap` | 3/3 | saturated, discarded |
| `repeated_failure_trap` | 3/3 | saturated, discarded |
| `decline_available_action` | 3/3 | saturated, discarded |
| `irreversible_side_effect` | 1/3, then 2/3 | **kept** |

Rejecting families that would inflate the benchmark's apparent rigor while adding no
discrimination is exactly right, and rare.

### The real blocker

> After hours of continuous cohort runs, `codex exec` began returning `429 Too Many
> Requests`... **No cohort run can produce valid evidence while this holds.**

A rate-limited backend was being scored as an agent failing the task. Now flagged
`infrastructure_failure` and excluded from the hard-zero tripwire. **This is
currently the binding constraint on producing any comparative evidence at all.**

---

## 4. Loop-Causing Risk Assessment for CFW

| Risk | Mechanism | Mitigation | Residual |
|---|---|---|---|
| Escalation ladder too aggressive | `GATE_CHANGED_HYPOTHESIS` refuses retry; if hypothesis-change detection is over-eager, productive persistence is blocked and the agent thrashes between blocked alternatives | 4-rung ladder demands specific change, not a blunt cap; `max_same_approach_retries` default 2 | Low–moderate. Unmeasured: the CFW 7/10 cohort is the only real pressure test and it is confounded by the ceiling effect. |
| Progress-redirect gate churn (historical, fixed) | An identical action was blocked without restating the redirect, forcing rotation | Bounded: identical action blocked without restating; second distinct attempt yields to another criterion; first action on new focus allowed once | **Fixed.** Focused suite 79 passed. |
| Lease boundary off-by-one across systems (historical, fixed) | CFW treated `expires_at_action = N` as valid at N; CSW treated N as expired. A framework lease could hold a control one action after its StateWork released | CSW adopted CFW's inclusive convention; `test_lease_boundary_contract.py` drives real CFW overlay + real CSW authority across actions 9–13 | **Fixed and cross-tested.** This was a genuine cross-system defect. |
| DP↔CFW policy conflict | DP emits `REPLAN`; CFW returns `require_changed_hypothesis` | CFW retains hard authority; DP receives bounded rejection | Low — CFW wins, so no unbounded loop. See cross-system report. |

---

## 5. Assessment

**Sophistication: PRODUCTION-GRADE.** The seven frameworks are coherent, mutually
non-overlapping, individually complete with adapters/examples/references, and
uniformly disciplined about silent-by-default operation. The enforcement layer is
real: hard gates, durable convergence ledgers, execution-scoped identity, and a
four-rung escalation ladder that demands specific change rather than imposing blunt
caps.

The most important thing CFW does for the user's goal is **what it refuses to do.**
By gutting its own metacognition module and delegating all global adaptation to DP,
CFW guarantees exactly one owner for behavior selection. A triad where two systems
both believed they owned the mode would oscillate by construction; this one cannot,
because the second owner was deliberately deleted.

The second most important thing is that CFW's gates demonstrably fire under real
pressure — the 6–7/10 CFW-only cohorts are, read correctly, *proof the loop-breaker
works*, not evidence of harm.

**The gap:** there is still no canonical result, and the binding blocker is external
(backend 429s), not architectural. The harness is now correct and the benchmark
contains one family that discriminates. What is missing is a run.

---

## 6. Findings

| # | Severity | Finding | Evidence |
|---|---|---|---|
| CFW-1 | **P1** | No canonical result exists in any cohort. The only cross-configuration comparisons are noncanonical, dirty-tree runs on two different models, and both show a saturated baseline. The comparative efficacy of the triad is **unmeasured**. | `live-results/README.md` "Canonical results: None yet" |
| CFW-2 | **P1** | Historical CFW-only underperformance (6–7/10 vs baseline 10/10) remains causally undiagnosed because the failed artifacts lack action-level receipts. Recorded as `INCOMPARABLE_COHORT`. Correctly not attributed to a cause. | HANDOFF blocked-state table; live-results table |
| CFW-3 | **P2** | A legacy invariant asserts `cfw_action_gate_observed=true` while the normalized artifact records **zero gate receipts**. Gate execution is not substantiated by the artifact in that run. | live-results row, self-flagged |
| CFW-4 | **P2** | Current cohort runs are blocked by external backend rate limiting (`429`). No valid comparative evidence can be produced until it recovers. | HANDOFF "BLOCKER: agent backend rate limited" |
| CFW-5 | **Info** | `metacognition.py` is a deliberate no-op shim; `acceptance-metacognitive-ablation.py` is retired. Correct single-ownership decision, but the naming now misleads a reader into expecting metacognitive logic in CFW. | Source inspection |

**Assessment of what is *not* broken:** the framework separation, the silent-by-default
discipline, the FUSE/WARD authority split, the four-rung retry ladder, durable
crash-safe convergence state, execution-scoped identity, optional-dependency
isolation, and the honesty of the benchmark documentation are all sound. CFW's
enforcement layer is the strongest of the three systems.

---

**END OF CognitiveFrameWorks INDIVIDUAL REPORT**
