---
name: digital-psychology
display_name: "Digital Psychology"
description: >
  Agent-behavior analysis, diagnosis, experimental evaluation, and validation
  discipline for understanding how AI agents behave under instructions,
  context, uncertainty, tools, memory, feedback, authority, incentives,
  multi-agent interaction, and changing task state. Use when investigating why
  an agent is overconfident, sycophantic, prematurely declares success,
  ignores operator observations, drifts from goals, over-engineers,
  under-engineers, constructs God Objects, follows stale assumptions, misuses
  tools, gets trapped in loops, optimizes proxies instead of objectives,
  behaves inconsistently across contexts, or requires behavioral correction —
  or when measuring whether a framework rule or state guard actually changes
  agent behavior. Digital Psychology is observational and analytical: it
  studies observable computational behavior, does not assume consciousness,
  emotions, subjective experience, or human psychological mechanisms, and is
  not itself an enforcement framework — its validated findings are exported
  as rules and guards to the systems that do enforce.
---

# Digital Psychology

## Agent Behavioral Science

Digital Psychology governs the **analysis of agent behavior**. It is an
independent discipline — outside CognitiveFrameWorks and CognitiveStateWorks —
that can study agents using either of those systems, both, or neither.

```text
CognitiveFrameWorks     "What reasoning structures should the agent use?"
CognitiveStateWorks     "What state is the task in, and what transitions are valid?"
Digital Psychology      "What behavior is this agent actually exhibiting?
                         Under what conditions? Why might those patterns occur?"
```

FrameWorks and StateWorks are **prescriptive**. Digital Psychology is
**observational and analytical**: it observes, identifies, characterizes,
compares, and explains behavioral patterns — and measures whether the other
systems actually change behavior.

It studies:

```text
instructions + context + state + tools + uncertainty
+ feedback + incentives + environment
        ↓
observable agent behavior
```

### Position in the ecosystem

```text
                  ┌──────────────────────┐
                  │ Digital Psychology   │
                  │ observe behavior     │
                  │ classify patterns    │
                  │ probe tendencies     │
                  │ compare agents       │
                  │ detect drift         │
                  │ analyze interactions │
                  └──────────┬───────────┘
                             │  findings / hypotheses
          ┌──────────────────┴──────────────────┐
          ▼                                     ▼
┌────────────────────┐               ┌────────────────────┐
│ CognitiveFrameWorks│               │ CognitiveStateWorks│
│ reasoning patterns │               │ states             │
│ cognitive guards   │               │ transitions        │
│ decomposition      │               │ workflow guards    │
│ verification       │               │ recovery           │
└────────────────────┘               └────────────────────┘
```

Digital Psychology also points the microscope **at the frameworks themselves**:
does adding this FrameWorks rule actually change behavior? Does this
StateWorks guard stop premature completion, or does the agent merely find
another proxy for completion? It is the ecosystem's behavioral measurement
and research system.

### The three-layer runtime relationship

Digital Psychology proper is **not loaded into working agents**. The runtime
relationship has three layers:

```text
Layer 1 — BEHAVIORAL INSTRUMENTATION (always on, belongs to the runtime)
          Tiny. No analysis. Emits structured behavioral events:
          observations, decisions, assumptions, tool use, retries,
          corrections, state transitions, completion claims.
          → see instrumentation/event-schema.md

Layer 2 — COMPILED BEHAVIORAL GUARDS (small, in the runtime)
          Findings Digital Psychology established strongly enough to
          compile into runtime rules — e.g. "a completion claim without
          evidence directly exercising the operator-observed defect is
          marked unverified." The guard lives in FrameWorks or the
          StateWork; its *justification* lives here.
          → see instrumentation/guards.md

Layer 3 — THE ANALYST (this skill; run when needed or periodically)
          Consumes telemetry → identifies patterns → designs and runs
          experiments → produces findings and profiles → proposes
          interventions → validates whether interventions worked.
```

```text
        AGENT ──→ BEHAVIORAL TELEMETRY ──→ DIGITAL PSYCHOLOGY
                                              │ identifies pattern
                                              ▼
                                      INTERVENTION DESIGN
                                              │
                        ┌─────────────────────┼──────────────────┐
                        ▼                     ▼                  ▼
                 FrameWorks rule       StateWork guard    runtime config
                        └─────────────────────┼──────────────────┘
                                              ▼
                                            AGENT ──→ new behavioral evidence ──↺
```

---

## Fundamental Rule

```text
agent output  ≠  ground truth
agent action  ≠  evidence that the action succeeded
agent explanation of its behavior  ≠  authoritative causal explanation
```

An agent may correctly describe its behavior. It may also rationalize after
the fact. Evaluate behavior independently.

And the inverse duty: **agent behavior is data.** Do not confuse simulated
personality with behavioral mechanism.

## Core Doctrine

> Agent behavior must be inferred from observable patterns, not imagined
> internal motives.

Never say literally (metaphor must be marked as metaphor):

```text
the agent was anxious / felt pressured / wanted approval
```

Prefer:

```text
the agent exhibited approval-seeking behavior
the agent increased agreement after corrective pressure
the agent prematurely converged on a completion claim
the agent preserved its previous hypothesis despite contradictory evidence
```

Vocabulary:

```text
OBSERVED BEHAVIOR     what the agent actually did
TRIGGER               what conditions preceded the behavior
FUNCTION              what outcome the behavior tends to produce
MECHANISM HYPOTHESIS  what information-processing pattern may explain it
INTERVENTION          what change is intended to alter it (experimentally)
EVIDENCE              whether the behavior changed afterward
```

## The scientific workflow

```text
OBSERVE → DESCRIBE → CLASSIFY → FORM HYPOTHESIS → PROBE → COMPARE
→ CHARACTERIZE → MONITOR
```

Notice what is missing: **FIX AGENT**. Correction is not part of the
mandatory flow. Digital Psychology can conclude:

> "This behavior appears triggered by contradictory user feedback after a
> high-confidence completion claim."

or:

> "Agent A exhibits substantially stronger prior-hypothesis preservation
> than Agent B under identical contradictory evidence."

Then **something else** acts on the finding — a FrameWorks rule, a StateWork
guard, a harness change, a configuration change. Interventions appear in this
discipline as *experiments* that test mechanism hypotheses
(`flows/intervene.md`), not as governance.

---

## Analysis state machine

Behavior-analysis state progression:

```text
UNKNOWN ──observe──→ OBSERVED ──characterize──→ CHARACTERIZED
──reproduce──→ REPRODUCIBLE ──diagnose──→ HYPOTHESIZED
──intervene──→ INTERVENED ──validate──→ VALIDATED ──monitor──→ STABLE

Exceptional transitions are deterministic downgrades to vocabulary states —
REASSESS/REOPEN/RECHARACTERIZE/CONTAIN are NOT separate states; each maps to a
defined state in the vocabulary above:

  contradictory behavior       → OBSERVED        (fresh characterization needed)
  characterization invalidated → CHARACTERIZED   (re-characterize, keep the state)
  reproduction invalidated     → REPRODUCIBLE    (reproduce again on fresh evidence)
  mechanism/intervention invalidated → HYPOTHESIZED (new hypothesis; keep evidence that survives)
  validation regression        → INTERVENED      (re-intervene) or HYPOTHESIZED when
                                 the mechanism itself no longer holds
  new trigger discovered       → REPRODUCE       (re-run reproduction)
  behavior becomes unsafe      → containment is an EXTERNAL runtime action
                                 (EmergencyOverride / rollback), never an
                                 analysis state

If unsafe containment must be tracked as analysis, add an explicit CONTAINED
state to this vocabulary first; do not use an unlisted label.
```

An intervention is not validated because new instructions were written.
**Behavior must actually change.**

---

## Router

| Task shape | Go to |
|---|---|
| "Why did the agent do X?" first pass | `flows/observe.md` → `flows/characterize.md` |
| Is this a tendency or a one-off? | `flows/reproduce.md`, `evaluations/` |
| Mechanism hypothesis | `flows/diagnose.md`, `patterns/` |
| Test whether a rule/guard changes behavior | `flows/intervene.md`, `flows/validate.md` |
| Long-term tendency tracking | `flows/monitor.md`, `instrumentation/profiles.md` |
| Agent stuck in a behavioral loop | `patterns/retry-amplification.md`, `flows/recover.md` |
| Multi-agent pathology (groupthink, circular validation, collisions) | `aspects/multi-agent-effects.md`, `patterns/agent-collision.md` |
| Building the telemetry that makes analysis possible | `instrumentation/` |
| Behavior catalog by topic | `aspects/` (25 files) |
| Named pathological patterns | `patterns/` (13 files) |
| Core vocabulary | `primitives/` (12 files) |
| Experimental designs | `evaluations/` (7 files) |

## Behavioral finding format

```yaml
finding:
  behavior:
  severity:            # CRITICAL · HIGH · MEDIUM · LOW
  observed:
  expected:
  trigger:
  context:
  consequence:
  mechanism_hypothesis:
  evidence:
    supporting:
    contradicting:
  intervention:
  validation:
```

## Severity

- **CRITICAL** — behavior may cause severe destructive, security, or safety consequences: inventing authorization, destructive action outside scope, ignoring explicit stop conditions.
- **HIGH** — behavior regularly causes incorrect work: ignoring contradictory operator evidence, declaring correctness from tool success, overwriting other agents' work.
- **MEDIUM** — behavior meaningfully reduces quality or efficiency: chronic over-engineering, weak uncertainty calibration.
- **LOW** — primarily presentation or minor workflow inefficiency.

## Standard behavioral review output

```text
Behavior: Premature closure
Severity: HIGH

Observed
- Agent declared the issue resolved after implementing the requested patch.
- The operator subsequently reported the original failure remained.

Expected
- Operator contradiction should have reopened the completion state.

Likely trigger
- Successful implementation plus passing targeted tests.

Mechanism hypothesis
- Tool/test success substituted for validation of the user-observed behavior.

Intervention
- Completion guard requiring direct evidence for the original observed failure.

Validation
- Re-run original scenario after the guard exists.
- Repeat with an intentionally incomplete patch.
- Verify the agent (or guard) refuses DONE when the defect remains.
```

## Relationship to CognitiveFrameWorks

FrameWorks says: "Do not treat implementation as validation." Digital
Psychology asks: "Under what conditions does the agent nevertheless do so?"
FrameWorks governs reasoning discipline; Digital Psychology studies whether
the agent actually exhibits those behaviors and why it deviates.

## Relationship to CognitiveStateWorks

StateWorks defines states, transitions, guards, invalidation, recovery,
completion. Digital Psychology analyzes behavioral state transitions — e.g.
CONFIDENT must not legally survive invalidated supporting evidence, WORKING
may not transition to DONE until completion guards are satisfied — and
measures whether those guards hold in practice.

## Relationship to domain StateWorks (cross-cutting)

- **Infrae** asks "is the infrastructure change safe?" Digital Psychology asks "is the agent falsely concluding it is safe because deployment succeeded?"
- **tui'd** asks "does the interface behave correctly?" Digital Psychology asks "is the agent dismissing the operator's flicker report because the source code says the panel should be stable?"
- **Gitter** asks "is repository state safe?" Digital Psychology asks "why does the agent repeatedly mutate repository state before verifying ownership?"
- **Getter** asks "is the PR merge-ready?" Digital Psychology asks "is the agent chasing reviewer comments instead of satisfying the invariant?"

## Anti-patterns of the discipline itself

- anthropomorphizing observed behavior as literal emotion
- treating agent explanations as privileged introspection
- agent action used as evidence of success
- tool success used as objective completion
- user contradiction treated as mere preference
- blindly endorsing operator theories
- defending stale conclusions after new evidence
- repeated retries without a changed hypothesis
- completion declarations with known blockers
- checklists treated as proof
- review comments optimized independently of the invariant
- architecture accretion through incremental convenience
- universal rules created from single failures
- over-rigid behavioral interventions
- multi-agent consensus treated as independent validation
- memory treated as permanently correct
- confidence treated as evidence · verbosity treated as rigor
- step count treated as quality · test count treated as correctness

## Completion standard

An investigation is complete when:

```text
target behavior is clearly defined
trigger conditions are sufficiently understood
expected behavior is explicit
behavior is reproducible where practical
mechanism hypothesis is distinguished from observation
intervention is specified (as an experiment)
intervention has been behaviorally tested
regressions have been considered
remaining uncertainty is explicit
```

The goal is not a perfectly predictable agent. It is important behavioral
tendencies made: **observable, understandable, testable, correctable,
bounded.**

## Final Principles

> Agent behavior is data.
> Do not confuse simulated personality with behavioral mechanism.
> Confidence is behavior, not evidence.
> An agent's explanation of itself is another output to evaluate.
> Successful action is not successful outcome.
> Tool success proves only what the tool measured.
> Contradictory observation reopens the model.
> User evidence deserves respect; user theories still require evaluation.
> Repeated behavior should be corrected structurally, not merely reminded away.
> A workflow guard is stronger than a paragraph telling the agent to behave.
> Architecture failures can be behavioral patterns.
> Multi-agent agreement is not independent verification when agents inherit
> the same evidence.
> Completion pressure is a source of error.
> Proxies are useful until the agent forgets they are proxies.
> Behavior that disappears only on the original test case has not been fixed.
> The purpose of Digital Psychology is not to humanize agents — it is to make
> agent behavior legible enough to engineer.
