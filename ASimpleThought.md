# A Simple Thought

**A read-only audit of DigitalPsychology, CognitiveFrameWorks, and CognitiveStateWorks,
and a grounded answer to one question: how do we make Laya meaningfully contributive
to deterministic, context-sensitive agent steering — without the triad causing the
loops it is meant to break?**

Audit date: 2026-10-12
Commits: DP `12cf70e` · CFW `49cd4e5` · CSW `fe64e2c`
Individual reports: `reports/audit-digitalpsychology-2026-10-12.md`,
`reports/audit-cognitiveframeworks-2026-10-12.md`,
`reports/audit-cognitivestateworks-2026-10-12.md`

---

## The short version

You already built the meta-meta-cognition system. It works, it is unusually
well-engineered, and its safety properties are real and enforced in code rather than
documented as intent. **What you have not built is any evidence that it helps** —
and the reason is not architectural. It is that the benchmark could not measure it,
the semantic layer is inert by construction, and the agent backend is currently
rate-limited.

Three findings drive everything that follows:

1. **Laya is structurally incapable of changing a DP decision when a deterministic
   rule applies.** The scoring ceiling is `0.35 + 0.40 × support ≤ 0.75`; a
   deterministic-rule candidate scores `0.82`. Verified empirically: **0 wins in 6
   configurations** against the real production selector.
2. **Advisory mode is unreachable.** It requires an active calibration record that
   does not exist on this host. Shadow is the effective ceiling, so no Laya
   influence can ever be observed in production.
3. **The triad's loop-breaking is genuinely strong — and it is stronger *because*
   Laya is currently inert.** The loop-break signals are host-fact-derived and
   unwritable by any semantic provider. That is the property to preserve.

The uncomfortable conclusion, stated plainly because you asked for the truth rather
than encouragement: **making Laya "positively meaningful and substantially
contributive" is not an integration problem. It is a measurement problem that has
not been solved, and your own repositories already contain the evidence that says
so.** The most valuable thing I can do here is not to propose a clever wiring. It is
to tell you which part of the goal is achievable now, which part is blocked on
something that cannot be coded around, and what the smallest honest next step is.

**Part 5 adds the reachability finding**: influence is not merely blocked by model
quality — exactly one legal influence path exists, and it is currently closed by an
activation gate rather than by scoring.

---

## Part 1 — What the triad actually is

Read together, the three systems are not three features. They are **three different
kinds of authority**, deliberately separated so that no two of them can want the
same thing.

| System | Kind of authority | The question it answers | Can it say no? |
|---|---|---|---|
| **CFW** | Enforcement | Which cognitive components and rules apply; is this action allowed? | **Yes** — hard gates, 4-rung retry ladder |
| **CSW** | State | What state is this work in; what transition is legal; what evidence is required? | **Yes** — illegal transitions are simply not legal |
| **DP** | Adaptation | Which bounded behavior change, under this condition, actually helped? | **No** — advisory only, bounded, reversible |
| **Laya** | (intended) Semantic judgment | What does this situation *mean*, where host facts are ambiguous? | **No** — and currently cannot even influence |

That table is the whole architecture. The triad works because **exactly one system
owns each kind of decision**, and because the adaptive layer is the one with no
authority to abuse.

The strongest evidence this was deliberate rather than accidental: CFW **deleted its
own metacognition module.** `scripts/runtime_contract/metacognition.py` is now an
18-line compatibility shim whose docstring says it "carries no metacognitive
controller or semantic provider state," and `acceptance-metacognitive-ablation.py`
is titled "Retired CFW System-1 experiment; semantic qualification belongs in DP."
A triad where two systems both believed they owned the cognitive mode would oscillate
by construction. This one cannot, because the second owner was removed.

That is the single most important structural decision in the three repositories, and
it is what makes the rest of this document possible.

---

## Part 2 — How the three compose (verified, not inferred)

I traced the full path and confirmed the authority boundaries hold in code, not just
in documentation.

```
Host observation (trusted, outside the model's reach)
   │
   ├─► CSW  ──► eligible StateWorks, legal transitions, evidence validity
   │            (DP may only REORDER the eligible set; never expand it)
   │
   ├─► CFW  ──► component floors, hard action gate, retry ladder
   │            (WARD can veto; FUSE selects but cannot authorize)
   │
   └─► DP   ──► evaluate_conditions()      [pure host-fact detectors]
                 InterventionLifecycle    [expire / rollback / anti-thrash]
                 CandidateGenerator       [bounded legal candidates only]
                 PolicySelector           [rank + explicit abstain]
                 DirectiveBuilder         [→ CFW/CSW ports]
                 InterventionEvaluator    [host evidence only]
```

Three properties I verified rather than assumed:

**DP's control plane is invisible to the model.** DP's private control MCP is
launched as a host-side child and DP *rejects* any tool registry that would expose
it. Runtime orchestration is deliberately withheld from the model's tool list. An
absent or partial registry cannot prove hiding, so DP fails closed. **CONFIRMED.**

**Agent self-reported success is not evidence.** `lib/outcomes.py` accepts only
scoped, post-intervention host/CSW/validator evidence plus an exact
applied-directive receipt. An agent claiming it succeeded contributes nothing to its
own evaluation. This is the correct primitive and it is the reason the slow loop is
trustworthy at all.

**Trajectories are not double-counted.** Repeated events from one trajectory cannot
become independent samples. A subtle methodological safeguard that most systems of
this kind get wrong.

I also ran the cross-repo suite with all three systems wired for real —
`integration_tests/test_current_cross_repo_wire_contracts.py` and
`test_lease_boundary_contract.py` → **25 passed**. That is genuine evidence that the
composition works at the wire boundary, which is more than the HANDOFF claims for
most of its subsystems.

**Full-suite results (all verified 2026-10-12, `TMPDIR=/tmp`):**

| Repo | Command | Result |
|---|---|---|
| DP | `pytest tests/` | **459 passed, 6 skipped** |

The 6 DP skips are opt-in local-checkpoint tests in
`tests/test_system_one_laya_local_robustness.py` — deliberate gates so an ordinary
test run never starts model-backed work. They cannot run today anyway, since DP-2
means no calibration bundle exists to run them against.
| CFW | `pytest tests/ integration_tests/` with CSW root + DP on path | **366 passed** |
| CSW | `pytest tests/` | **63 passed** |

No code defects surfaced in any suite. The only failures observed anywhere were
three tests that write to pytest's `tmp_path` and trip DP/CFW's
`RoutingProfileError: routing profile parent directory is not a protected trust
boundary` — because Hermes' default `TMPDIR` (`~/.hermes/cache/scratch`) sits under
a `0o775` non-sticky parent. Under `TMPDIR=/tmp` all pass. **The security control is
firing correctly in both repos**; worth knowing if you run these suites from an
agent shell rather than a terminal.

---

## Part 3 — The loop question, answered with evidence

You asked for two things that pull in opposite directions: break the agent out of
loops, and do not cause loops. Here is what the code actually guarantees.

### 3.1 Breaking loops: two independent mechanisms, both real

**CFW's four-rung escalation ladder** (`RetryBoundaryEnforcer`) is the hard
authority. It does not cap retries — it demands a specific kind of change at each
rung:

| Gate | Trigger | Required remedy |
|---|---|---|
| `GATE_REINSPECT` | Identical approach, under limit | Re-inspect before retrying |
| `GATE_CHANGED_HYPOTHESIS` | Same approach at limit, or repeat failure after reinspection | **Must change the hypothesis** — retry refused |
| `GATE_GOAL_REANCHOR` | Support-work budget exhausted | Redirect to runnable criteria |
| `GATE_BLOCK` | Recovery epochs exhausted / terminal | Cause terminated `BLOCKED` |

This is the right shape. A blunt retry cap breaks legitimate persistence; a ladder
that demands *"show me the premise moved"* breaks loops while permitting recovery.
Convergence state is journalled so **resume cannot erase the retry budget** — closing
the hole where a broken loop silently resets and recurs.

**DP's condition-scoped intervention history** is the second mechanism. I probed the
real predicate:

| Scenario | Result | Verdict |
|---|---|---|
| Same cause, same `condition_id` re-triggered | blocked | **loop broken** |
| Same cause, **new** `condition_id` (host reported new hypothesis) | not blocked | **recovery allowed** |
| Failed on an *unattended* criterion | not blocked | **scope-correct** |

Plus three independent latch-closers: **mode hysteresis** (a mode is held until a
*specific* exit condition is met, with a minimum-action floor, releasable early only
by a hard interrupt); **self-expiring leases** (every intervention carries
`expires_after_actions`, bounded 1–16); and **inconclusive ≠ terminal** (partial
evidence keeps an intervention live to its declared horizon, so a missing fact cannot
trigger premature rollback — which would itself oscillate).

**Verdict: loop-breaking is genuinely solved. CONFIRMED, HIGH confidence.** And the
best evidence is not my probe — it is the live-results table. The historical CFW-only
runs that scored 6–7/10 **all terminated via `require_goal_reanchor`**, i.e. CFW's
own loop-breaker firing exactly as designed under real retry pressure.

### 3.2 Not causing loops: currently guaranteed, for two independent reasons

**Reason 1 — the ceiling.** Laya cannot outrank a deterministic-rule candidate
(0/6 configurations, verified). Its ranking influence is capped at 0.75 < 0.82.

**Reason 2 — the loop-break signal is unreachable by any provider.** The three
inputs gating `previously_failed` are: (a) `item.outcome`, set by
`InterventionEvaluator` from **host evidence only**; (b) `item.condition_id`, derived
by `identify_condition()` from goal set / CSW snapshot / evidence refs / approach
epoch; (c) `attention_ids`, from the host-owned attention lease. **None is writable
by a `SemanticProvider`.** Laya cannot forge a loop-break, and cannot suppress a
genuine one.

**This is the property to protect above all others.** Everything in Part 5 is
designed so that making Laya useful does not break it.

### 3.3 The honest risk register

These are latent, not active. They are the price of making Laya matter, and any plan
that does not name them is not a plan.

| Risk | Mechanism | Residual |
|---|---|---|
| Semantic→StateWork flip-flop | `rank_stateworks` reorders at ≥0.80 confidence. A near-uniform binary model crossing the threshold intermittently would alternate workflow selection across ticks. | **Real.** `route_preference` was measured at raw p=0.50/0.66; Stage-B failed its confidence-separation floor (margin 0.121 < 0.15). The gate is close to the noise floor. |
| Unvalidated support mapping | `_semantic_support()` converts labels to alignment/risk scores using behavioural assumptions with no outcome validation. | **Real but bounded** by the ceiling today; unbounded if the ceiling is raised. |
| DP↔CFW policy conflict | DP may emit `REPLAN` while CFW returns `require_changed_hypothesis`. | Low — CFW wins, so no unbounded loop. |
| Inference latency in the control path | Measured p50/p95: action 6.9/8.2 s, progress 8.8/13.8 s, retry 8.5/9.0 s, route 2.8/4.0 s; warmup 5.6–22 s. | Moderate. Fine for low-frequency advisory use; **not** per-action real-time. |
| Evidence flakiness → CSW reopen oscillation | Invalidation reopens a completed workflow. Depends on host validator stability, outside CSW's control. | Low but **unmeasured**. |

---

## Part 4 — Why Laya is currently inert (and why that is not a wiring bug)

This is the part most likely to be misread as "the integration is broken." It is not.
The integration is the best-controlled component in the system. The *model* is the
problem, and your own decision record already proved it.

### 4.1 The wiring is excellent

- **Provider-neutral by construction.** A `SemanticProvider` Protocol; `LayaProvider`
  is one implementation. **No static `import laya` anywhere in DP source** — the
  package loads only when a provider is constructed, so normal DP startup never
  imports Laya, PyTorch, or weights.
- **Integrity pinned before load.** Revision + adapter content hash + weight hash,
  computed over the exact checkpoint, config, and tokenizer inputs. Symlinks
  rejected.
- **16-field response validation** (identity, sequence, state digest, question
  bundle, protocol hashes, backend/model/revision/content, calibration bundle,
  protocol version). A mismatch is a `provider_contract_error` that **does not open
  the shared circuit breaker** — so one agent's malformed response cannot disable
  another agent's calls.
- **Three modes with correct fail-safe.** `advisory` demotes to `shadow` unless the
  backend is verified AND `advisory_eligible is True` AND every requested family is
  calibrated.

### 4.2 The model is measured at or near chance on four of five families

The checkpoint is a 421M ModernBERT-large, fine-tuned **1 epoch** on **four
synthetic workflows** (invoice processing, security incidents, customer service,
agent-trace observability). Metadata-blinded results:

| Family | Result | Note |
|---|---|---|
| `action_relation` | 1/5 | probs ≈ uniform 0.29–0.31 |
| `progress_relation` | 1/6 | collapses to `verification_progress` |
| `retry_relation` | 1/4 | collapses to `equivalent_retry` |
| `goal_drift` | **0/2 drift recall** | the exact ability the question exists for |
| `route_preference` | 2/2 | but raw p = 0.50/0.66 — near coin-flip |

**Input defects were disproven as the cause.** 399–758/1024 tokens used, **0
truncated, 0 dropped, 0 collapsed option spans** across all 19 retained projections.
Prompt variants (binary, compact, hierarchical, supplied-claims, `noul`) all failed
to repair discrimination. The discriminating facts are present in the frame; the
model does not leverage them. Its own README says: *"This is a specialist… Expect it
to behave like the base laya checkpoint, or worse, on anything else."*

**This is a model-capability failure, not an adapter defect.** No amount of
integration work changes it. That is the finding that should govern the plan.

### 4.3 Two corrections to your own records

I am flagging these because you asked for evidence over agreement, and because both
change what you would do next:

- **The pinned checkpoint is NOT absent.** `HANDOFF.md` states the pinned
  runtime/checkpoint is missing and "supersedes" the earlier environment note. That
  is **stale**. `~/.cache/laya/convaiinnovations-laya-typed-decisions-1a793eb/model.safetensors`
  exists on disk. What is missing is the **calibration bundle**, not the checkpoint.
  *This downgrades an earlier recorded verdict in your own handoff, and it matters:
  the blocker is a data problem, not a download problem.*
- **`_ROLE_UTILITY` does not exist in this checkout.** The decision record attributes
  the unvalidated label→utility mapping to that symbol in `candidate_generator.py`. At
  `12cf70e` the equivalent logic is inline in `_semantic_support()`
  (`candidate_generator.py:316-360`). The finding survives; the named symbol does not.
  Anyone grepping for `_ROLE_UTILITY` will conclude the issue was fixed when it was
  only moved.

---

## Part 5 — Decision-Influence Reachability: where Laya can actually act

Parts 1–4 established that Laya has no behavioral influence today and that the
cause is a model-capability failure. That framing was incomplete. It conflated two
independent constraints — *is influence architecturally permitted?* and *is the model
good enough to use it?* — and answered only the second.

This part separates them. It enumerates every legal path from a Laya judgment to an
authorized DP decision, at the pure decision-function level, and records for each
whether influence is possible at all. The distinction that matters throughout:

> **No influence because the model is wrong** is a data/training problem.
> **No influence because the architecture forbids it** is a design decision.
> These have different remediations, and conflating them wastes effort.

### 5.1 The reachability matrix

Probe: `probe_reachability_matrix.py`, against production `BehaviorCandidateGenerator`,
`BehaviorPolicySelector`, and `rank_stateworks`.

| # | Path | Status | Evidence |
|---|---|---|---|
| 1 | `semantic_evidence` → candidate ranking | **Closed by scoring** | Max semantic score `0.35 + 0.40·1.0 = 0.75` < deterministic floor `0.82`. Verified across contradiction / retry / task_complete / ambiguous: **0 of 4** conditions had any semantic input change the selection. |
| 2 | `semantic_statework_preference` → `rank_stateworks()` | **Open by scoring** | `conf=0.79` → no change. `conf=0.80 / 0.95 / 1.0` → `('repair','verify')` becomes `('verify','repair')`. |
| 3 | Abstention override (`has_decision_evidence`) | **Closed by construction** | Abstention requires continuation + fallback + *no* decision evidence. Every condition emitting `abstention_fallback` also emits `deterministic_rule`, so `has_decision_evidence` is always True. **There is no abstention to flip.** |
| 4 | Engine activation gate (`engine.py:191`) | **Binding constraint today** | `judgment = result.judgment if status == "ok" else None`. Shadow → `judgment = None` → `decide()` never sees it. |

**Correction to my own earlier claim.** In the previous revision I asserted that
abstention override was a reachable path. That was wrong — I inferred it from reading
the abstention branch and never tested whether abstention is reachable. It is not. The
finding is recorded here as corrected, not quietly dropped.

### 5.2 Is a reordered list a behavioral intervention? (the load-bearing question)

A reordered preference list is not automatically consequential. If both StateWorks
eventually execute identically, Laya's influence would be operationally
insignificant regardless of scoring. I traced this end to end rather than assuming.

**The path terminates in real activation.** `DirectiveBuilder` emits a
`prefer_statework` directive → `ControlActuator` validates eligibility against the
live CSW snapshot → `CSWRuntimeAdapter.activate_statework()` → CSW's
`WorkflowAuthority`. The actuator then *verifies* the effect and raises if CSW did
not confirm:

```python
snapshot = self.csw.activate_statework(...)
if statework_id not in snapshot.active_stateworks:
    raise RuntimeError("CSW did not confirm StateWork activation")
```

**Reordered StateWorks are genuinely different programs.** From the bundled
`transitions.yaml` files — distinct states, triggers, and evidence gates:

| Bundle | Distinct `required_evidence` sets | Example gate |
|---|---|---|
| `getter` | 6 | `[intent, risk]` to advance PLANNED→CHANGING |
| `gitter` | 3 | `[repository_truth]` to reach a terminal state |
| `infrae` | 16 | `[plan, invariants, rollback]`, `[change_gate, authority]` |

Selecting `getter` over `gitter` therefore changes **what evidence gates progress**,
not merely an ordering label. Activation is behaviorally consequential. **CONFIRMED.**

### 5.3 Two enforcement gaps found during the trace

**Gap 1 — the experimental route path bypasses the 0.80 confidence gate.**
`directive_builder.py:162-167` passes `semantic_confidence=1.0` unconditionally for
`experimental_route_preference`, so the ≥0.80 threshold is trivially satisfied and
cannot reject anything. The semantic path (line 171-176) correctly passes the real
confidence. The experimental value is host-owned (`policy_context`, not Laya), so
this is not a Laya escalation — but it means the confidence gate provides **no
protection** on that path, and it is the path a cohort experiment would use.

**Gap 2 — no margin requirement on route reordering.** `rank_stateworks` reorders on
threshold alone. A `0.50/0.50` calibrated distribution at confidence ≥0.80 reorders
just as readily as a decisive one. The selector's `minimum_candidate_margin` (0.10)
applies to *candidate* selection and explicitly exempts `deterministic_rule` and
`validated_experiment` — it does not cover route preference. Stage-B already failed
on exactly this (margin 0.121 < 0.15); the margin is not currently enforced anywhere
in code.

### 5.4 What this does and does not establish

**Established:** candidate ranking is closed to semantic influence by a
model-independent inequality; abstention override is closed by construction;
StateWork reordering is open by scoring, terminates in verified CSW activation, and
changes which evidence gates progress.

**Not established:** that StateWork reordering is the *only* path in the entire
runtime. This matrix covers the examined paths and configurations. It is not a proof
of uniqueness across every integration surface.

**Also not established:** that the current model can use Path 2. Architecture and
model quality are **independent constraints**. The architecture permits the path; the
model has not demonstrated it can drive it. Stage-B measured `n=2` with margin
0.121 — that is insufficient to establish dependable accuracy *or* dependable
failure. Two examples cannot characterize a distribution.

### 5.5 Architectural Decision Record

**ADR — Laya Semantic Influence Boundary**

- **Retain deterministic decision authority.** Do not raise the `0.82` floor. It is
  the mechanism enforcing augmentation-not-replacement.
- **Retain the existing shadow gate** until independent evidence exists.
- **Treat `route_preference` StateWork reordering as the only currently demonstrated
  potentially reachable advisory mechanism, pending downstream execution
  verification.** Verified consequential by §5.2; not yet verified *useful*.
- **Qualify checkpoint/calibration/family combinations, not families permanently.**
  A family failing today is not disqualified forever — new weights, training data, or
  representation changes may rehabilitate it.
- **Require evidence of incremental behavioral utility** before enabling advisory
  influence.
- **Preserve the other four families for diagnostic research** without expanding the
  initial activation scope.
- **Close Gap 1 and Gap 2** (§5.3) before any advisory activation, since both weaken
  the only open path.

### 5.6 Revised sequence

The previous sequence put model work first. That is now demonstrably premature: with
exactly one open path, a five-family qualification program would spend most of its
effort measuring families whose results cannot affect any decision.

| Phase | Deliverable | Gate |
|---|---|---|
| **P0-A** | Reachability matrix *(done, §5.1)* | ≥1 reachable permitted effect, or explicit confirmation none exists |
| **P0-B** | **End-to-end StateWork consumption trace** *(substantially done, §5.2)* | Prove reordering changes an externally observable result |
| **P0-C** | Close Gap 1 + Gap 2; pin shadow inertness and activation boundaries with tests | Threshold and margin enforced on the real integration path |
| **P1** | Independent semantic targets + hard adversarial benchmarks, **`route_preference` only** | Labels without controller-decision leakage |
| **P1** | Discrimination by checkpoint/family; error **complementarity** vs the controller | Predictive value on held-out cases |
| **P2** | Calibrate the qualified combination | Confidence reliability + bounded applicability |
| **P2** | Qualified advisory influence on Path 2 only | Proven authorized decision changes, invariants intact |
| **P3** | Long-horizon treatment evaluation | Net improvement in completion, recovery, stability, intervention cost |

Three evaluation observations must be kept separate — collapsing them is what makes
a corpus circular:

1. **Semantic target** — what the situation represented, independently labeled.
2. **Controller decision** — what the deterministic system selected, and why.
3. **Observed outcome** — what actually happened, against independent criteria.

A host-authoritative outcome is valid evidence of usefulness even when the controller
generated the action. Circularity arises only when the controller's own
*interpretation* is used as the ground-truth label.

### 5.7 The decision this forces

> **Is StateWork reordering alone a sufficient influence surface?**

**If yes** — the program is small and decidable: qualify `route_preference` to an
explicit statistical bar (calibrated accuracy, margin, and sample counts with stated
dataset roles, independence requirements, and acceptance criteria), close Gaps 1–2,
then enable the gate for that family alone. Weeks, with a clean pass/fail.

**If no** — making Laya consequential requires changing DP's decision authority,
which is the danger named throughout this document. The legitimate options are a
deliberately designed new influence path (semantic veto on a proposed intervention,
or tie-break where the controller is genuinely indifferent) or accepting Laya as a
telemetry asset.

The one change that is not on the table is raising `0.82`. It is a one-character edit,
it would make a weak model consequential, and it would break the augmentation
boundary that currently makes the system safe.

**Residual risk if the answer is "no":** Laya would have a technically reachable
influence mechanism without an operationally consequential one. That distinction
determines whether the next investment belongs in the model, its integration, or the
architecture itself — and it is the finding this trace was built to expose.

---

## Part 6 — What I would actually do

Ordered by evidence-per-unit-effort. The first item is not optional and is not a
preliminary.

### Phase 0 — Make the benchmark discriminative. Nothing else is worth doing first.

Your own Pass 5 reached this conclusion and then got blocked. Restating it because it
is the load-bearing constraint: the real 140-trial cohort showed baseline 0.952,
CFW 0.952, CFW+CSW 0.952, CFW+CSW+DP 0.905. **Five of seven families scored 1.00 in
every cell.** No behavioral policy has room to improve a task the model already
solves. You correctly declined to call 0.905-vs-0.952 a regression (it is one trial),
and you correctly declined to call the benchmark discriminative.

You also correctly rejected three harder families that saturated, and kept only
`irreversible_side_effect` (1/3 → 2/3, genuine variance). That instinct — discard
what saturates rather than pad the benchmark — is the right one and should continue
to govern.

**The work:** author more families in the shape of `irreversible_side_effect`, i.e.
pressure the model does *not* already handle, each screened by
`probe_difficulty.py` before registration. The hint in your own notes is the right
one: *a trap only visible after a side effect is committed, or a task whose correct
answer requires declining an available action.*

**Acceptance:** ≥3 families where the unassisted baseline scores below 1.00, with
run-to-run variance, in the same cohort. Until then, every efficacy claim is
unfalsifiable.

**Blocker:** the agent backend is returning `429`. No cohort run produces valid
evidence until a bare `codex exec` returns a real reply. This is external and cannot
be coded around. Also note the harness now correctly flags empty replies as
`infrastructure_failure` rather than scoring them as agent failure — that fix is
essential and already in place.

### Phase 1 — Collect labels. This is the real prerequisite for Laya.

Shadow mode is already the right operating mode, and it is already generating the
only asset that matters: **frames paired with host-authoritative outcomes.** The
decision record notes "3 labels total." The qualification gates require ≥100
independent outcomes, ≥30 independent trajectories, ≥20 per work-mode/deliverable
cohort **per family**.

Concretely, and this needs no new architecture:

1. Run the (now-discriminative) cohort with Laya in **shadow** across all five
   families. Shadow inference already happens; it is currently discarded.
2. Persist every frame with its host-authoritative label. You already have the
   durable-evidence plumbing (`256 KiB` bound per attempt, digest-verified replay).
3. Build calibration records from that corpus. **This is what makes `advisory` mode
   reachable at all** — `SystemOneService` refuses to construct without an active
   calibration record.

**Acceptance:** per family, ≥100 outcomes / ≥30 trajectories / ≥20 per cohort, with
train/calibration/holdout disjoint by trajectory. Only then does advisory mode
become constructible, and only then can any Laya effect be observed.

**This is the honest answer to "how do we make Laya meaningful": first make it
possible to tell whether it is.** Today it is not, and no integration change alters
that.

### Phase 2 — Fix the ceiling *deliberately*, not by accident.

DP-1 is a real defect, but the fix is **not** "raise the constants." That would make
an uncalibrated model load-bearing — precisely the "Laya could make behavior worse"
failure the decision record warns about, and it would break the property in §3.2 that
currently guarantees no semantic loops.

The correct change is to make semantic influence **conditional on measured
reliability**, so the ceiling lifts only where evidence justifies it:

- Keep `0.82` as the floor for any family **without** an active, drift-clear
  calibration record. Uncalibrated families stay inert. This preserves today's
  safety property exactly.
- For a **calibrated** family, allow semantic support to exceed the floor, bounded
  by that family's measured accuracy — e.g. `support = calibration.progress_lower_bound
  × model_confidence`. A family measured at 0.55 cannot outrank a deterministic rule
  no matter how confident it sounds; a family measured at 0.90 legitimately can.
- Add a **regression test that pins the ceiling**: assert that an uncalibrated
  semantic candidate cannot outrank a deterministic rule. Today nothing fails if
  someone tunes `semantic_threshold` or the `0.35`/`0.40` constants — the system
  would silently flip Laya from decorative to load-bearing.

**This directly serves your goal:** it makes Laya's influence *proportional to
demonstrated value* rather than either zero or arbitrary. It is the deterministic
shift you asked for — deterministic in the sense that the influence is a function of
measured reliability, not of a tuned constant.

### Phase 3 — Close the one live flip-flop risk.

`rank_stateworks` is the only channel where Laya already actuates, and it is the one
place a near-uniform binary model could oscillate workflow selection. Before any
route-qualification attempt:

- Require a **margin**, not just a threshold. Stage-B already established the right
  bar (confidence separation ≥ 0.15 with calibrated probabilities); enforce it in
  code rather than as an experiment precondition.
- Add **hysteresis**: do not re-rank on a preference change unless the new preference
  exceeds the incumbent by a margin sustained across N consecutive judgments. This
  reuses the exact anti-thrash logic DP already applies to interventions — the same
  shape, applied to route selection.
- Bound reorder frequency per session, so even a pathological provider cannot
  flap workflows faster than the state machine can absorb.

**Acceptance:** a property test that feeds an alternating near-threshold preference
sequence and asserts the selected StateWork does not change more than once per
hysteresis window.

### Phase 4 — Only then, run the Laya arm.

The harness is already correct and properly gated: `cfw_csw_dp_laya` (shadow) and
`cfw_csw_dp_laya_advisory`, with `_require_durable_laya_evidence()` and an
`INVALID_TREATMENT` classification for a configuration lacking live provider
attestation. That gating is exactly right and should not be relaxed.

Run shadow-first, compare `cfw_csw_dp` → `cfw_csw_dp_laya`, and only promote to
advisory for a family that has cleared Phase 1 and Phase 2. Report influence rates
(`influenced_decisions`), not just scores — an arm that changes nothing is a valid
negative result and should be recorded as one.

---

## Part 7 — What I am explicitly not recommending

- **Do not raise the semantic ceiling now.** It converts an inert safety net into an
  unvalidated one. The decision record's "Laya could make behavior worse" is the
  correct risk, and the ceiling is currently the only thing preventing it.
- **Do not fine-tune Laya yet.** Strategy C is deferred for the right reason: ~1,200
  labeled decisions per family are needed to bootstrap, and you have 3. Training
  before the corpus exists would be fitting to noise.
- **Do not add a new orchestration layer.** The decomposition is already correct
  (detectors / lifecycle / candidates / selector / directives are properly separated;
  the fast-loop facade is not a god-object). A new layer would add a second owner for
  a decision that currently has exactly one — the specific failure this architecture
  was built to avoid.
- **Do not treat CSW as a steering participant.** CSW has **zero** Laya awareness by
  design (one mention repo-wide, in its README, naming Laya as a future cohort arm).
  It is an authority, not an advisor. Any design assuming CSW participates in
  semantic steering is unbuildable. Steer *within* CSW's constraint set; never
  through it.
- **Do not relax the qualification gates** to get a result. They are the only thing
  standing between "Laya is meaningful" and "Laya is confident and wrong."

---

## Part 8 — Verdict

**On the architecture:** this is production-grade and, in the parts that matter most,
architect-level. The three-way authority separation is correct and enforced. CFW's
deletion of its own metacognition module is the decision that makes the whole thing
coherent. DP's pure-function decomposition, CAS revision checks, provider-neutral
protocol with 16-field validation, condition-scoped anti-thrash, mode hysteresis,
and self-expiring leases are all right. CSW's evidence-derived completion, CAS
revisions, append-only history, and "data-only bundles cannot trigger Python imports"
are all right. The cross-repo wire tests pass with all three systems constructed for
real.

**On the goal:** the meta-meta-cognition system exists and steers correctly — it
steers via DP's deterministic controller, which is bounded, reversible, measured, and
incapable of latching. What does not yet exist is a *semantic* contribution to that
steering, and the reason is that the semantic model performs at or near chance on
four of five families and has no calibration record.

So the honest framing of your question is this: **you are not asking how to wire Laya
in. You are asking how to make an unmeasured component load-bearing.** The answer
cannot be a wiring diagram. It is a measurement plan, and your own repositories
already contain most of it — the shadow plumbing, the durable evidence store, the
qualification gates, the difficulty screener, the properly-gated cohort arms, and a
decision record that reached the same conclusion two days ago.

The single most valuable next action is not in DP, CFW, or CSW. It is **authoring
task families hard enough that the unassisted model fails them**, because until that
exists, no steering layer — deterministic or semantic — can demonstrate value, and
every number you produce will saturate.

And the thing most worth protecting while you do it: **the loop-break signals are
host-derived and unwritable by any provider.** That property is why the system is
safe today. Any change that makes it possible for a model to influence when a loop is
considered broken — rather than merely which of several legal behaviors is preferred —
trades away the strongest guarantee in the triad. I would treat that as the one line
not to cross.

---

---

**END OF ASimpleThought.md**
