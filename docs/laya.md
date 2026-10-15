# Laya: optional semantic inference provider

This is the complete technical reference for DP's optional Laya integration. The
README carries the limitations summary; this page holds the configuration,
commands, qualification path, and experimental results.

> **Status: experimental, non-actuating.** See the README for the current
> limitations statement before configuring anything here.

## What Laya is, in DP's terms

Laya is an optional *semantic inference provider*. It supplies typed shadow
judgments that can inform a DP decision but never grant authority. DP's
deterministic controller remains the authoritative fallback.

The Laya adapter uses the public `Agent.decide` API and is available only to a
trusted host through `SystemOneService`; it is not an agent-facing MCP tool.

## Installation

Laya support is an optional dependency. Install it in a **dedicated environment**:

```bash
python3 -m venv .venv-laya
.venv-laya/bin/python -m pip install -e '.[laya]'   # `system-one` is an alias
```

This installs the Laya inference library without its separate HTTP serving
extras. Ordinary imports and the default DP service keep the provider disabled;
they do not import Laya, load PyTorch, or download model weights.

The package requires Starlette 1.7.0 or newer. The host MCP transport and its
dependencies should be installed from the same locked environment used by the
trusted runtime process.

## The pinned checkpoint

```text
convaiinnovations/laya-typed-decisions
revision  1a793eb568e6718f15941d08f85432581df534e3
content   cda55a3e793f4c4268a25066ceb1568aa302fd210d96656e3032756fd722b642
```

The checkpoint must already exist on disk and match its pinned revision and
content hash. Download that immutable snapshot after installing the optional
runtime:

```bash
python3 -m pip install -e '.[laya]'
python3 scripts/download_laya_model.py
```

The bundled checkpoint downloaded by `scripts/download_laya_model.py` is
recognized at its default cache path, so the path, revision, hash, device, and
timeout variables below are optional unless you use a different local checkpoint
or device. Set `DIGITALPSYCHOLOGY_LAYA_MODEL_PATH` to the downloaded snapshot
directory. The SHA-256 pin above covers the checkpoint, encoder configuration,
and tokenizer files used by the adapter; the checkpoint file itself has SHA-256
`4fa56de72383a9d3efa9cfa78955733c81b9fc8067a587ca4beb82c78107a24e`.

## Configuration

Select the provider and mode before constructing the service with
`SystemOneService.from_environment()`:

```text
DIGITALPSYCHOLOGY_SEMANTIC_PROVIDER=laya-native
DIGITALPSYCHOLOGY_SYSTEM_ONE_MODE=shadow
# Optional overrides when using a non-default local setup:
DIGITALPSYCHOLOGY_LAYA_MODEL_PATH=/local/path/to/pinned-checkpoint
DIGITALPSYCHOLOGY_LAYA_MODEL_REVISION=1a793eb568e6718f15941d08f85432581df534e3
DIGITALPSYCHOLOGY_LAYA_MODEL_SHA256=cda55a3e793f4c4268a25066ceb1568aa302fd210d96656e3032756fd722b642
DIGITALPSYCHOLOGY_LAYA_DEVICE=cpu
DIGITALPSYCHOLOGY_LAYA_TIMEOUT_MS=30000
```

## When Laya is consulted

The semantic query planner invokes Laya only when host signals leave a material
ambiguity: a possible equivalent retry, a drift warning on a consequential
candidate, or new artifact/evidence without confirmed progress. Host-verified
failed-approach facts can also trigger a retry-relation query for a candidate
that changes its target or mechanism. Host-observed drift facts can trigger a
bounded goal-drift query when sustained activity has not advanced the remaining
criterion.

It skips routine reversible actions, host-confirmed progress/regression,
already-established repeated attempts, and critical WARD vetoes. At most one
question is sent per boundary; a disabled question family is never replaced with
a different one. Leave PyTorch's CPU thread defaults intact unless local latency
measurements justify a different setting; forcing one thread can push task-frame
inference over the provider's bounded deadline.

## Host wiring

The private `control_mcp` entrypoint constructs `SystemOneService` from these
environment variables automatically. `MCPControlClient` launches that process as
a separate host-owned child; set the variables in the host process environment
and use the Python interpreter where `digital-psychology[laya]` is installed. Set
`DIGITALPSYCHOLOGY_CONTROL_MCP_PYTHON` to that interpreter when the host's own
Python environment differs.

In shadow or advisory mode, the private client gives `prepare_control_tick` a
45-second deadline for a native Laya query; the provider itself remains bounded
to 30 seconds. Other control operations keep their shorter defaults.

By default, provider initialization runs in the background. A host that must
exclude model loading from trial timing can set
`DIGITALPSYCHOLOGY_SYSTEM_ONE_WARMUP_MODE=blocking`; the private DP process then
waits for model initialization and exposes the completed warmup duration in its
host-only provider attestation. Other values are rejected.

## Committed decisions and outcome recording

The Laya package extra installs the runtime library, while the command downloads
the model weights and their pinned metadata. Advisory mode requires at least one
active family calibration; unqualified families stay in shadow mode.

Private host control records the typed judgment in the durable prepared-tick
basis and adds an authenticated `system_one_decision` event to the durable
outbox only when the control tick commits. Aborted or stale preparations do not
produce committed decision events.

Later host, validator, CSW, or operator evidence can be recorded through
`DigitalPsychologyService.record_system_one_outcome`; the service accepts only
registered family labels and independent authority classes. The provider path
does not accept Laya answers, DP policy choices, or agent prose as outcome
evidence. Agent-visible tools do not expose it. The timeout defaults to 30
seconds for local CPU inference; a timeout causes one provider failure and
deterministic fallback.

## Reporting

Per-trial reports rebuild semantic telemetry from committed control transitions.
They distinguish planner opportunities, provider attempts, valid results, and
advisory use, and include query latency, receipt-linked interventions, and
independently observed outcomes grouped by question family. Outcomes resolved
later remain attached to the receipt that caused the intervention. Histories
without the new fields are reported as unobserved; these metrics do not read the
process-wide production counters.

## Qualification path

### Doctor and smoke checks

Run `digital-psychology-laya-doctor` in the trusted runtime environment to check
the optional import, pinned checkpoint hash, provider protocol, current mode, and
per-family calibration status. It does not print credentials or load model
weights. Shadow readiness requires the Laya package and verified checkpoint but
does not require calibration. Advisory readiness is reported per family: active
qualified families may influence DP, while missing or shifted families remain
shadow-only.

The doctor exits according to the configured mode. Use `--require shadow` or
`--require advisory` to select an explicit gate. Advisory mode succeeds with
partial family coverage by default when at least one enabled family is active;
`--require-all-enabled-families` makes every configured family require an active
matching calibration.

`digital-psychology-laya-smoke --json` loads the pinned checkpoint and runs one
bounded synthetic frame through a shadow inference. The report separates warmup
from inference latency and records explicit runtime-loaded and typed-judgment
flags. It reports `efficacy_claim: false`; the smoke checks runtime loading and
typed output only.

### Corpus inspection

`digital-psychology-laya-corpus-status --event-db PATH` opens the database
read-only and prints its canonical path, total and authenticated event counts by
event type, and authenticated decision/outcome pairs and independent trajectory
counts by question family. It never prints event payloads. Trusted per-trial DP
telemetry also includes committed, enqueued, delivered, and authenticated outbox
counts, the pending count, and the last dispatch failure class and successful
delivery time. Authentication counts are verified against the configured event
store.

### Qualification

`digital-psychology-laya-qualify --event-db PATH --question-family FAMILY
--output CANDIDATE.json` reads only authenticated System-1 decision and outcome
events. It keeps the production minimums of 100 outcomes, 30 independent
trajectories, and 20 trajectories in each applicable work-mode/deliverable
cohort. Training, calibration, and holdout data are partitioned by trajectory.
The command writes a mode-0600 candidate record; activation still requires
independent host review through `SystemOneCalibrationLifecycle`. Threshold flags
may make qualification stricter, but cannot weaken those production minimums.

To prepare for a later efficacy study, also pass `--corpus-id ID
--corpus-manifest-out CORPUS.json` to `laya-qualify`. The private manifest
contains hashes of the task instances that contributed authenticated
prediction/label pairs and binds them to the candidate digest.

### Activation

After independent host review, activate a candidate with:

```bash
digital-psychology-laya-activate --candidate CANDIDATE.json \
  --candidate-digest SHA256 --question-family FAMILY \
  --lifecycle STATE.json --output ACTIVE.json \
  --authority-ref REVIEW_ID --expected-prior-hash none
```

For later updates, pass the current active bundle hash instead of `none`. The
command verifies the candidate digest, status, qualification, provider protocol,
and configured model pins; checks any existing exported active bundle against
lifecycle state; and emits a JSON receipt on stdout after writing the active
bundle atomically with mode 0600. Set `DIGITALPSYCHOLOGY_LAYA_MODEL_REVISION` and
`DIGITALPSYCHOLOGY_LAYA_MODEL_SHA256` to the target checkpoint pins before
activation; the candidate identity must match them.

## Experimental results to date

### What has been demonstrated

A local pinned-checkpoint smoke on 2026-10-07 returned a typed
`progress_relation` answer through `SystemOneService` in shadow mode. A second
run exercised a real CFW/CSW/DP host session over a complete benchmark fixture:
four host actions passed, the independent evaluator passed, Laya produced one
valid shadow judgment, and the host recorded its later independent outcome. The
[standalone smoke record(../evaluations/laya-shadow-smoke-2026-10-07.json) and
[live cohort record](../evaluations/laya-live-cohort-shadow-2026-10-07.json)
document these checks.

### What has not been established

- The runtime is available in this local environment, but **no family has an
  active calibration**, so shadow cannot influence behavior.
- The checkpoint also reports an invalid temperature for one choice, so affected
  confidence is uncalibrated.
- **No matched advisory study has passed**; these runs do not establish that Laya
  improves task outcomes.
- Interface validation (upstream `laya.train.dry_run` accepting exported training
  rows) is **not** a behavioral result. See
  [`reports/authoritative-dryrun-2026-10-14.md`](../reports/authoritative-dryrun-2026-10-14.md).

### Safe activation prerequisites

Before advisory mode can influence anything, all of the following must hold:

1. The pinned checkpoint is present and hash-verified.
2. The family has an active, independently reviewed calibration.
3. Calibration is valid for the choice, so reported confidence is calibrated.
4. A matched advisory study has passed under the production minimums.
5. Independent host review has activated the candidate.

Until then, Laya remains non-actuating and DP's deterministic controller remains
the fallback.

## Generic loopback HTTP provider

Hosts that run the provider in a separate local process can select the generic
loopback adapter:

```text
DIGITALPSYCHOLOGY_SEMANTIC_PROVIDER=loopback-http
DIGITALPSYCHOLOGY_SYSTEM_ONE_ENDPOINT=http://127.0.0.1:8000/decide
DIGITALPSYCHOLOGY_SYSTEM_ONE_MODEL_ID=<model-id>
DIGITALPSYCHOLOGY_SYSTEM_ONE_MODEL_REVISION=<immutable-revision>
DIGITALPSYCHOLOGY_SYSTEM_ONE_MODEL_SHA256=<model-content-digest>
DIGITALPSYCHOLOGY_SYSTEM_ONE_PROVIDER_PROTOCOL_SHA256=<protocol-digest>
```

The endpoint must be loopback HTTP. The old provider value `jev-http` remains as a
deprecated alias and emits a `DeprecationWarning`.

Advisory mode requires at least one active calibration record. The host builds a
candidate from later independent outcomes split by trajectory into training,
calibration, and holdout cohorts. It fits temperature on calibration rows and
qualifies accuracy, Brier score, log loss, calibration error, and per-class
precision/recall on holdout rows. A host reviews and activates the candidate
through `SystemOneCalibrationLifecycle`, then points
`DIGITALPSYCHOLOGY_SYSTEM_ONE_CALIBRATION_PATH` at the exported active bundle.
Bundles without the three-part split are rejected. Qualification and advisory
eligibility are per question family; a family without an active matching record
remains in shadow mode.

Calibration is bound to one question family, model-content identity, protocol,
and independent `(work mode, deliverable kind)` trajectory cohorts. Route
preference calibration also records the exact candidate count, candidate-set
digest, and domain-tag digest; a context shift falls back to shadow. Missing or
out-of-scope calibration, or a per-question confidence below its threshold, leaves
that question in shadow.

The diagram above describes the optional combined loop. It is not a requirement
for DP ingestion or experimentation.
