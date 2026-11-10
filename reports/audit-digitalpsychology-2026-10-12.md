# DigitalPsychology — Individual Audit Report

**Audit type:** read-only source audit
**Date:** 2026-10-12
**Commit audited:** `12cf70e` (branch `master`), 338 tracked files, 219 Python / 79 Markdown
**Test evidence (all verified 2026-10-12):** full `tests/` suite **459 passed, 6 skipped**
(twice: `457 passed` in the initial background run, then `459 passed` on re-verification
with `TMPDIR=/tmp` — the 2-test delta is the `test_multi_agent_isolation.py` pair
described in the environment note below, which the first run had not yet reached with
a protected TMPDIR). Targeted batches: `test_policy_selector.py` +
`test_laya_influence.py` + `test_control_tick_noop.py` → **44 passed**;
`test_fast_loop.py` + `test_slow_loop_outcomes.py` + `test_intervention_outcomes.py` +
`test_task_attention.py` + `test_control_boundary_sequence.py` → **60 passed**;
`test_control_mcp_host_bridge.py` + `test_mcp_transport_compat.py` +
`test_multi_agent_isolation.py` → **62 passed**.

The 6 skips are all opt-in local-checkpoint tests in
`tests/test_system_one_laya_local_robustness.py` (5 + 1), reason: *"opt-in local
checkpoint test; never starts model-backed work by default."* They are deliberate
gates that keep model-backed work out of an ordinary test run — consistent with this
audit's read-only, no-inference constraint, and consistent with DP-2 (no calibration
bundle exists to run them against).

> **Environment note (not a code defect).** Under Hermes' default
> `TMPDIR=~/.hermes/cache/scratch`, two tests in `tests/test_multi_agent_isolation.py`
> (`test_fallback_requires_explicit_known_good_profile`,
> `test_expired_profile_stops_being_advisable_after_restart`) fail with
> `RoutingProfileError: routing profile parent directory is not a protected trust
> boundary`, raised from `lib/policy_priors/prior_store.py:85`. Cause: the tests use
> pytest's `tmp_path`, which lives under `$TMPDIR`; the parent
> `~/.hermes/cache` is mode `0o775` and lacks the sticky bit, so the security control
> correctly rejects it. Both tests **pass** under `TMPDIR=/tmp` (verified: 19/19 in
> that file). The identical mechanism produces the CFW
> `test_routing_identity.py` failure. The security control is working as designed in
> both cases.

---

## 1. What DigitalPsychology Is

One sentence: **DP is an external, host-trusted adaptive behavioral controller and
learning plane that owns no authority over actions, workflows, or evidence, and
exists to answer one question the other two systems cannot: *which bounded
behavior change, under this specific condition, actually helped?***

The distinguishing architectural decision — stated plainly in the README — is that
DP was deliberately *not* built as a fourth skill pack. CFW already prescribed how
an agent should behave and CSW already encoded what transitions were legal. Adding
more instructions would have added policy without answering the causal question.
DP is therefore built as a **measurement instrument with a bounded actuator**, not
as an instruction layer. That is the correct abstraction, and it is the reason the
three systems compose instead of colliding.

### Authority model (verified, and genuinely clean)

| System | Owns | Cannot |
|---|---|---|
| DP | Behavior state revision, intervention lifecycle, candidate generation, policy ranking, telemetry | Disable WARD, lower component floors, authorize an action, bypass CFW's gate, rewrite CSW evidence, legalize an illegal transition |
| CFW | Component contracts, hard action gates | Choose global mode/focus (delegated to DP when bound) |
| CSW | Workflow eligibility, state, evidence, transitions | — |

DP does not grant permissions and is not in the model-visible tool registry. Its
control MCP is launched as a host-side child process and DP *rejects* a tool
registry that would expose it. Runtime orchestration is deliberately withheld from
the model. **CONFIRMED** — this is the single best-engineered boundary in the triad,
and it is enforced in code (`lib/service.py`, `lib/host/bridge.py`) rather than by
convention.

### Control flow (traced end to end)

Host observations → `FastLoopInput` → `evaluate_conditions()` (pure host-fact
detectors) → `InterventionLifecycle.advance()` (expire/rollback) →
`BehaviorCandidateGenerator.generate()` (bounded legal candidates) →
`BehaviorPolicySelector.select()` (rank + abstain) → `DirectiveBuilder.build()` →
host applies through CFW/CSW ports → `InterventionEvaluator` scores outcome from
**host evidence only**.

Every stage is bounded and pure. The controller is explicitly documented as "a pure
decision function" — the host persists state, checks revision (CAS), and intersects
directives with hard constraints. Agent self-reported success is explicitly *not*
evidence (`lib/outcomes.py`). This is the correct design.

---

## 2. Fast Loop / Slow Loop

**`lib/fast_loop.py` (233 lines)** — `BehavioralController.decide()` is a thin,
well-decomposed orchestrator. Responsibilities are correctly extracted into
`lib/control/*` (detectors, candidate generator, policy selector, directive
builder, intervention lifecycle). The facade is not a god-object. **CONFIRMED.**

**`lib/slow_loop.py` (259 lines)** — `BoundedLearningController` evaluates
interventions across *independent* trajectories and promotes bounded routing
profiles only after a host-bound receipt passes. Critically, it is **explicitly
invoked, not a background worker**, and the MCP boundary refuses raw
`events`/`task_context`, accepting only a protected `experiment_id` plus host
authorization. That closes the most obvious privilege-escalation path.

Trajectory reconstruction prevents repeated events from one trajectory being
counted as independent samples. This is a real methodological safeguard, not
ceremony.

---

## 3. Laya Integration — The Central Finding

### 3.1 How Laya is wired (verified)

`lib/system_one_backends.py` defines a **provider-neutral** `SemanticProvider`
Protocol. `LayaProvider` is one implementation; no provider name belongs in the
interface. There is **no static `import laya` anywhere in DP source** — the package
is imported only when a `LayaProvider` is constructed, so DP's normal startup never
loads Laya, PyTorch, or model weights. **CONFIRMED** (grep across `lib/`,
`cognitive_runtime_core/`, `scripts/` returns zero static imports).

Model integrity is enforced before load: revision + adapter content hash + weight
hash (`model_content_digest()` hashes the exact checkpoint, config, and tokenizer
inputs; symlinks are rejected). Failure → deterministic fallback.

The frame is validated with 16 cross-checks in `SystemOneRuntime._validate()`
(identity, request ID, sequence, state digest, question bundle, protocol hashes,
backend/model/revision/content, calibration bundle, protocol version). A mismatch
raises `provider_contract_error` and — importantly — is classified as a **local
contract violation that does not open the shared circuit breaker**, so one agent's
malformed response cannot disable another agent's calls.

Three modes: `disabled` (default), `shadow` (judgment computed, never reaches
policy), `advisory`. Advisory demotes to shadow unless the backend is
`digitalpsychology-laya-native` **and** `inference_diagnostics.advisory_eligible is
True` **and** every requested question family is calibrated. This is correct
fail-safe design. **CONFIRMED.**

### 3.2 The arithmetic ceiling — Laya cannot outrank the deterministic rule

This is the most consequential finding in the audit, and it is **structural, not
incidental**. From `lib/control/policy_selector.py` (lines 706–728):

```python
if learned_supported:            decision_support = 0.60 + 0.40 * empirical_quality
elif candidate_id in rule_support:
    decision_support = float(rule_support_by_id[candidate_id])
    if semantic_support is not None:
        decision_support = max(decision_support, 0.35 + 0.40 * semantic_support)
elif "deterministic_rule" in reason_codes:  decision_support = 0.82
elif semantic_support is not None:          decision_support = 0.35 + 0.40 * semantic_support
elif "continue_current" in reason_codes:    decision_support = 0.40
else:                                       decision_support = 0.25
```

`0.35 + 0.40 × semantic_support` has a **maximum of 0.75** (at `support_score = 1.0`).
A `deterministic_rule` candidate scores **0.82**. Since ranking sorts on
`decision_support` descending, a purely semantically-endorsed candidate can
**never** displace the deterministic controller's own recommendation.

**EMPIRICALLY VERIFIED** — probe `/probe_semantic_ceiling.py` against the real
production `BehaviorPolicySelector`, sweeping `goal_alignment_support` across
0.0/0.25/0.5/0.75/0.9/1.0:

```
semantic_support |  winner mode | semantic wins?
             0.0 |      RECOVER |          False
             0.5 |      RECOVER |          False
             1.0 |      RECOVER |          False
RESULT: semantic-only evidence beat the deterministic rule in 0/6 configurations.
```

**CONFIRMED, HIGH confidence.** The 0.07 gap is small enough to be an accident of
tuning and large enough to be decisive. Anyone who tunes `semantic_threshold` or the
0.35/0.40 constants — entirely plausible maintenance edits — flips Laya from
decorative to load-bearing with no test failing.

This is deliberate ("Advisory Laya evidence can raise a candidate's support without
weakening a stronger deterministic floor"), but its consequence was never written
down: **Laya is currently incapable of changing a DP decision in the case that
matters most — when the deterministic rule is active.** That is exactly the
contradiction case where semantic judgment is most valuable.

### 3.3 The one place Laya genuinely actuates

`lib/control/statework_selector.py::rank_stateworks()`:

```python
if (semantic_preference in ranked and semantic_confidence >= minimum_semantic_confidence):
    ranked.remove(semantic_preference); ranked.insert(0, semantic_preference)
```

This **does** deterministically reorder. It is correctly bounded — the preference
must already be in the CSW-eligible set (`test_semantic_preference_reorders_only_csw_eligible_stateworks`
proves an unregistered ID is ignored), and it is gated at
`minimum_semantic_confidence = 0.80`. CSW retains legality. This is the *only*
semantic→policy channel that survives the ranking ceiling. **CONFIRMED.**

### 3.4 Advisory mode is currently unreachable in practice

`SystemOneService.__init__` raises unless at least one enabled question family has a
calibration record with `status == "active"` and `drift_status == "clear"`. No such
record exists on this host. Therefore `advisory` mode **cannot be instantiated** —
shadow is the effective ceiling. **CONFIRMED** by source; consistent with
`reports/laya-decision-record-2026-10-10.md` ("no calibration record exists") and
the HANDOFF (`Laya qualification and efficacy: BLOCKED_RUNTIME_UNAVAILABLE`).

Note the HANDOFF says the pinned runtime/checkpoint is absent and "supersedes" the
earlier environment note. **That statement is now stale** — I verified
`~/.cache/laya/convaiinnovations-laya-typed-decisions-1a793eb/model.safetensors`
exists on disk. The checkpoint is present; the *calibration bundle* is what is
missing. **Stating this explicitly because it downgrades an earlier recorded
verdict.**

### 3.5 What the model was actually measured to do

`reports/laya-decision-record-2026-10-10.md` is unusually rigorous prior art and I
verified its claims are consistent with source. The checkpoint is a 421M
ModernBERT-large fine-tuned for 1 epoch on **four synthetic workflows** (invoice
processing, security incidents, customer service, agent-trace observability). On
metadata-blinded probes:

| Family | Result |
|---|---|
| `action_relation` | 1/5 |
| `progress_relation` | 1/6 |
| `retry_relation` | 1/4 |
| `goal_drift` | **0/2 drift recall** (the exact ability the question exists for) |
| `route_preference` | 2/2, but raw p = 0.50/0.66 — near coin-flip |

Input defects were **disproven** as the cause: 399–758/1024 tokens used, **0
truncated, 0 dropped, 0 collapsed option spans** across all 19 retained projections.
Prompt-format variants (binary, compact, hierarchical, supplied-claims, `noul`) all
failed to repair discrimination. The evidence is present; the model does not
leverage it. This is a **model-capability failure, not an adapter defect** — and the
adapter is the best-controlled component in the system.

Root cause #3 is the one that matters for the user's goal: the semantic→role→support
mapping encodes behavioural assumptions (`equivalent_retry` → RECOVER-flavoured
alignment, `goal_drift` → drift risk on diagnostic roles) with **no measured outcome
evidence**. With near-uniform model distributions, these mappings convert wrong
labels into confidently wrong support scores — in its own words, "Laya could make
behavior worse."

*(Correction: the decision record names this `_ROLE_UTILITY` in `candidate_generator.py`.
That symbol is **not present in the current checkout** at `12cf70e`; the equivalent
logic is inline in `_semantic_support()` at `candidate_generator.py:316-360`. The
finding survives the symbol change — the mechanism is unvalidated either way.)*

---

## 4. Loop-Breaking and Loop-*Causing* Analysis

This is the user's explicit requirement, so it deserves direct evidence rather than
assertion.

### 4.1 DP has a genuine anti-thrash mechanism (verified empirically)

`InterventionLifecycle._condition_history()` blocks re-creating an intervention on
a condition that previously failed:

```python
previously_failed = {item.cause for item in original.intervention_history
    if item.outcome in {"ineffective","regressed","unattributed","inconclusive","expired"}
    and applies_to_attention(item.target_criteria)
    and current_condition_ids.get(item.cause) == item.condition_id}
```

Probe `/probe_loop_breaking.py` against the real predicate:

| Scenario | Result | Verdict |
|---|---|---|
| Same cause, same `condition_id` re-triggered | `{'equivalent_retry'}` blocked | **loop broken** |
| Same cause, **new** `condition_id` (host reported new hypothesis) | not blocked | **recovery allowed** |
| Failed on an *unattended* criterion | not blocked | **scope-correct** |

This is the correct semantics: it blocks *identical* retries while permitting
recovery when host state materially changes. It is neither a blunt global cap nor a
permissive always-allow. **CONFIRMED, HIGH confidence.**

Three independent loop-latches are additionally closed:

1. **Mode hysteresis** — `_mode_lease_holds()` holds a mode until a *specific* exit
   condition is met (`verification_result`, `fresh_evidence`, `plan_committed`,
   `diagnostic_evidence`, `progress_or_new_approach`), with a `minimum_actions`
   floor, releasable early only by a hard interrupt. A withdrawn intervention
   **cannot** bypass its own lease by restoring a saved mode value.
2. **Self-expiring leases** — every intervention carries `expires_after_actions`
   (bounded 1–16, validated `1..64` in `behavior_state.py`) and an
   `evaluation_horizon_actions`. Probe 5 confirmed a lease cannot persist
   indefinitely. **No latch-based loop is possible.**
3. **Inconclusive ≠ terminal** — partial evidence keeps an intervention live until
   its declared horizon, so a missing fact cannot trigger premature rollback (which
   would itself oscillate).

### 4.2 Can Laya cause a loop? — Currently no, for two independent reasons

**Reason 1 (the ceiling).** Probe 1 proved Laya cannot outrank a
deterministic-rule candidate. Its only ranking effect is capped at 0.75 < 0.82.

**Reason 2 (the loop-break signal is unreachable).** The three inputs gating
`previously_failed` are: (a) `item.outcome`, set by `InterventionEvaluator` from
**host evidence only**; (b) `item.condition_id`, derived by `identify_condition()`
from goal set / CSW snapshot / evidence refs / approach epoch; (c) `attention_ids`,
from the host-owned lease. **None is writable by a `SemanticProvider`.** Laya cannot
forge a loop-break or suppress a genuine one. **CONFIRMED.**

### 4.3 Where a loop *could* be introduced — honest risk register

These are latent, not active. They are the price of making Laya meaningful.

| Risk | Mechanism | Current mitigation | Residual |
|---|---|---|---|
| Semantic→StateWork flip-flop | `rank_stateworks` reorders at ≥0.80 confidence; a noisy binary model crossing the threshold intermittently would alternate workflow selection across ticks | Gate is at 0.80; selection is bounded to the CSW-eligible set | **Real.** The decision record measured `route_preference` at raw p=0.50/0.66 on a coin-flip case. A 0.66-vs-0.80 gate is not a wide margin for a near-uniform distribution. |
| Unvalidated support mapping | The inline semantic→role→alignment logic in `_semantic_support()` is heuristic; on a collapsed distribution it converts wrong labels into confident wrong scores | Ceiling keeps any resulting candidate below deterministic rules | **Real but bounded.** Would become unbounded if the ceiling were raised. |
| DP↔CFW policy fight | DP may emit `REPLAN` while CFW's `RetryBoundaryEnforcer` returns `require_changed_hypothesis` | CFW retains hard gate authority; DP gets a bounded rejection boundary | Low. CFW wins, so no infinite loop — but see the cross-system report for a subtler interaction. |
| Inference latency in the control path | Measured p50/p95 per family: action 6.9/8.2 s, progress 8.8/13.8 s, retry 8.5/9.0 s, route 2.8/4.0 s; warmup 5.6–22 s | Circuit breaker (3 failures → open), 30 s deadline, bounded semaphore | Moderate. Acceptable for low-frequency advisory use; **not** for per-action real-time. The query planner's VOI gate exists precisely to bound this. |

---

## 5. Assessment

**Sophistication: PRODUCTION-GRADE, architect-level.** This is not a prototype.
The engineering discipline is exceptional and rare: pure decision functions,
compare-and-swap revision checks, provider-neutral protocols with 16-field
validation, adversarial self-audits recorded in the repo itself, refused
completion claims, and an explicit refusal to widen a benchmark that had saturated.

The strongest signal of engineering maturity is `HANDOFF.md` Pass 5: the author
discovered the cohort harness "was measuring nothing" — it set environment variables
and never constructed CFW or bound DP, so **every prior cohort run measured the
model, not the systems**. Rather than reporting those runs as evidence, they were
discarded as non-measurements. Then, having fixed the harness and run a real
140-trial cohort, the author declined to claim an effect because five of seven task
families scored 1.00 in every cell — a ceiling effect — and stated that the
benchmark "is not yet discriminative." Then rejected two newly-authored harder
families that also saturated, and committed only `irreversible_side_effect` (1/3,
then 2/3 — genuine variance).

That is exactly the right instinct, executed repeatedly, against the incentive to
publish a win.

**The gap:** DP's architecture is sound and its safety properties are real, but its
semantic layer is *inert by construction* — capped below the deterministic floor,
gated behind a calibration record that does not exist, and feeding a model measured
at 1/5, 1/6, 1/4, 0/2 on four of five families. The system is safe precisely because
Laya currently does almost nothing. Making Laya meaningful is therefore **not a
tuning problem — it is a measurement problem**, and the repo's own evidence says so.

---

## 6. Findings

| # | Severity | Finding | Evidence |
|---|---|---|---|
| DP-1 | **P0** | Semantic evidence is structurally capped below the deterministic floor (0.75 < 0.82), so Laya cannot change a decision whenever a deterministic rule applies — the exact case where semantic judgment is most valuable. Unwritten consequence of deliberate design. | Probe 1, 0/6 configs; `policy_selector.py:706-728` |
| DP-2 | **P0** | `advisory` mode is unreachable: requires an active calibration record that does not exist. Shadow is the effective ceiling, so no Laya influence can ever be observed in production. | `system_one_service.py:32-36`; decision record §A.1 |
| DP-3 | **P1** | The semantic→role→support mapping in `_semantic_support()` (`candidate_generator.py:316-360`) encodes behavioural assumptions with **no outcome validation** — e.g. `action_relation == "advances_primary_goal"` raises `alignment` for `DELIVER`/`CONTINUE`/`RECOVER` roles and *lowers* it for others, and `goal_drift == True` sets `drift_risk` for `DIAGNOSE`/`REPLAN`/`REINSPECT`. Given a model measured at 1/5, 1/6, 1/4 and 0/2, these mappings systematically convert wrong labels into confidently wrong support scores. **Correction to the decision record:** the `_ROLE_UTILITY` map it describes is **not present in this checkout** (`12cf70e`) — the logic is inline in `_semantic_support` instead. The finding survives; the named symbol does not. | `candidate_generator.py:316-360`; decision record §D.3, §G |
| DP-4 | **P1** | `rank_stateworks` semantic reorder (≥0.80) is the only live Laya→policy channel, yet `route_preference` was measured at raw p=0.50/0.66. The gate sits close to the model's noise floor. | Decision record §E, Stage-B failure (margin 0.121 < 0.15) |
| DP-5 | **P1** | HANDOFF states the pinned Laya checkpoint is absent and "supersedes" the earlier note. **Stale** — `~/.cache/laya/convaiinnovations-laya-typed-decisions-1a793eb/model.safetensors` exists. The missing piece is the *calibration bundle*, not the checkpoint. | Filesystem verification; explicit downgrade of an earlier recorded verdict |
| DP-6 | **P2** | Latency p50/p95 of 2.8–13.8 s per family makes Laya unsuitable for per-action real-time use without the VOI gate strictly enforced. | Decision record §C |

**Assessment of what is *not* broken:** the authority boundary, the pure-function
decomposition, the CAS revision checks, the anti-thrash condition scoping, the mode
hysteresis, the self-expiring leases, the trajectory-independence checks, and the
fail-safe provider validation are all correct and correctly enforced in code. DP-1
through DP-5 concern an *inert subsystem*, not a corrupt one.

---

**END OF DigitalPsychology INDIVIDUAL REPORT**
