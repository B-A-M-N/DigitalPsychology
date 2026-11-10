# Laya / DigitalPsychology — Decision Record and Closure Plan

**Date:** 2026-10-10
**Author:** DigitalPsychology agent investigation (read-only investigation phase; the authorized post-review alignment pass modified source files in the advisory worktree without committing — no Git state changed)
**Authoritative checkout:** `DigitalPsychology-laya-advisory` worktree, branch `laya/advisory-behavior-selection` @ `45ae7e3` (protocol 2.7.2, projection v7, laya 0.3.22, pinned checkpoint `convaiinnovations/laya-typed-decisions` revision `1a793eb`, content SHA `cda55a3e…`)

**Update 2026-10-10 (implementation and model-use rereview):** the third review
found the Stage-B raw-margin rejection conceptually wrong, confirmed the
route-only planner gate was not wired into production, and showed the 0.90
advisory gate makes calibrated coverage zero on these distributions. All P0
items were fixed (see `reports/laya-rereview-response-2026-10-10.md` in the
advisory worktree); the Stage-B result is reclassified as *promising
discrimination on a small authored set, no validated advisory readiness*, and a
new mode-controlled contrast screen shows **PARTIAL** evidence use beyond the
mode-tag heuristic (7/10 vs 5/10 deterministic) with unusable confidence
separation. Laya remains non-actuating.

**Update 2026-10-10 (post-review alignment pass):** the authoritative checkout is
`DigitalPsychology-laya-advisory` (branch `laya/advisory-behavior-selection`, commit
`45ae7e3` pre-change, working tree now modified by the authorized alignment pass —
6 lib files + tests, uncommitted by design). Implementation and route-screen
results are recorded in the advisory worktree at
`reports/laya-alignment-implementation-2026-10-10.md`,
`reports/laya-route-screen-stage-b-2026-10-10.json`, and
`reports/laya-token-coverage-audit-2026-10-10.json`. The original decision-record
files under the master worktree `reports/` are now superseded for implementation
identity by the advisory worktree copies.
**Scope:** Determine the most technically sound, computationally efficient, empirically defensible path for optional Laya in the DP/CFW/CSW triad. This is a decision and closure plan, not another audit.

Restrictions honored: no Git-state mutation, no commit/push, no external agent/model trajectories launched; no billed inference; no downloads; no calibration/threshold/activation change; local inference limited to the bounded offline diagnostics (token-coverage replication + the Stage-B route screen, 10 inferences, `/tmp` output, network disabled, cached checkpoint).

---

## A. Executive decision

**Adopt Strategy E + B in sequence, operating as Strategy F (shadow-only) until a decisive experiment passes: keep the deterministic triad as the sole decision authority; freeze or shadow-lock four of five Laya question families; qualify at most `route_preference` (binary StateWork fit) — and only after a narrow, pre-registered offline experiment that the checkpoint has not yet passed.**

The current checkpoint — a 421M ModernBERT-large specialist fine-tuned for 1 epoch (7,313 RLCD updates) on four synthetic workflows (invoice processing, security incidents, customer service, agent-trace observability) — does **not** generalize to DP's behavioral families. On the metadata-blinded 19-case probe: `action_relation` 1/5, `progress_relation` 1/6, `retry_relation` 1/4, `goal_drift` 1/2 (0/1 drift recall — the exact ability the question exists for), `route_preference` 2/2 but with 0.50/0.50 and 0.34/0.66 raw probabilities (the "correct" answers are near coin flips; raw max confidence across all families is 0.29–0.66). The checkpoint's own README states: "This is a specialist… Expect it to behave like the base laya checkpoint, or worse, on anything else." Input truncation and option-span collisions are **disproven** as causes — verified by replicating Laya's own sequence builder with the pinned tokenizer on the 19 retained projections: 399–758/1024 state tokens used, **0 truncated, 0 dropped, 0 collapsed option spans** across all cases (audit artifact `laya-token-coverage-audit-2026-10-10.json`). Prompt/projection variants (binary, compact, hierarchical, explicit evidence-boundary, supplied claims, `noul`) all fail to fix the collapse. The one real authenticated trace is 1 trajectory, 5 shadow judgments, 3 independent labels → 2/3, tied with the deterministic host baseline at 2/3, zero advisory use, zero influenced decisions.

Expected benefit of the recommended course: no regression risk (deterministic system unchanged), removes the present risk that a near-uniform model distribution + arbitrary fixed utility values reorder legal candidates inside an experiment (i.e., it prevents Laya from making the agent worse), and produces the only evidence that can justify further investment: a decisive offline discrimination test plus a staged qualification path for the single usable family.

Primary remaining risk: `route_preference` may also fail at ≥0.8 accuracy (its current 2/2 is 2 hand-authored cases, uncalibrated) — in which case Laya is retained in shadow-only indefinitely and the closure is "deterministic-only + shadow," a valid end state per the Mission.

---

## A.1 Evidence-language corrections (final-alignment review §9)

| Original claim | Corrected status |
| --- | --- |
| "Checkpoint domain mismatch VERIFIED (HIGH confidence)" | **Verified fact:** different training domain (published); **strong hypothesis:** domain mismatch causes failures. Diagnostic failures observed; representation/formulation alternatives not fully excluded. |
| "FAILED for four of five families" | **Demonstrated diagnostic limitation** on corrected blinded synthetic probes; not a statistically established real-world failure rate. |
| "0 truncation means not an input defect" | **Verified for the tested projections** (19 retained cases, 0 dropped/truncated/collapsed); does not prove every relevant distinction is semantically optimal. |
| "route_preference is a qualification candidate" | **Unverified** — the 2026-10-10 Stage-B screen FAILED the confidence-separation floor (see §J update). |
| "Laya improves agent outcomes" | **Unverified** (unchanged). |

## B. Verified implementation baseline (what works and must not change)

Verified by source inspection (lib/system_one_*, lib/control/*) and retained artifacts:

1. **Optional-dependency discovery.** `SystemOneService.from_environment()` auto-discovers `laya` when installed; absent the package, DP is deterministic and never imports laya/torch. Verified in `.venv-laya` smoke runs (typed shadow judgments through the production path).
2. **Pinned checkpoint integrity.** Revision + adapter content hash (SHA-256 `cda55a3e…`) + weight hash (`4fa56de7…`) verified before load; download-on-first-warmup with bounded 180 s initialization timeout; failure → deterministic fallback.
3. **Shadow-only default.** No provider/mode flags → shadow; shadow judgments never reach policy selection. Verified across canaries and the retained cohort (`advisory_uses: 0`, `influenced_decisions: 0`).
4. **Bounded actuation machinery (exists, unvalidated).** Selection change requires: explicit experiment ID, exactly two legal host-generated candidates from the planned pair, calibrated distributions for both candidates, versioned `semantic-role-utility-v1` expected-utility scores, calibrated confidence ≥ 0.80, margin > 0, CFW/CSW gates, circuit breaker (3 consecutive ineffective/regressed → 1 h open), one-use context breaker. The fixed `_ROLE_UTILITY` map and planner decision values (0.70–0.86) are heuristics with no outcome validation — this is a defect to gate, not delete.
5. **Deterministic host baseline v3.** Immune-frame-derived baselines only: action via `host_declared_relation`, progress via `progress_class` mapping, route via first legal route; no retry/drift/no-progress inference. Conservative by design.
6. **Full evidence lifecycle.** Per-attempt durable evidence (256 KiB bound), authenticated decision events, independent-outcome receipts (v3 action-bound for action/progress/drift; retry receipts rejected pending a contract), digest-verified offline replay, cluster-bootstrapped metrics (top-1, macro-F1, Brier, log loss, 10-bin ECE, AURC, host baseline, majority class).
7. **Qualification gates.** ≥ 100 independent outcomes, ≥ 30 independent trajectories, ≥ 20 per work-mode/deliverable cohort per family; min accuracy ≥ 0.80, max Brier ≤ 0.25, max ECE ≤ 0.12; train/calibration/holdout disjoint by trajectory; activation requires independent host review. Unchanged; no family is qualified.

---

## C. Source / evidence matrix

| Claim (from retained evidence) | Source | Reproducible? | Valid measurement? | Remaining uncertainty |
|---|---|---|---|---|
| Native `laya==0.3.22` runs; typed shadow judgment through DP path | `laya-shadow-smoke-2026-10-07.json`, offline smokes 10-09 | Yes (offline, cached checkpoint) | Integration only | — |
| Pinned checkpoint = ModernBERT-large 421M, 28 layers, ctx 1024; fine-tuned 4 synthetic workflows, 7,313 updates/1 epoch | `~/.cache/laya/…/rl_agent_config.json`, `encoder/config.json`, checkpoint README | Yes | — | Training-domain closeness to DP cannot be directly measured |
| Choice:11+ temperature invalid (0.1006 → clamped 0.5) | `rl_agent_config.json` + provider warnings | Yes | — | Confidence in all ≥11-option answers uncalibrated; DP families cap at 6 options, so the bucket is unused in practice — but the invalid entry shows the shipped calibration is not trustworthy |
| Metadata-blinded 19-case collapse: action 1/5, progress 1/6, retry 1/4, drift 1/2, route 2/2 | `evaluations/laya-semantic-families-metadata-blinded-2026-10-10.json` (+retained input projections) | Yes (script + fixture builder retained; inputs retained) | Diagnostic only: hand-authored fixtures, raw probabilities, n=19 | Real per-class error rates unknown; fixtures necessarily encode evidence a classifier must interpret |
| Drift hard-negative: 0/8 drift recall across choice/relation/compact/noul formats; 12/12 support specificity | `laya-goal-drift-*-metadata-blinded-probe-2026-10-09.json` ×3 | Yes | Diagnostic (20 + 12 hand-authored cases) | Same as above |
| Retry blind: 1/4 exact with claims supplied; `equivalent_retry` collapse | `laya-retry-structured-claims-metadata-blinded-probe-2026-10-09.json` | Yes | Diagnostic (4 cases) | n=4 only |
| Progress: 3/6 exact after wording fix; hierarchy 1/6 + 3.8× latency; no_progress → verification_progress collapse | `laya-progress-hierarchical-prototype-2026-10-10.json`, robustness suite 24/1 | Yes | Diagnostic | Single fixtured case per class |
| No truncation, no option-collision on all 19 retained projections (399–758/1024 tokens, 0 dropped, distinct spans) | **New token-coverage replication** (Laya's own `build_sequence` + pinned tokenizer; no forward pass) | Yes | Mechanically valid | Coverage was not recorded in the original run; replication assumes the retained `input_projection` equals the forwarded state (script path shows it does) |
| Real trace: 1 trajectory, 5 shadow judgments all `verification_progress`, 3 validator labels → 2/3; host baseline 2/3; 0 advisory/influence | `cohort-report.json` + `authenticated-events.sqlite3` (retry2 cohort) | Yes (DB retained, digests table matching manifest) | Integration + descriptive; n=1 trajectory, 3 labels | No independent-trajectory interval; labels from one task |
| Latency p50/p95 per family (CPU, warm): action 6.9/8.2 s, drift 4.7/6.0 s, progress 8.8/13.8 s, retry 8.5/9.0 s, route 2.8/4.0 s; warmup 5.6–22 s | `evaluations/laya-local-latency-2026-10-10.json`, smokes | Yes | Environment-specific (CPU, single worker) | GPU/other hardware untested; queue serialization adds tail latency under load |
| Qualification ineligible: 0 paired outcomes in default stores; cohort blockers all unmet (control/advisory arms, calibration corpus, 30 pairs, cohort minima) | `cohort-report.json` `qualification_blockers`, `system_one_calibration.py` gates | Yes | — | — |
| Prior v2 "2/3 accuracy" and 7–8/19 sweeps | audit text + superseded artifacts | Partial (source DB not retained) | **No longer counts** (label leakage or missing rows) | Superseded |

**Separation of concerns:** Functional integration — VERIFIED (B.1–B.6). Predictive correctness — UNVERIFIED on real observations; DIAGNOSTIC FAILURE on blinded synthetic sets (collapse). Confidence calibration — NOT APPLICABLE (no calibration record; raw probs near-uniform; checked `choice:11+` invalid). Decision usefulness — UNVERIFIED (utility map is heuristic; no outcome link). Downstream behavioral influence — UNVERIFIED (zero influenced decisions observed). Whole-task efficacy — UNVERIFIED (n=1 trajectory). Latency/cost — MEASURED (above); acceptable only for low-frequency advisory use, not per-action real-time.

---

## D. Root-cause analysis (ranked)

**Verified defects/limitations:**
1. **CHECKPOINT_DOMAIN_MISMATCH (primary, verified).** `laya-typed-decisions` was trained to discriminate typed decisions inside four synthetic workflows. DP's families ask for behavioral-state semantics (progress classes, retry equivalence, drift) that are outside that learned distribution. It performs at/near class prior on 4/5 families. This is the model, not the adapter.
2. **Insufficient contrastive state information is NOT the cause for the synthetic sets (verified).** Every retained projection carried the discriminating facts (objectives, criteria, candidate purpose, blocker transitions, prior-failure claims, evidence kinds) and fit untruncated; the model still collapsed. The information is present but not leveraged.
3. **Fixed heuristics without outcome validation (verified defect, code-level).** `_ROLE_UTILITY` (candidate_generator.py) maps labels to role utilities (e.g., `equivalent_retry → RECOVER 1.0`, `same_premise_new_action → VERIFY 1.0`) with no measured outcome evidence; planner decision values 0.70–0.86 are labeled as heuristics. With near-uniform model distributions, these utilities can reorder legal candidates arbitrarily inside an experiment — i.e., Laya could make behavior worse.
4. **Task formulation mismatch for the actual DP need (verified, secondary).** Several questions ask Laya to re-derive facts the host already authoritatively knows: progress (host has `progress_class` on observed activity; the baseline derives from it), route (CSW eligibility + mode-tag scoring already rank legal routes), drift (CFW host signals already flag drift). The projection deliberately omits host labels to avoid leakage — good — but the net effect is Laya is asked to do with semantic inference what deterministic rules already do wherever the question is answerable.

**Demonstrated limitations (diagnostic):**
5. Binary/question-format manipulations do not repair discrimination (drift 0/8 across 4 formats; retry 1/4 with claims supplied; progress 3/6 best case). Question formulation alone is insufficient — narrowing may help the usable families but cannot rescue the collapsed ones.
6. Raw confidence cannot be used for abstention: all wrong answers carried "advisory-eligible" raw probabilities (0.29–0.34 max on 5-way), which would pass a 0.30 gate; calibrated confidence gates must remain mandatory and no calibration exists.

**Hypotheses (not yet tested):**
7. Domain-specialization (fine-tuning the base ModernBERT or the typed-decisions checkpoint on DP-labeled families) could plausibly reach usable accuracy — there is feasibility evidence (published recipe, 4–5 h on free 2xT4, RLCD), but DP has essentially no labeled corpus (3 labels total), so training data must be collected first. Training is premature and unauthorized now.
8. A replacement local provider (other checkpoint/model) could be better — no alternative is cached locally; evaluation requires download authorization. Documented as Strategy D, deferred.

**Missing evidence:** real labeled frames for any family (≥ 100/30/20 gates), calibration records, matched control/advisory arms, independent efficacy outcomes.

---

## E. Per-family suitability determination (mission §4 required output)

| Family | Evidence | Determination | Recommendation |
|---|---|---|---|
| `action_relation` (5-way) | 1/5 blinded; probs ≈ uniform 0.29–0.31; host already carries `relation_certainty`/`action_class`, planner skips when explicit | **CHECKPOINT_DOMAIN_MISMATCH** (input complete, model at chance) | **Deterministic-only** — rely on host relation facts + existing candidate conditions; keep family disabled or shadow-only. |
| `progress_relation` (6-way) | 1/6 blinded; all→`verification_progress`; 3/6 after wording, no_progress still missed; host owns `progress_class` | **DETERMINISTICALLY_RESOLVABLE** (when host observes progress) + **CHECKPOINT_DOMAIN_MISMATCH** (when it doesn't) | **Deterministic-only** — use host progress evidence (baseline v3 already does); leave Laya shadow-only pending real labels. |
| `retry_relation` (4-way) | 1/4 blinded both conditions; collapse to `equivalent_retry`/`same_premise_new_action`; requires 3 model-proposed claims that agents rarely supply | **CHECKPOINT_DOMAIN_MISMATCH** + input frequently missing (planner already skips) | **Deterministic-only / defer** — host retry counters (≥2) + failure descriptors are the authority; no Laya benefit shown. |
| `goal_drift` (binary) | 0/8 drift recall across choice/relation/compact/noul; 12/12 support specificity → always "no" | **CHECKPOINT_DOMAIN_MISMATCH** — bilateral failure on the exact distinction the question exists for | **Deterministic-only** — CFW host drift signals already gate; Laya's "no" adds nothing and misses real drift. |
| `route_preference` (binary legal-route fit) | 2/2 blinded but raw p = 0.50/0.66 (coin-flip confidence on one case); the deterministic legal-order baseline (first legal route) scores 1/2 on the two labeled route cases per the retained canary audit | **CURRENT_MODEL_SUITABLE(*)** — structurally competent, unvalidated | **Narrow + qualify candidate** — only family worth a qualification attempt; requires ≥0.80 calibrated accuracy on ≥100/30 before any actuation. |

(*) Suitability for `route_preference` is provisional: binary contrast within the model's learned "route/choice" primitive is the one task shape the checkpoint demonstrates; n=2. The decisive experiment (G) tests it with ≥10 blinded cases against the deterministic ranking.

---

## F. What actually reaches Laya (trace verified end-to-end)

Flow: host observations → DP goal/behavioral state → `BehaviorCandidateGenerator`/`assess_host_candidate_ambiguity` → `SemanticQueryPlanner` (fixed heuristic gate; skips on host-resolved ambiguity) → `lib/control/system_one.py` frame factory (`SemanticState` + `QuestionSpec`) → `laya_state_projection()` family-specific projection (v7) → `_laya_questions()` (criteria sorted by label; ≤24 options) → `Agent.decide(state=projection, questions=..., return_details=True, lang="en")` → Laya's `build_sequence` (`[CLS] <type> question: <instructions> [SEP] [MASK] opt… [SEP] state [SEP]`, max_len 1024, head 256, options capped at 48 tokens each) → typed answer + per-question probabilities → `_laya_diagnostics` (coverage, option-collision, truncation) → judgment → `SystemOneService` (shadow/advisory gate, calibration check, abstention) → `candidate_generator` utility scoring → bounded policy selection.

**Verified facts about what the model receives:**
- Retained evidence fields per family (task object, criteria claims/status/evidence, candidate purpose/claims, blocker before/after, activities, legal routes) — complete and untruncated in all 19 retained blinded cases (0 dropped tokens; 399–758/1024).
- Option spans distinct in all 19 cases (5/5, 6/6, 4/4, 2/2, 2/2) — no collapsed options.
- Host progress class / host-declared relation are **omitted** (leakage control) — meaning Laya cannot cheat, but equally cannot leverage authoritative labels; it is asked to infer from raw evidence.
- Prediction-time facts only: `criteria_before` snapshot added (protocol 2.7.x) for temporal progress; no future information in the frame.
- Label leakage controls verified by the blinded script (`_project_checked` rejects target-correlated metadata; explicit negative-class markers rejected).

**Conclusion:** DP asks Laya questions that are *answerable from the supplied state* in the sense the discriminator facts are present — but they are answerable only by a model trained on this semantic domain, which this checkpoint is not. This is a model-capability failure, not an input/contract defect (the input contract is among the best-controlled parts of the system).

---

## G. Semantic decision-value matrix (mission §8)

| Question | Ambiguity resolved | Possible policy change (role/controls) | Observable benefit (evidence) | Risk | Evidence |
|---|---|---|---|---|---|
| `action_relation` | Candidate-to-objective relation | DELIVER/CONTINUE/VERIFY/DIAGNOSE/REPLAN roles; verification intensity, retry policy | None shown; host relation facts already gate; model at chance | **High**: reorders candidates on near-uniform probs (0.29–0.31) | 1/5 blinded |
| `progress_relation` | What the completed activity established | VERIFY vs DELIVER vs DIAGNOSE vs RECOVER | Only where host `progress_class` absent; host baseline already 2/3 on real trace | High: collapse to `verification_progress` biases verification intensity upward for no-progress cases | 1/6 blinded; hierarchy 1/6 |
| `retry_relation` | Is a repeat equivalent vs new | RECOVER/VERIFY/REPLAN/DIAGNOSE; retry_policy | None shown; host counters/descriptors are authority | High: `equivalent_retry` collapse would force RECOVER/REPLAN on materially-new work | 1/4 blinded, both claim conditions |
| `goal_drift` | Is sustained work displacing required work | REPLAN/DIAGNOSE/REINSPECT vs CONTINUE/DELIVER | None shown (0/8 recall); CFW host drift signals exist | **Very high**: 100% specificity means real drift is never flagged — silent lost-work continuation | 0/8 drift recall, 4 formats |
| `route_preference` | Which legal StateWork fits | Reorder eligible StateWork pair (rank_stateworks at ≥0.80) | Structurally positive: 2/2 with legal-route contrast; deterministic legal-order baseline (first legal route) only 1/2 on the two canary cases | Moderate: uncalibrated (coin-flip on one case); still bounded to legal pair, cannot violate CSW | 2/2 blinded, n=2 |

**Utility assessment:** the `semantic-role-utility-v1` values are **mechanistically present but outcome-unvalidated heuristics** (candidate_generator `_ROLE_UTILITY`; stated in code and README). For families with collapsed distributions, they are effectively arbitrary and potentially harmful (prediction correctness ≠ policy benefit; the mapping assumes a correct label). For `route_preference` the utility is simply expected mass on the first StateWork preference — closer to a decision quantity, but still requires calibrated probabilities to be meaningful. Recommendation: do not actuate any family whose utility rests on an uncalibrated collapsed distribution.

---

## H. Alternative strategy comparison (mission §9)

| Strategy | Predictive improvement (evidence) | Downstream improvement | Complexity | Compute | External calls | Data | Latency | Risk | Reversible | Validation needed |
|---|---|---|---|---|---|---|---|---|---|---|
| **A. Keep Laya, improve projection** | Unlikely: 19-case input complete; prompt variants fail | Unknown | Low | None | None | None | Same | Low | Yes | — **rejected**: cause already separated (input is not the defect) |
| **B. Simplify to narrow semantic distinctions** | Plausible for binary route; disproven for drift/retry/progress formats | Unknown | Low | None | None | None | Same | Low | Yes | Decisive experiment G |
| **C. Domain-specialize Laya** | Plausible if DP-labeled data exists | Unknown | High | 4–5 h 2×T4 (published recipe); 0.8 GB checkpoint | None (local) | ~1,200 labeled decisions per family to boot | Similar | Overfit; data absent today | Checkpoint pinning | Preceded by corpus build — **deferred, not now** |
| **D. Replace provider (other local semantic model)** | Unknown (no candidate cached) | Unknown | Medium | Unknown | Download authorization | — | Unknown | Medium | Yes (provider abstraction) | **Deferred** pending G; requires authorization |
| **E. Reduce reliance on learned semantics** | Baseline v3 is conservative and already ties Laya (2/3) | Preserves deterministic system | Minimal | Zero | None | None | Zero | **Lowest** | Yes | None beyond G comparisons | — **selected as primary** |
| **F. Shadow-only retention** | None (by design) | None until qualified | Minimal | Shadow inference only | None | None | Shadow latency only | Low | Yes | Qualification corpus | — **selected as operating mode** |

**Selected:** **E (primary; deterministic authority preserved, Laya reduced to shadow) with B (narrow `route_preference` as the only qualification candidate) — resolved by the executed Stage-B screen. F is the operating mode.** **Stage B ran 2026-10-10: the route screen FAILED the preregistered gate** (top-1 8/8 but mean confidence margin 0.121 < 0.15 with coin-flip hard cases and uncalibrated confidence), so the bounded sequence terminates at "keep Laya shadow-only; deterministic-plus-shadow is the end state." The route-qualification branch is not entered until a checkpoint or calibration demonstrates confidence separation ≥ 0.15 with calibrated probabilities on an independent balanced set.*

Why E+B wins: it is the only path that (1) cannot make the system worse, (2) costs nothing beyond already-installed local inference, (3) converts the largest current uncertainty (can this checkpoint do *any* DP-valuable semantics?) into a decision with ~15 minutes of offline CPU, and (4) preserves the option to specialize/replace later once real labels exist — without spending them now.

---

**Decision confidence:** MODERATE — the recommendation is supported by source analysis, controlled diagnostic evidence (blinded probes, token audit, real-trace DB), and the executed Stage-A/B route screen. The negative half (4/5 families unsuitable) is HIGH confidence. The positive half (route_preference qualify-ability) was **LOW** and is now resolved negative at the exploratory bar: the checkpoint ranks route fit correctly (8/8 argmax) but fails the confidence-separation floor on the discriminating cases.

**Rejection rationale (why not the others now):**
- **A (projection improvement):** rejected because the input contract is verified complete/untruncated and prompt variants (binary, compact, hierarchical, evidence-boundary, supplied-claims, noul) all failed to repair discrimination; the projection is not the bottleneck.
- **C (domain specialization):** rejected *now* — no DP-labeled corpus exists (3 real labels total); training without evaluation design would violate the Mission; revisit after ≥ 100/30 corpus exists per family, or after experiment J if any family passes.
- **D (replacement provider):** rejected *now* — no alternative checkpoint is cached; evaluating requires download authorization; the provider abstraction is retained so D is a drop-in later if J fails and a replacement is authorized.
- **F (shadow-only forever):** not rejected, it is the operating mode; the decision is only about which family may aspire to leave shadow.

**Change-of-course conditions (exact results that justify revising the recommendation):**
1. If experiment J shows any currently-failing family (action/progress/retry/drift) at balanced accuracy ≥ 0.80 with confidence separation ≥ 0.15 on ≥ 10 blinded cases AND ≥ its deterministic baseline → promote that family to qualification instead of freezing it, and re-open Strategy C planning with the corpus plan.
2. ~~If `route_preference` passes J but fails the real-corpus qualification…~~ **Superseded 2026-10-10:** Stage B failed the confidence-separation floor (0.121 < 0.15), so the route screen did not clear. The operative condition is now: route_preference may be revisited only when a checkpoint/calibration demonstrates confidence separation ≥ 0.15 with calibrated probabilities on an independent balanced set; only then re-run Stage B, then qualify on the real corpus (acc ≥ 0.80, Brier ≤ 0.25, ECE ≤ 0.12, ≥ 100/30/20) before any actuation.
3. If a later local checkpoint/model (Strategy D) is authorized and demonstrates ≥ 0.80 balanced accuracy on the same blinded cases with ≤ 2× the route latency → replacement is the superior strategy; the provider abstraction makes this a configuration change.
4. If the current checkpoint's confidence calibration is repaired (valid temperatures or refit) and discriminative power improves on the blinded set, re-run J before any other action; calibration alone does not create discrimination.

## I. Detailed code-edit notes (minimum coherent change set; all local, no authorization needed for the code edits themselves)

All changes are small, additive, and do not alter deterministic selection. They harden the advisory path against the verified defects. (No new orchestration layers, event stores, or runtime abstractions.)

1. **Disable non-`route_preference` families from the default enabled set.**
   - File: `lib/system_one_service.py` (default `enabled_question_families`) and README env table.
   - Current verified behavior: all five families enabled when Laya present (shadow).
   - Root cause: 4/5 families fail blinded discrimination; each represents a reorderable-pair risk inside an experiment.
   - Change: default enabled set = `{"route_preference"}`; other families remain available by explicit host opt-in but never default; document that their shadow collection continues via opt-in only.
   - Tests: configuration + frame-factory tests assert default set; planner tests for the other families still pass (planner emits plans; service filters).
   - Acceptance: `digital-psychology-laya-doctor` shows only `route_preference` enabled by default.

2. **Freeze `route_preference`'s calibration/qualification contract to the current legal-route shape.**
   - File: `lib/system_one_calibration.py` / candidate record (no logic change if already keyed by family+question spec — verify `record.valid_for(frame)` covers the dynamic criteria; add an explicit test that a calibration bound to a 2-route shape cannot silently serve a 3-route frame; the cohort `context` already includes `len(legal_routes)` and hash — confirm and keep).
   - Root cause: dynamic option sets could otherwise let a qualified binary calibration apply out of distribution.
   - Acceptance: qualification eligibility requires `context` match including route count/hash (already implemented in `SystemOneCalibrationLifecycle`; add regression test).

3. **Require route-confidence abstention.** Already gated by `minimum_semantic_confidence = 0.80` + calibrated distribution + expected utility; verify threshold applies to the *calibrated* mass on the preferred StateWork (it does via `statework_fit_support`/`rank_stateworks`). Add a unit test that a 0.50/0.50 calibrated route answer cannot reorder (currently the selector requires margin > 0 only inside a qualified experiment; a coin-flip distribution at calibrated 0.80 is impossible, but the test should pin the boundary).
   - File: `lib/control/policy_selector.py` + tests.

4. **Add an outcome-validation note + guard on `_ROLE_UTILITY`.** Do not change the values (no evidence to set better ones); add a `utility_status: "unvalidated_heuristic"` marker to receipts/candidates so any future experiment ID cannot claim outcome support (file: `lib/control/candidate_generator.py`, `SemanticCandidateEvidence`; receipts in `lib/control/semantic_influence.py`). Cheap, honest, audit-proof.

5. **Retain and publish the token-coverage audit as a standing diagnostic.** Add `state_tokens_used/dropped` to the blinded-probe result rows (the script currently drops them; the retained probe lacks them). This makes future probes self-verifying (file: `scripts/evaluate_laya_semantic_families_blinded.py`).

6. **README correction:** replace any claim that checkpoints "can inform bounded interventions" with the verified statement that no family is qualified and only `route_preference` is a candidate, plus the decisive-experiment gate. (README currently is careful, but the family-default change must be reflected.)

No changes to: `_ROLE_UTILITY` values, calibration thresholds, activation commands, `SemanticQueryPlanner` heuristics (they are labeled as heuristics and only prioritize queries, not decisions), CFW/CSW boundaries, the provider abstraction, or the deterministic baseline.

---

## J2. Executed Stage A/B route screen result (2026-10-10)

Stage A (host-only, no inference): **passed** — the CFW cohort's
`semantic_route_preference` fixture provides a genuine two-route ambiguity
(`gitter` vs `infrae`), both legal, with an independent evidence-backed
evaluator (`route_matches_evidence`, `route_evidence_inspected`) and a
deterministic comparator (`rank_stateworks()`).

Stage B (narrow route screen, executed): **reclassified by the third review**
(`reports/laya-route-screen-stage-b-rereview-2026-10-10.json`). The raw mean
confidence margin (0.121) is **descriptive, not a suitability veto** — it does
not establish model unsuitability. Verdicts: typed integration PASS; route
classification on eight authored cases PASS (8/8, small-set diagnostic);
incremental advantage over deterministic ranking OBSERVED +1/8, INCONCLUSIVE;
robustness UNVERIFIED; confidence calibration UNVERIFIED; advisory eligibility
NOT_ESTABLISHED — the operative limitation is the calibration/abstention
contract: at the maximum permitted sharpening temperature (0.25), the highest
achievable calibrated confidence on these eight distributions is 0.845, below
the 0.90 advisory gate (verified numerically; a qualified calibration would
yield **zero advisory-eligible decisions** on them).

**Disposition:** Laya remains shadow-only / non-actuating. The bounded sequence
terminates at "keep non-actuating; deterministic-plus-shadow is the end state"
until the calibration-readiness gate can be reached (a checkpoint/calibration
that demonstrates trustworthy calibrated selective predictions at useful
coverage on independent data — now quantified via
`calibration_metrics(..., calibration_record).selective_advisory_coverage`).

## J. Minimum decisive experiment (mission §7/G; small, offline, bounded)

**Goal:** Decide whether this checkpoint can deliver *any* DP-valuable semantic discrimination worth qualifying — specifically the `route_preference` (binary legal-route fit) contrast — and confirm the four collapsed families are not rescued by a final narrow formulation.

**Design (all local, cached checkpoint, network off, isolated `/tmp` output):**
- **Condition A (baseline of record):** replay the 19-case blinded set (inputs retained) — 19 inferences through `SystemOneService`/`Agent.decide` with `laya_state_projection` v7. Baseline metrics already known (A: action 1/5, progress 1/6, retry 1/4, drift 1/2, route 2/2).
- **Condition B (narrowed route contrast):** ≥ 10 hand-authored blinded legal-route cases (repair vs verify vs extend-eligibility variations) with the exact production projection; plus **deterministic comparator** `rank_stateworks`/`statework_scores` and the legal-order baseline on the same cases.
- **Condition C (binary narrowing of other families):** one final 2-option contrast per family — `goal_drift` (yes/no — already fails at 0/8, re-run for the record), `action_relation` (unrelated vs not), `progress_relation` (no_progress vs any), `retry_relation` (equivalent vs not) — 6–10 blinded cases each. This is the last formulation attempt; **stop condition**: if any family still ≤ majority-class + 0.15 balanced accuracy, freeze it (shadow-only, no further prompt rewrites).
- **Controls:** identical expected labels; identical fixture provenance; option-order invariance (2 orders); paraphrase invariance (1 paraphrase); checkpoint identity fixed; no calibration applied (report raw probs); per-case token/option coverage recorded (add columns per change 5).
- **Measures:** per-family accuracy, balanced accuracy, macro-F1, confusion, majority-class baseline, deterministic baseline, per-class raw confidence, token coverage, latency.
- **Cost limits:** ≤ 120 inferences total (≈ 15–20 min CPU wall at ~3–14 s each, single worker); stop early if any probe family shows zero discrimination on the first 6 cases.
- **Decision rule:** PASS for `route_preference` iff balanced accuracy ≥ 0.80 AND raw-confidence separation (mean |p_best − p_second|) ≥ 0.15 AND ≥ deterministic comparator on the same blinded cases. PASS for any other family iff balanced accuracy ≥ 0.80 AND ≥ deterministic baseline. Otherwise FAIL → family frozen; if all fail → close the loop at "deterministic-only + shadow" (Strategy F end state).

**Why it is minimal:** it uses only already-retained inputs + ≤ 2 new small authored sets; it is the smallest set of inferences that can validate or invalidate the selected strategy; it needs no trajectory authorization, no downloads, no calibration fitting. A negative result removes every remaining reason to invest further in this checkpoint.

---

## K. Staged execution plan (mission §10/H)

1. **Immediate local work (this week, no authorization):** changes I.1–I.6 + run experiment J. Expected output: `RouteB`/`RouteA` comparison JSON + frozen-family disposition.
2. **Next predictive-quality gate:** if route PASS, run `digital-psychology-laya-qualify` on retained authenticated event DBs (currently 0 paired outcomes; real corpus build required first): collect ≥ 100 authenticated decision→independent-outcome pairs across ≥ 30 independent trajectories for `route_preference` only, with work-mode/deliverable cohort coverage ≥ 20 each. **Data acquisition understanding:** replay/canary runs do not count; only new authenticated agent/host traces with later independent validator labels count; each trajectory must be a fresh independent task instance.
3. **Next advisory-actuation gate:** only after a qualified candidate (acc ≥ 0.80, Brier ≤ 0.25, ECE ≤ 0.12 on holdout) passes independent host review and activation: one bounded experiment ID, route family only, one legal-pair reorder allowed, circuit breaker armed, `useful_influence_rate` + receipt-linked outcomes tracked.
4. **Next real-agent efficacy gate:** matched deterministic vs advisory arms, ≥ 30 independent matched pairs per arm, predeclared efficacy gates; primary measure = independently verified task outcomes (host/validator authority), not Laya query count.
5. **Production qualification gate:** unchanged gates (B.7); additional requirement: per-family calibration disjoint from train/holdout, frozen model+protocol pins, retry outcomes still unsupported pending a retry evaluator contract.

**Data-collection cost bounds:** each authenticated trajectory ≈ 1 external model call + ≤ ~10 host actions (per retained cohort run: 1 model call, 8 host actions, < $0.05 API-equivalent upper bound; billed $0). 30 independent trajectories ≈ $0 billed (ChatGPT subscription) + ~1–2 h host time. No separate billed inference API is required for DP's Laya side (local CPU). If RouteB fails, stop data collection for Laya entirely.

---

## L. Runtime performance and break-even (mission §11)

Measured (warm, CPU, `laya-local-latency-2026-10-10.json`): p50/p95 per family — action 6.9/8.2 s, drift 4.7/6.0 s, progress 8.8/13.8 s, retry 8.5/9.0 s, route 2.8/4.0 s. Warmup 5.6–22 s (init timeout 180 s). One question per decision (planner emits one family), so latency is per candidate-decision boundary; queue is serialized (one worker), so concurrent ticks queue. Cold-start precedes first actionable query and is bounded by the 240 s host-preparation deadline.

**Feasibility for a real-time behavioral controller:** borderline for progress/retry (8–14 s per decision), acceptable only if the decision cadence is slow (multi-minute) and the query is genuinely decision-critical. Route (2.8–4.0 s) is the only family whose latency is tolerable per decision.

**Break-even requirement (reporting, not claiming):** with no measured outcome benefit, compute the break-even as: `E[time-saved-by-better-decision] × P(correct flip) > inference overhead (≈3–14 s) + induced behavioral overhead`. Under the current evidence (no correct flips observed; 0 influenced decisions), no positive break-even is demonstrated. The experiment J provides the first discriminating evidence; until then, the honest statement is *"the value requirement is E[saved] × P(flip) ≳ 5–15 s per queried decision boundary, unmeasured."*

---

## M. Residual blockers (mission §12/I)

**External (cannot be fixed locally):**
1. Zero authenticated real-labeled corpus (0 paired outcomes in default stores; 3 labels in the one retained cohort DB). Only new authorized agent/host traces + independent validator labels can fill it. Requires trajectory authorization (explicitly outside this investigation's grant).
2. No alternative local checkpoint/model cached; evaluating Strategy D requires download authorization (network).
3. Codex App Server `workspace_routing_unavailable` (`-32603`) blocks authenticated agent trajectories in this environment (documented against 0.160.1/0.162.1; `log_dir` workaround ineffective here). This is the operational blocker for the efficacy study; not bypassed per policy.
4. Training data for Strategy C does not exist; collecting ≥ ~1,200 labeled decisions per target family is a real cost/authorization item.

**Local (fixable without authorization):** the default-enabled-family set (I.1), utility-status marker (I.4), probe coverage columns (I.5), route calibration-shape test (I.2), README family default (I.6), and the decisive experiment (J).

---

## N. Final verdict (mission §13/J)

1. **Is the current Laya checkpoint suitable for the semantic decisions DP needs?** **FAILED for action, progress, retry, and goal-drift** (demonstrated diagnostic limitation: blinded collapse to single classes, near-uniform probabilities, 0/8 drift recall) — domain-mismatch is a strong hypothesis, not a proven causal diagnosis. **For route_preference, two separate answers:** *ranking/discrimination* is **PROMISING but unproven** (Stage-B 8/8 on authored cases, +1/8 over the deterministic ranking; the mode-controlled contrast screen shows 7/10 vs 5/10 with 3/5 hard alternatives correct — the checkpoint uses *some* semantic evidence beyond the mode-tag heuristic); *usable advisory support* is **NOT ESTABLISHED** — raw-margin 0.121 is descriptive, the real limit is calibration/abstention (max achievable confidence 0.845 at T=0.25 < 0.90 gate), so no calibrated selective prediction is currently actionable.
2. **Is the existing DP/Laya integration capable of translating accurate predictions into beneficial behavior?** **CAPABLE BUT UNPROVEN.** The machinery (bounded legal pair, calibrated-distribution + expected-utility gate, experiment ID, confidence ≥ 0.80, margin, circuit breaker, CFW/CSW authority) is sound and safe; but the utility mapping is unvalidated heuristics, no family is qualified/calibrated, and zero influenced decisions have ever been observed — so the integration is *safe* and *auditable*, not *shown beneficial*. Verified verdict: **PARTIALLY VERIFIED** — integration mechanically capable and safe (VERIFIED); beneficial translation of predictions into behavior (UNVERIFIED).
3. **Best course of action to demonstrate and improve whole-system effectiveness at the lowest reasonable cost?** **The deterministic system stays authoritative (Strategy E); Laya is shadow-only / non-actuating (F) for all five families.** The Stage-B margin-based rejection is withdrawn; the correct framing is: Laya shows *promising but unproven* route discrimination and *zero advisory coverage* under the current calibration contract. The review's P0 fixes are implemented and green (504+ suite, targeted robustness suite 21 passed / 1 documented xfail): the planner now enforces enabled families through production, `RouteDecisionContext` is the single route contract, selective advisory coverage is reported, and the Stage-B/mode-controlled verdicts separate discrimination from calibration readiness. The immediate continuation is **not** corpus collection; it is to determine whether a properly estimated confidence threshold (with independent-data justification) and/or domain calibration can produce trustworthy selective predictions at useful coverage — with `calibration_metrics().selective_advisory_coverage` as the required report, and domain specialization via Laya's native `{state, questions, expected}` fine-tuning only if stronger hard-negative evidence accumulates.


---

## O. Held-out preregistered screen (third-review directive, 2026-10-10)

**Directive honored:** freeze the integration; run **one** stronger offline
experiment before any real-agent corpus collection. Per the third review:
~40–60 genuinely new route decisions covering same-mode/different-evidence,
reversed descriptions, misleading mode tags, and equal-lexical-similarity
contrasts; compare Laya against `rank_stateworks()` **and** a description-aware
deterministic baseline; report paired wins/losses, accuracy, balanced accuracy,
per-case probabilities, option-order stability, and latency. No gate, no
temperature-range change, no retraining, no corpus collection.

**Design (preregistered in `laya-route-screen-heldout-manifest-2026-10-10.json`
before any inference):** 48 genuinely new authored cases (family A 20,
family C 16, family D 12) + 10 presentation-order perturbations (family B,
option-order reversal on 8 cases and description-sentence-order reversal on
2) = 58 bounded inferences (ceiling 64), offline, pinned checkpoint
(`1a793eb`, weights `4fa56de7…`). Labels authored from evidence semantics
before any Laya call.

**Baselines:** production `rank_stateworks()`; TF-IDF cosine over route
descriptions (fit on this screen's texts); Jaccard word overlap; majority
class. Family D's equal-lexical tie was verified: mean TF-IDF gap 0.070,
max 0.196; mean Jaccard gap 0.048, max 0.141.

**Result — verdict `NO_SUSTAINED_ADVANTAGE` (the preregistered negative
branch):**

| metric | Laya | rank_stateworks | TF-IDF | Jaccard | majority |
| --- | --- | --- | --- | --- | --- |
| overall accuracy (n=48) | 0.667 | 0.333 | **0.875** | 0.875 | 0.208 |
| family A (same mode, different evidence) | 0.700 | 0.500 | 0.900 | 0.950 | — |
| family C (misleading mode tags) | 0.625 | 0.000 | 0.938 | 0.938 | — |
| family D (equal lexical similarity) | 0.667 | 0.500 | 0.750 | 0.667 | — |
| balanced accuracy | 0.688 | — | — | — | — |
| mean margin / Brier | 0.055 / 0.468 | — | — | — | — |
| ordering stability | 0.80 | — | — | — | — |

**Paired wins/losses on disagreements:** vs TF-IDF 2W/12L (win rate 0.143);
vs `rank_stateworks` 20W/4L (win rate 0.833); vs Jaccard 2W/12L.

**What this settles (review §"Decision after that experiment," third branch):**
Laya's apparent advantage over the deterministic ranking does **not** survive a
description-aware baseline. The cheap TF-IDF/Jaccard lexical baselines reach
0.875 on the same 48 held-out cases while Laya reaches 0.667; on the 14 cases
where they disagree, Laya wins only 2 and loses 12. Confidence remains
unusable (Brier 0.468, near the two-option random-guessing floor; mean margin
0.055; only 0.80 option-order stability). Family D shows no semantic edge:
all methods cluster at/near chance where only semantics separate the routes.

**Disposition (preregistered negative branch, executed):**
- Do **not** collect a route-specific calibration corpus for this checkpoint.
- Do **not** activate; Laya remains shadow-only / non-actuating for all five
  families (Strategy F unchanged).
- Do **not** change the 0.90 advisory gate or the [0.25, 5.0] temperature
  bounds (constraint honored; no calibration was applied or fitted).
- Deterministic DP + `rank_stateworks` remain authoritative.
- If route discrimination is revisited later, the evidence points to a
  **description-aware lexical baseline (TF-IDF/Jaccard over route
  descriptions)** as the stronger, cheaper first component — not this
  checkpoint's semantic representation.
- The integration work (route pair binding, family gating, calibration
  isolation, selective-coverage reporting, 546-test suite) remains valid and
  valuable; only the model-use conclusion changes.

**Artifacts:** `reports/laya-route-screen-heldout-manifest-2026-10-10.json`
(preregistration), `reports/laya-route-screen-heldout-2026-10-10.json`
(results, 58/64 inferences),
`reports/laya-route-screen-heldout-2026-10-10.md` (full report),
`scripts/route_screen_heldout_2026-10-10.py` (reproducible offline harness).
All uncommitted in the advisory worktree, per the no-Git-mutation constraint.

**Freeze verification:** the implementation is byte-identical to the state
that passed the full suite (546 tests) before this screen: `git diff --stat`
shows the same 7 modified lib/test files with 542 insertions / 147 deletions,
and only the new artifacts (manifest, results, report, script) were added.
The 16 alignment tests pass in 0.17s on the frozen state.

**xfail evidence retained:** the documented progress-paraphrase `xfail` from
the opt-in robustness suite remains visible as model-instability evidence, not
counted as resolved (per the review's qualification).

**Operating rule honored:** *Preserve the functional deterministic system. Give optional semantic inference executive influence only when its information is demonstrably more useful than the decisions the system can already make without it.* — Currently it is not demonstrably more useful (2/3 = 2/3), and this plan defines exactly what "demonstrably" must mean before that changes.

## P. Route-selection remediation (fourth-review directive, 2026-10-10)

**Scope:** Bounded deterministic route-ranking project per the rereview directive. All changes uncommitted; no Laya inference; no commits or pushes.

### P.1 Root-cause findings

| Finding | Evidence | Disposition |
|---|---|---|
| `observe_task()` raises `ValueError` for `semantic_route_preference` family | Reproduced with actual route fixture; `_observe_action()` calls the dispatcher for every host action | **Fixed** — new branch in `CFW/scripts/cohort/task_semantics.py` |
| Deterministic `rank_stateworks()` always ranks gitter first; infrae cases all fail (0/20) | Cohort measurement: Recall@2 = 1.00, conditional ranking accuracy = 0.50 on 40 real CFW fixtures | **Structural limitation documented** |
| Jaccard scorer matches deterministic exactly on cohort infrae cases (0/20 correct) | Bag-of-words overlap cannot capture structured symptom-timing rules | **Stop rule applies** |
| Jaccard resolves 8/8 multi-route cases | Direct vocabulary overlap between task text and TUI route purpose | **May be evaluation-specific** |

### P.2 Error decomposition

| Set | n | Recall@2 | Conditional ranking | E2E det | E2E Jaccard |
|---|---|---|---|---|---|
| Cohort gitter/infrae (40) | 40 | 1.00 | 0.50 | 0.50 | 0.50 |
| Multi-route (8) | 8 | 0.75 | 0.667 | 0.50 | 1.00 |
| Combined (48) | 48 | — | — | 0.50 | 0.583 |

The cohort infrae failure is a **ranking/information problem**, not a shortlist problem (recall@2 = 1.00). The discriminating signal is structured host evidence (symptom-timing, degradation-sustained), not lexical overlap.

### P.3 Applied changes

| PR | Repository | Files | Status |
|---|---|---|---|
| A — Route evaluator correctness | CFW | `scripts/cohort/task_semantics.py` + `tests/test_route_preference_observe_gap.py` (8 tests) | ✅ Green |
| B — Evaluation isolation | DP | Bottleneck measurement artifact (moved to `reports/route-bottleneck-2026-10-10.json`) | ✅ Complete |
| C — Diagnostic Jaccard scorer | DP | `lib/control/statework_selector.py` (`jaccard_route_scores()`) + `tests/test_route_jaccard_scorer.py` (8 tests) | ✅ Green; **not activated** |
| D — Bounded integration | DP | Not started | ⏸ Blocked by C disposition |

### P.4 Test evidence

| Suite | Result |
|---|---|
| CFW `tests/test_route_preference_observe_gap.py` | 8 passed in 0.36s |
| CFW full `tests/` | 221 passed (7 deselected) |
| DP `tests/test_route_jaccard_scorer.py` | 8 passed in 0.30s |
| DP targeted selector suite | 48 passed in 0.40s |
| DP full suite | Attempted; hung >50 min (known issue). Killed. Scorer is additive with zero call-site changes; targeted tests cover all affected modules. |

### P.5 Disposition

**Retain Jaccard experimentally (diagnostic-only). Do NOT activate.**

The cohort proves lexical-only scoring cannot resolve structured-evidence cases. The 4 multi-route wins may be evaluation-specific. The real bottleneck is the absence of structured host-fact rules (symptom-timing, degradation-sustained signals) — a Phase 4 finding requiring separate implementation.

### P.6 Remaining evidence for end-to-end efficacy

If the structured-evidence gap is resolved and disposition changes: run 8 multi-route cases as a CFW cohort trial with the fixed route evaluator (PR A) to confirm downstream agent behavior changes. Requires fresh user authorization.

### P.7 Artifacts

- `reports/route-selection-remediation-2026-10-10.md` — full remediation report (advisory worktree)
- `reports/route-bottleneck-2026-10-10.json` — per-case measurement data (advisory worktree)
- `/tmp/route-bottleneck-2026-10-10.json` — original measurement artifact
