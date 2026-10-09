# Laya offline quality audit

**Date:** 2026-10-09

**Scope:** DigitalPsychology’s local Laya boundary, deterministic fallback, retained authenticated evidence, and offline replay.

**Inference:** One read-only replay of three retained progress decisions plus an opt-in synthetic consistency canary. No Codex/API calls, new agent trajectory, checkpoint change, or calibration activation.

## Findings

The native adapter previously discarded Laya’s token and truncation usage. It also passed one broad state object to every family. The family projection now has an explicit version, uses stable option ordering, records bounded inference diagnostics, and requires known complete tokenization, no truncation, and full option coverage before an answer can be advisory-eligible.

The audit found direct answer leakage in two model-facing fields. `action_relation` received the host-declared relation, and `progress_relation` received the host progress class. Both fields are now omitted from the relevant projections. Blocker hints also filter question and answer labels. Full host facts remain in the retained tick basis.

Laya’s hypotheses no longer become host-confirmed retry or drift conditions. The no-provider path still plans host query opportunities and builds the same deterministic legal candidate pool. A disabled planner is marked separately from an enabled semantic provider, so ordinary no-Laya sessions do not require semantic deliverable metadata.

CFW and CSW remain authority sources: the host derives legal candidates and route eligibility; Laya can only add a preference over existing candidates. An unqualified answer falls back to shadow/deterministic behavior.

## Family input audit

| Family | Model-facing evidence | Leakage/order/truncation result |
| --- | --- | --- |
| `action_relation` | Objective, acceptance criteria, candidate action and purpose, and relevant host context | Direct host relation removed; semantic answer hints filtered; options sorted before inference. |
| `progress_relation` | Host-observed activity, result class, artifact change, evidence kinds, affected criteria, and criterion status | Model-proposed activity is excluded. Host `progress_class` and action relation are excluded because they reveal the requested answer. |
| `retry_relation` | Candidate hypothesis plus host-recorded failed premise, mechanism, evidence target, and planned action | Previous and proposed approaches are distinct fields. No real replay labels exist for this family. |
| `goal_drift` | Objective, unfinished criterion status, and candidate purpose/affected criteria | Answer-like blocker hints are filtered. Criterion status remains because it is trusted evidence needed to assess whether work is still unfinished. |
| `route_preference` | Only CSW-eligible legal routes, with workflow purpose/state descriptions | Candidate routes remain host-authorized. Laya cannot introduce a route. Options are sorted. |

The protocol’s question wording already distinguishes the five targets and defines the classes. It was retained. The input projection change is versioned as `laya-semantic-projection/v2`, and the provider protocol hash changes with it.

## Retained real-trace replay

The default DigitalPsychology event and behavior databases contain no paired outcomes or committed decision frames. The retained `laya-shadow` run contains three independently receipted `progress_relation` outcomes and their immutable frames. They are three decisions from one task, trajectory, and fixture, so the runner marks them descriptive and creates no train/calibration/holdout split.

The replay used the pinned local `laya-typed-decisions` checkpoint and the current v2 projection. It did not call an external model API. All three inferences had complete option coverage, known tokenizer preflight, zero dropped state tokens, and no truncation.

| Metric | Result |
| --- | ---: |
| Paired outcomes / independent trajectories | 3 / 1 |
| Top-1 accuracy | 0.667 (2/3) |
| Macro-F1 over six registered classes | 0.133 |
| Multiclass Brier | 0.606 |
| Top-label Brier | 0.306 |
| Log loss | 1.194 |
| ECE, 10 bins | 0.293 |
| AURC | 0.111 |
| Host baseline | unavailable in these older decision events |
| Clustered confidence interval | unavailable; one trajectory/fixture |

Laya selected `verification_progress` on all three cases. The labels were one `goal_progress` and two `verification_progress`. The in-sample majority label is also `verification_progress` at 2/3; this is not an out-of-sample baseline and does not establish value over a host rule. No policy-influence receipt changed selection, and replay does not measure actuation or task efficacy.

Laya emitted a checkpoint warning that its `choice:11+` temperature was outside the accepted range and fell back to `0.5`, with confidence from affected entries uncalibrated. The replayed progress question had six options, so it did not exercise that `11+` bucket. Larger route-choice sets still need a dedicated confidence check.

## Synthetic robustness canary

The opt-in local test ran 15 inferences across five synthetic fixtures: original wording, a semantically equivalent objective paraphrase, and reversed option order. All five families preserved their selected class across the paraphrase and order change. Each response had complete option coverage and was untruncated. These fixtures are implementation checks only; they are not independent trajectories, outcome labels, or predictive-performance evidence.

## Measurement and replay changes

Calibration reporting now distinguishes multiclass Brier from top-label Brier, reports the ECE bin count and reliability bins, and includes per-class precision/recall/F1, macro-F1, confusion matrices, log loss, risk-coverage/AURC, host baseline accuracy, and trajectory/fixture cluster intervals. Dynamic route labels are validated against the exact legal route set. Provider and coverage failures are counted separately by replay.

The new `digital-psychology-laya-offline-replay` command reads authenticated events and committed tick frames through read-only SQLite connections. It verifies the event-to-tick frame link and independent outcome receipt, uses deterministic task/trajectory/fixture-disjoint splits when at least three independent clusters exist, and otherwise reports descriptive-only results. Replays retain source/replay frame digests and never count as new trajectories. No calibration or qualification thresholds were changed.

## Validation

- Full DigitalPsychology suite: **441 passed, 13 skipped** in 218.68 seconds. The additional five skips are the opt-in local Laya cases when the environment flag is unset.
- Focused authority, projection, calibration, runtime, event, and offline-replay tests: **118 passed**.
- Opt-in local Laya robustness test: **5 passed** in the Laya 0.3.22 environment.
- Ruff checks on the new replay module and its new tests: passed. Existing repository lint warnings in legacy files remain outside this change.
- Both copies of `behavior-event.schema.json` are identical.

## Conclusion

The current evidence confirms the local adapter runs, captures complete-coverage diagnostics, and is stable to the tested paraphrase and option-order perturbations. The only authenticated labeled family has weak descriptive performance equal to its in-sample majority reference, with no host baseline and no independent holdout. There is no evidence yet that Laya improves five-family prediction, policy outcomes, or task efficacy. Qualification remains unproven; additional independent labeled trajectories are still required.
