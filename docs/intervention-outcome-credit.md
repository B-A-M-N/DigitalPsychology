# Intervention outcome and credit assignment

The fast controller proposes and applies temporary behavior changes. A
separate evaluator assigns their outcomes from host-resolved evidence. The
controlled agent and optional Laya/Jev judgments are never outcome authorities.

## Evidence and attribution

For each evaluation, the MCP runtime supplies:

- the exact intervention ID and runtime scope;
- a host receipt showing that the intervention was applied;
- the current revision and action index, after the intervention's baseline;
- a bounded evaluation window and any overlapping intervention IDs;
- evidence references resolved by CSW or trusted host validators.

The evaluator recognizes criterion verification/rejection, objective progress
or regression, artifact changes, materially different/equivalent attempts, and
verification-quality changes. Evidence includes a source-local revision and a
host action index; only post-baseline facts inside the declared window count.
Each reference is resolved through an injected `OutcomeEvidenceAuthority`;
source and kind must match an allowed authority. Raw model text cannot create
an outcome fact.

The result distinguishes:

- `effective`: objective progress is verified for the target criterion in an
  isolated window and the intervention has an applied receipt;
- `ineffective`: the complete evaluation window contains host validation that
  all target criteria remain unmet;
- `regressed`: independent evidence confirms objective regression in an
  isolated window;
- `unattributed`: progress/regression occurred without an applied receipt,
  while another intervention could explain it, or a comparative evaluator
  found that the progress was not attributable to this intervention;
- `inconclusive`: the baseline, validation, or evaluation window is
  insufficient;
- `expired` / `withdrawn`: lifecycle closure only, never effectiveness claims.

`attribution=supported` means the evidence supports an isolated local
assessment; it does not claim that one trajectory proves universal causality.
A trusted host experiment evaluator can attach a `comparison_id` and a
`matched_baseline` or `matched_baseline_holdout` design to explicitly record
`progress_not_attributed`, `intervention_no_effect`, or
`intervention_effect_confirmed`. The slow loop retains these intervention IDs
and evidence references as observations. It requires independent baseline,
intervention, and holdout cohorts before promoting a behavioral strategy, and
does not count `unattributed`, `inconclusive`, `expired`, or `withdrawn`
outcomes as wins.

## Host API

`InterventionEvaluator.assess()` is a host-only Python API. The MCP transport
does not expose it as an agent tool. A runtime adapter must verify applied
receipts and resolve each evidence reference against the authoritative stores
before calling it. The resulting typed `InterventionOutcome` can then be
passed to `FastLoopInput.intervention_assessments`; the controller closes that
intervention and retains, releases, or blocks behavior accordingly.

The evidence resolver is an explicit dependency so the evaluator does not
silently trust caller-provided labels. DigitalPsychology's `RuntimeSession`
owns the evaluation window and calls CSW and trusted host-validator ports
through explicit adapters.
