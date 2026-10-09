# Laya offline quality audit

**Date:** 2026-10-09

**Scope:** DigitalPsychology’s local Laya boundary, deterministic fallback, retained authenticated evidence, and offline replay.

**Inference:** Read-only local replay of three retained progress decisions plus an opt-in synthetic consistency canary. No Codex/API calls, new agent trajectory, checkpoint change, or calibration activation.

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

The `progress_relation` view currently combines the latest host-observed activity with the current criterion status and evidence status. The immutable frame does not contain the prior criterion snapshot needed to show a true before/after delta. The direct host progress flag remains excluded because it gives away the answer. This is an unresolved host-frame contract gap; the present evidence is not enough to claim that this family receives a complete temporal comparison.

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
| Host-rule baseline accuracy | 0.667 (2/3), derived from immutable frames |
| Clustered confidence interval | unavailable; one trajectory/fixture |

The older decision events did not persist host-baseline labels, so replay derives them from their immutable frames under `deterministic-host-baseline/v2`. That rule maps the host fact `VALIDATION_EVIDENCE` to `verification_progress`; the three derived baseline labels are recorded with source and version in the replay output.

| Family | Authenticated labeled cases | Laya result | Host baseline | Abstentions / provider failures |
| --- | ---: | --- | --- | --- |
| `action_relation` | 0 | Not measured | Not measured | No cases |
| `progress_relation` | 3 from 1 trajectory | 2/3 correct; one `goal_progress` was predicted as `verification_progress` | 2/3 correct; all three baseline predictions were `verification_progress` | 0/3; 0 |
| `retry_relation` | 0 | Not measured | Not measured | No cases |
| `goal_drift` | 0 | Not measured | Not measured | No cases |
| `route_preference` | 0 | Not measured | Not measured | No cases |

Thus Laya ties the host rule on the only measured family and sample. It is correct on both observed `verification_progress` cases and wrong on the single `goal_progress` case. The other four families have no real labeled outcomes, so their error and abstention behavior is unknown. The three progress responses were all coverage-eligible; none abstained. The in-sample majority is also `verification_progress` at 2/3. None of these descriptive comparisons is an out-of-sample value estimate. No policy-influence receipt changed selection, and replay does not measure actuation or task efficacy.

Laya emitted a checkpoint warning that its `choice:11+` temperature was outside the accepted range and fell back to `0.5`, with confidence from affected entries uncalibrated. The replayed progress question had six options, so it did not exercise that `11+` bucket. Larger route-choice sets still need a dedicated confidence check.

## Synthetic robustness canary

The opt-in local test ran 21 inferences across five synthetic families. Each family was tested with original wording, an equivalent objective paraphrase, reversed option order, and renamed incidental criterion identifiers. All five preserved their selected class across those perturbations. A separate host frame larger than 12 KB retained the relevant criterion and candidate evidence in the compact projection; local Laya remained coverage-complete and untruncated. The fixtures have no criterion evidence attached, so they also check that missing criterion evidence remains explicit. An adversarial-confidence metric test verifies that an overconfident wrong answer receives large Brier and log-loss penalties. These are implementation checks only; they are not independent trajectories, outcome labels, or predictive-performance evidence.

## Measurement and replay changes

Calibration reporting now distinguishes multiclass Brier from top-label Brier, reports the ECE bin count and reliability bins, and includes per-class precision/recall/F1, macro-F1, confusion matrices, log loss, risk-coverage/AURC, host-baseline accuracy, and trajectory/fixture cluster intervals. The host baseline is versioned; replay can derive a missing older event label from its immutable frame and records whether each label came from an authenticated event or frame derivation. Dynamic route labels are validated against the exact legal route set. Provider and coverage failures are counted separately by replay.

The new `digital-psychology-laya-offline-replay` command reads authenticated events and committed tick frames through read-only SQLite connections. It verifies the event-to-tick frame link and independent outcome receipt, uses deterministic task/trajectory/fixture-disjoint splits when at least three independent clusters exist, and otherwise reports descriptive-only results. Replays retain source/replay frame digests and never count as new trajectories. No calibration or qualification thresholds were changed.

## Validation

- Full DigitalPsychology suite: **451 passed, 6 skipped** in 194.58 seconds. The six skips are opt-in local Laya cases when the environment flag is unset. One unrelated Pydantic warning came from the shared Hermes environment.
- Final focused projection, event, and offline-replay tests: **35 passed**, including the explicit missing-evidence assertion added after the full-suite run.
- Opt-in local Laya robustness test: **6 passed** in the Laya 0.3.22 environment; 21 inferences, no API calls. The checkpoint emitted its known `choice:11+` temperature warning.
- Ruff checks on the replay module and changed tests: passed. `system_one_events.py` retains pre-existing `TRY004` and `UP012` lint findings outside this change.
- Both copies of `behavior-event.schema.json` are identical.

## Conclusion

The current evidence confirms the local adapter runs, captures complete-coverage diagnostics, and is stable to the tested paraphrase, option-order, and incidental-ID perturbations. On the sole authenticated labeled family, Laya ties the deterministic host baseline at 2/3, with one dependent trajectory and no holdout. It has not demonstrated improvement in five-family prediction, policy outcomes, or task efficacy. The progress frame also lacks a prior criterion snapshot for explicit before/after comparison. Qualification remains unproven; more independent labeled trajectories and a complete temporal host frame are still required.
