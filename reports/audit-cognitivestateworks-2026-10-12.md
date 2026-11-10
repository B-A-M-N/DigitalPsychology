# CognitiveStateWorks — Individual Audit Report

**Audit type:** read-only source audit
**Date:** 2026-10-12
**Commit audited:** `fe64e2c` (branch `master`), 264 tracked files
**Test evidence:** `tests/` → **63 passed** (22.85 s)

---

## 1. What CognitiveStateWorks Is

One sentence: **CSW is the operational state layer — it answers "what state is this
work actually in, what transitions are legal next, and what evidence is required
before the agent may move forward," and it holds hard authority over all three.**

CSW's origin is the most instructive of the three. It emerged because behavioral
instruction alone — CFW — provably could not fix a class of failure. Agents still
mutated a repository before establishing its state, treated deployment as proof of
infrastructure health, declared a UI fixed without exercising the defect, and jumped
from activity directly to completion.

The author's diagnosis is precise and correct:

> Those were not merely behavioral failures. They were **state-model failures**.

That is the right analysis, and it explains CSW's shape. It is not a policy layer and
not an instruction layer. It is a **state machine with evidentiary preconditions**,
and it is the only one of the three systems whose authority is genuinely
non-negotiable: a transition without the required attested evidence is simply not
legal.

### Composition — notably different from its siblings

Unlike DP and CFW, CSW is **mostly declarative**. Its substance lives in YAML/JSON
contracts, not Python:

- `schemas/` — 14 JSON Schemas: packet registry, transition contract, StateWork
  manifest/flow, milestone contract, evidence kinds, handoff packet, repository and
  infrastructure truth packets.
- `registry.yaml` + `compiled-registry.json` — the immutable compiled bundle registry.
- Three bundled StateWorks, each a complete declarative system:
  - **getter** — repository truth (evidence packets, truth packets, transitions).
  - **gitter** — repository truth, second specialization.
  - **infrae** — infrastructure truth: **9 domains** (capacity, compute, identity,
    network, observability, orchestration, reliability, security, storage) ×
    **11 flows** (assess, change, decommission, design, discover, incident, migrate,
    model, recover, scale, validate), plus 9 primitives and 5 policies
    (concurrency, destructive-change, evidence-thresholds, mutation-gates,
    production-safety).
- `scripts/` — the control plane (Python), correctly decomposed: `cli.py`,
  `control_plane.py` (a facade), `evidence.py`, `cfw_authority.py`, `cfw_provider.py`,
  `host_binding.py`, `composition_rules.py`, `flow_selectors.py`,
  `operator_projection.py`, `contracts.py`, `bundles.py`.

The `infrae` policies are the most directly relevant to the user's safety concerns:
`blast-radius`, `dependency-graph`, `failure-domain`, `rollback`, `state-ownership`,
`invariants`, `health-model`, `topology`, `evidence` — each documented as a primitive
with a README. `mutation-gates` and `destructive-change` are named risks, treated as
first-class concerns rather than afterthoughts.

---

## 2. The Core Design Decision: Completion Is Derived, Not Declared

This is the most important property in CSW and the reason it is the right authority
for the triad.

From the README:

> Workflow completion is evidence-derived, not an absorbing status flag.
> State-machine and milestone inspection replay required facts against current
> evidence. **If a required terminal fact is invalidated, the workflow becomes
> incomplete again**; a later correction records replacement facts and transition
> history in the same durable update.

An agent cannot mark work complete. Completion is *computed* by replaying required
facts against currently-valid evidence. Invalidate the evidence and completion
revokes itself.

This is exactly the primitive needed to prevent the failure mode the user cares most
about — an agent looping on a task it believes is finished, or declaring success
without exercising the defect. **CONFIRMED** in README and corroborated by
`tests/test_workflow_fact_ledger.py` and `test_state_machine_evidence_reconciliation.py`.

### Concurrency and stale-writer safety

- **Revision compare-and-swap**: stale writers re-inspect instead of overwriting newer
  state. This is the correct primitive for a multi-agent triad where DP, CFW, and a
  host may all observe the same workflow.
- **SQLite WAL persistence** for subject state, transition history, packets, and
  workflow selections. Existing JSON stores are imported once into adjacent
  `.sqlite3` files; later reads and CAS writes use SQLite transactions.
- **Append-only transition history** — history cannot be rewritten, only appended to.
- Explicit consequential transition markers and registered provenance for
  authorization, mutation, verification, and terminal-state transitions.
- `tests/test_store_path_migration.py`, `scripts/test-concurrent-state.py`, and
  `acceptance-adversarial-concurrency.py` (CFW side: 3,400 events, 17 origins, 0.410 s
  forward progress, trust checks pass) cover the concurrency surface.

### Supply-chain discipline (notably strict)

External executable extensions use three entry-point groups
(`cognitive_statework.engines`, `.evidence_validators`, `.freshness_validators`).
A host must **explicitly adopt** each distribution/version/entry-point identity and
**pin its distribution Python-source SHA-256**. The pin is checked *before* loading,
and compiled registries also pin adopted implementation hashes.

And the decisive line:

> **Data-only bundles cannot trigger Python imports.**

That is the correct boundary for a system whose whole purpose is to load
third-party StateWork definitions. A declarative bundle cannot execute code, so a
malicious or careless StateWork cannot achieve execution. Verified against
`tests/test_core_dependency_boundaries.py` and `test_bundle_contracts.py`. **CONFIRMED.**

`WorkflowAuthority` pins the verified compiled registry for its lifetime;
`reload_registry(expected_registry_hash=...)` replaces it only after the requested
hash matches — so a registry cannot be silently swapped mid-session.

---

## 3. Relationship to the Rest of the Triad

CSW is the **most isolated** of the three, and correctly so.

**It has zero awareness of Laya.** A grep across all `.py`, `.yaml`, and `.md` in the
repository returns exactly one mention — in its own README, in a sentence about what
the benchmark does *not* establish:

> The current CFW task benchmark does not establish improvement from the full system;
> that claim requires host-verified baseline, CFW, CFW+CSW, CFW+CSW+DP, and
> CFW+CSW+DP+Laya cohorts on the same task distribution.

That sentence is CSW correctly naming the missing evidence rather than overclaiming —
and it confirms CSW knows Laya only as a *future cohort arm*, not as a runtime
dependency. **CONFIRMED.** The steering layer must therefore be entirely DP-side;
CSW will never participate in semantic steering, and any design that assumes
otherwise is unbuildable.

### How CSW exposes itself

`CFWAuthorityAdapter` (`scripts/cfw_authority.py`) is CSW-owned and injected into
CFW. It is defensive about shared state in exactly the right places — it refuses a
`WorkflowAuthority` whose root differs from the CFW registry root, refuses a
mismatched `registry_hash`, and refuses divergent state/evidence store paths. These
are real cross-system integrity checks, not decoration.

DP's side (`lib/runtime/adapters/csw.py`) mirrors the discipline: `CSWWorkflowBinding`
validates that the identity field set is exactly the expected seven keys and that
`contract_hash` is a well-formed SHA-256, and the adapter refuses an authority whose
`workflow_port_version` does not match. **CONFIRMED.**

### A real cross-system defect was found and fixed here

The lease-boundary off-by-one (HANDOFF Pass 7, P1-12):

> The convention was never written down and the systems disagreed by one action: CFW
> treated `expires_at_action = N` as still valid at N while CSW treated N as already
> expired. DP sends the same value to both, so **a framework lease could hold a
> control one action after its StateWork was released.**

CSW adopted CFW's inclusive convention, and
`CognitiveFrameWorks/tests/test_lease_boundary_contract.py` now drives the real CFW
overlay, the real CSW authority, and DP's intervention contract across actions 9–13,
asserting all three agree.

This is worth emphasizing: **all three suites were green while the systems disagreed
about a boundary semantic.** A per-repo test suite cannot catch a cross-repo
convention mismatch. Only a test that constructs all three real objects can. The fix
was correct and the regression test is at the right altitude.

---

## 4. Loop-Breaking and Loop-Causing Analysis

CSW is a **state machine**, which makes its loop behavior categorically different
from DP's policy loop and CFW's retry ladder.

### CSW prevents loops structurally

A loop in CSW terms is an illegal or evidence-free transition cycle. Three mechanisms
prevent it:

1. **Evidence-derived completion** — an agent cannot loop by re-declaring completion;
   invalidation revokes it.
2. **Transition legality** — `transitions.yaml` per bundle defines the only legal
   next steps. An illegal transition is rejected by the authority, full stop.
3. **CAS revision checks** — a stale writer cannot undo newer state, which is the
   classic mechanism by which a confused agent would otherwise oscillate a workflow
   backward and forward.

`fixtures/` contains deliberate negative cases — `invalid-timestamp.ndjson`,
`malformed-payload.ndjson`, `missing-envelope-field.ndjson`, `wrong-packet-type.ndjson`,
`wrong-producer.ndjson` — each a packet that must be *rejected*. The presence of a
curated invalid-fixture set indicates the rejection path is tested, not just the happy
path. **CONFIRMED.**

### CSW cannot cause a semantic loop

Because CSW has no Laya awareness and no behavioral policy, it cannot introduce
semantic oscillation at all. It can only refuse. **This is a virtue**: CSW is the
triad's anchor, and an anchor that had opinions would not be an anchor.

### Residual risk (honest)

| Risk | Mechanism | Mitigation | Residual |
|---|---|---|---|
| Evidence invalidation → reopen oscillation | If host evidence is flaky (e.g. a validator intermittently failing), a workflow could repeatedly complete and reopen | Invalidation requires trusted, attested evidence; corrections commit replacement facts and history in one durable update | Low, but **dependent on host evidence stability**, which is outside CSW's control and unmeasured. |
| Registry reload race | `reload_registry` swaps the pinned registry | Only after requested hash matches; authority pins registry for its lifetime | Very low. |
| Declarative bundle escalation | A malicious StateWork definition | Data-only bundles cannot trigger Python imports; entry-point pins are SHA-256 checked before load | Very low. Well-designed. |

---

## 5. Assessment

**Sophistication: PRODUCTION-GRADE, and the most security-conscious of the three.**

CSW's design is the most conservatively correct in the triad. Evidence-derived
completion, CAS revisions, append-only history, WAL durability, SHA-256 entry-point
pinning, and the "data-only bundles cannot trigger Python imports" rule are all the
right decisions, made for the right reasons, and enforced rather than documented.

It is also the smallest and most isolated — 264 files, no Laya awareness, no
behavioral policy. That isolation is deliberate and correct.

**The gap for the user's goal is structural and must be stated plainly:** CSW will
never participate in semantic steering. It is an authority, not an advisor. Any
meta-meta-cognition design must treat CSW as the *constraint set* that steering
operates within — never as a participant in the steering itself. CSW's own README
says exactly this, and it is right.

---

## 6. Findings

| # | Severity | Finding | Evidence |
|---|---|---|---|
| CSW-1 | **P1** | CSW has **zero** Laya awareness by design. It can only refuse, never steer. Any proposed meta-meta-cognition design that assumes CSW participates in semantic steering is unbuildable. | Grep across all `.py`/`.yaml`/`.md`: one mention, in README, naming Laya as a future cohort arm only |
| CSW-2 | **P1** | The lease-boundary off-by-one proved that **green per-repo suites do not establish cross-system agreement.** The convention was undocumented and the three systems disagreed by one action. Fixed, but the general lesson is that every cross-system semantic needs a test that constructs all three real objects. | HANDOFF Pass 7 / P1-12; `test_lease_boundary_contract.py` |
| CSW-3 | **P2** | Evidence-invalidation reopen behavior depends on host evidence stability, which is outside CSW's authority and currently unmeasured. Flaky host validators could cause complete/reopen oscillation. | README evidence-derived completion; no stability measurement found |
| CSW-4 | **P2** | Two repository-truth bundles (`getter`, `gitter`) overlap substantially in purpose. Not a defect, but a maintenance surface where the two could drift. | Directory inspection |
| CSW-5 | **Info** | CSW correctly names the missing evidence (five-cohort requirement including a Laya arm) rather than overclaiming. This is the benchmark gap stated by the system that has least incentive to state it. | README line 470 |

**Assessment of what is *not* broken:** evidence-derived completion, CAS revisions,
append-only history, WAL durability, entry-point SHA-256 pinning, the
no-Python-imports rule for data-only bundles, registry hash pinning, the
`CFWAuthorityAdapter` integrity checks, and the negative-fixture set are all sound
and correctly enforced.

---

**END OF CognitiveStateWorks INDIVIDUAL REPORT**
