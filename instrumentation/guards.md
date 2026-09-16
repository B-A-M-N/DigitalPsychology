# Instrumentation: Compiled Behavioral Guards

Layer 2 — **small, in the runtime**. A guard is a Digital Psychology finding
established strongly enough to compile into a runtime rule. The guard lives
in CognitiveFrameWorks or the relevant StateWork; its justification, evidence,
and validation record live here.

## Guard lifecycle

A guard must pass through governed stages. Never let a single observation
rewrite runtime policy:

```text
finding
  → candidate guard
  → experiment
  → validated
  → canary
  → active
  → (stale | rolled_back)
```

DigitalPsychology does not edit system prompts. It manufactures candidates,
validates them, and ships them through the registry.

## Guard record

The machine schema is `schemas/guard.schema.json` — this document derives
from it and must not drift. The canonical record shape:

```yaml
id:                  # stable id
family:              # versioned identity family (G-completion)
version:             # integer version; key = family@version
key:                 # family@version (unique registry key)
status: candidate | experiment | validated | canary | active | stale | rolled_back

target_behavior:
rule:
location:
source_finding_ref:

scope:
  domains:
  stateworks:
  task_shapes:
  models:
  harnesses:

trigger:
priority: P0 | P1 | P2 | P3
conflicts_with:
supersedes:

estimated_tokens:
cost:

validation_receipt_ref:   # immutable receipt id; booleans are not evidence
validation:
  # informational only — lifecycle gates verify the receipt, never these
rollout:
  canary_fraction:
  canary_task_ids:
monitoring:
  active_metric:

model:               # legacy flat scoping (schema allows)
harness:
domain:
task_shapes:
evaluator:           # analysis-time evaluator mapping (allowed field)
enforcement_key:     # exact runtime behavior equivalence; deduplicates kernel/DP rules
```

Guard lifecycle gates require **immutable receipts** (ValidationReceipt /
ReceiptRegistry): `experiment → validated` needs a verifiable validation
receipt; `canary → active` needs a verifiable canary receipt. Fields like
`successful_probe_record` / `canary_success` / `blocking_regression` are never
sufficient evidence.

## Guard budget

The single most important guard against overload. Without a budget, behavior
defect → new rule → behavior defect → another rule becomes a 6000-line agent
constitution.

The guard compiler must perform, in order:

```text
deduplication
subsumption
scope narrowing
priority ranking
conflict detection
stale-guard retirement
token-cost accounting
```

Initial policy:

```text
runtime always-on:
    only the universal kernel

per task:
    only guards matching current domain/task/trigger

prefer:
    one structural workflow/state guard
over:
    five reminder paragraphs
```

The exact active count is empirically tuned, starting deliberately small —
not an arbitrary published limit. Measure mean active guard tokens/task and
recurrence of the same defect after activation.

## Example

Finding: "Agents repeatedly treat passing tests as proof that the
operator-observed defect is fixed."

We do **not** respond by loading 400 lines of behavioral psychology into
every agent. We extract one compact runtime rule:

```text
If an operator-observed defect exists,
completion requires evidence that directly exercises that defect.
```

```yaml
id: G-completion-direct-evidence
status: active
source_finding: patterns/premature-closure.md
target_behavior: premature closure / proxy completion
trigger: completion claim after operator-observed defect
rule: >
  completion_claim without evidence directly exercising the
  operator-observed defect → marked unverified
location: StateWork completion guards (e.g. WORKING ↛ DONE)
priority: P1
conflicts_with: []
supersedes: []
cost:
  estimated_tokens: 40
  expected_latency: none
validation:
  trial_count: 12
  effect: direct-evidence completion rate 0.33 → 0.79
  holdout: held-out operator-contradiction cases remained valid
  regressions: no premature-close reduction lost
  overshoot: no evidence of completion deferral beyond the defect boundary
  confidence: high (multiple trials, independent evaluator)
rollout:
  canary_fraction: 0.25
  activated_at: 2026-09-01
  rollback_condition: recurrence of premature completion without contradiction
monitoring:
  event_types: [completion_claim, validation, contradiction]
  revalidate_after: model/harness change
```

## Change discipline

- Guards are the *compiled* output of `../flows/intervene.md` experiments — never opinions.
- One behavior, one guard: a guard that also fixes unrelated behaviors is over-fitted (see `../flows/validate.md` overshoot/overfitting checks).
- Guards are re-validated after model/harness changes (`../flows/monitor.md`).
- Advice ("try not to close early") is not a guard; a guard changes what transitions are legal.
- Policy is frozen per task: framework/statework/guard versions, telemetry
  schemas, transition contracts, and SISPIS calibration pin at task start and
  do not mutate mid-task except for a genuine safety/authority containment event.

The trusted export is `compiled-guard-pack.json` under the DigitalPsychology
trusted state root. An explicit empty pack is valid when every DP guard is
stale or retired; the FrameWorks kernel remains the runtime baseline.
