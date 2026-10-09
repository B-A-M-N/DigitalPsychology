# Laya offline quality audit

**Date:** 2026-10-09

**Scope:** DigitalPsychology’s local Laya frame, deterministic fallback, retained evidence replay, and offline family checks.

**Inference boundary:** All model calls in this audit used the locally pinned Laya checkpoint. No Codex/API calls, new agent trajectory, training, calibration activation, threshold change, or qualification action occurred.

## Changes made

The System-1 frame now carries an optional trusted `criteria_before` snapshot alongside current `criteria`. The in-process runtime supplies its last committed goal set. The control path supplies the preceding committed tick basis, falling back to the session’s initial goal set on its first tick. If a prior snapshot or its projected criterion claims are unavailable, the field stays `null`; DP does not infer or fabricate history.

The canonical DP frame schema is now protocol **2.6.0**. The `progress_relation` Laya projection is `laya-semantic-projection/v3` and exposes `criterion_progress_context.before` and `.after`, containing criterion claims, statuses, and evidence statuses. It also includes the completed host activity. The projection continues to omit the host’s direct progress label and host-declared relation.

The retry projection now groups the proposed approach with recorded failed approaches, and leaves out the repeated task-objective wording while retaining criterion claims. Retry instructions distinguish a changed action or evidence target under the same premise from an equivalent retry. The question labels did not change. Provider and calibration compatibility hashes update with the new protocol; calibration thresholds and activation state did not change.

The deterministic host candidate pool and CFW/CSW authority remain the decision authority. Laya answers remain advisory; incomplete, stale, or unavailable results fall back to the deterministic path. No monkeypatching was introduced.

## Family input audit

| Family | Model-facing evidence | Guardrails |
| --- | --- | --- |
| `action_relation` | Objective, unfinished criterion claims, candidate action and purpose | Host relation omitted; answer-like blocker hints filtered; options sorted. |
| `progress_relation` | Before/after criterion status and evidence status, completed host activity, result class, artifact change, evidence kinds, affected criteria | Host progress class and relation omitted. A missing prior snapshot is explicit. |
| `retry_relation` | Proposed action and purpose grouped with host-recorded failed hypothesis, mechanism, target, and affected criteria | Candidate comparison remains a hypothesis. It does not become a host fact or trigger. |
| `goal_drift` | Objective, unfinished criterion status, candidate purpose and affected criteria | Host-authorized criteria remain the scope; answer-like blocker hints filtered. |
| `route_preference` | CSW-eligible legal routes with workflow purpose/state | Laya cannot introduce or activate a route. |

## Authenticated evidence replay

The prior v2 audit recorded three authenticated `progress_relation` outcomes from one task trajectory and fixture: Laya accuracy 2/3, host-rule baseline accuracy 2/3, with no holdout or independent-cluster interval. Those figures remain a historical v2 result, not a v3 measurement. The source event/runtime database used for that run is not present in the current workspace, so the v2 result cannot be independently replayed here.

I ran the current read-only replay against DigitalPsychology’s default event and runtime stores. It found **0 paired outcomes, 0 authenticated frames, and 0 replayed inferences**; it made 0 model/API calls. The separate October 7 cohort record describes one host-controlled task with four actions and one recorded outcome, but no agent model generated those actions and the private trial database was not retained. It is integration evidence, not an independent agent-prediction sample.

Offline replay now supports authenticated legacy v2.5 frames. It verifies their original protocol, projection, question, state, and request digests, associates the original event using the raw frame digests, then creates a current replay frame with `criteria_before: null`. It uses the current question registry and preserves the source-frame digest. A regression test covers this normalization. This path preserves replay when the original database is available; it does not create a before snapshot for old data.

No real labeled v3 sample is currently available. Laya’s improvement over the deterministic host baseline, its five-family error rates, calibration, policy effects, and task efficacy remain unmeasured. No qualification or activation claim is supported.

## Synthetic local checkpoint canary

The opt-in canary ran **21 local inferences**: four variants for each family plus one oversized host-state case. The variants changed objective phrasing, option order, and incidental criterion IDs. All five families matched their synthetic expected class across the four variants:

| Family | Expected and observed class |
| --- | --- |
| `action_relation` | `required_verification` |
| `progress_relation` | `verification_progress` |
| `retry_relation` | `same_premise_new_action` |
| `goal_drift` | `no` |
| `route_preference` | `verify` |

The oversized-frame check retained the criterion and candidate evidence in the compact projection, excluded irrelevant host context, and reported complete, untruncated coverage. These are synthetic correctness and invariance checks, not independent trajectories or predictive-performance evidence.

The checkpoint still reports an invalid `choice:11+` temperature and falls back to `0.5`; confidence in that bucket is uncalibrated. The replayed real progress question had six options, and the synthetic route question had two, so the larger-choice confidence path remains untested.

## Validation

- Full DigitalPsychology suite: **448 passed, 14 skipped** in 102.61 seconds.
- Opt-in local Laya canary: **6 passed**, 21 inferences, no external calls; one known checkpoint temperature warning.
- Final focused frame, control-path, compatibility, replay, and fallback tests: **11 passed**.
- Ruff diff-line audit: **0 findings on added lines**. Ruff reports 123 existing findings across the selected files, outside the added lines.
- Read-only default-store replay: 0 paired outcomes, 0 frames, 0 model calls.

## Conclusion

The frame now provides the temporal criterion evidence missing from `progress_relation`, and the local checkpoint produced stable synthetic labels for all five families under the tested perturbations. The retry prompt/projection needed a correction before that family met the synthetic known-truth case.

The current workspace has no authenticated v3 outcomes with which to show that Laya beats the host baseline. The earlier 2/3 result belongs to a one-trajectory v2 sample and remains descriptive. Qualification, calibrated confidence, policy influence, and task efficacy are still unproven.
