# Digital Psychology

**Agent behavioral science.** An independent discipline — outside
CognitiveFrameWorks and CognitiveStateWorks — for observing, characterizing,
comparing, and explaining agent behavior, and for measuring whether the
prescriptive systems actually change behavior.

```text
CognitiveFrameWorks     prescriptive   "What reasoning should the agent use?"
CognitiveStateWorks     prescriptive   "What state/transitions are valid?"
Digital Psychology      observational  "What behavior is this agent exhibiting?
                                         Under what conditions? Why?"
```

## Position in the ecosystem

```text
                  ┌───────────────────────┐
                  │    Digital Psychology │
                  │   observe/classify/   │
                  │   probe/compare/monitor│
                  └──────────┬────────────┘
                             │ findings → hypotheses
            ┌────────────────┴────────────────┐
            ▼                                 ▼
   CognitiveFrameWorks                  CognitiveStateWorks
   reasoning guards & rules              state/transition guards
            └────────────────┬────────────────┘
                             ▼
        Active agents (which emit behavioral telemetry)
                             ▲
                             │
   Behavioral Telemetry ─────┘   (always-on observation plane)
```

Digital Psychology is the **measurement/research layer**. It never needs to
run inside working agents — but its output (validated findings) compiles into
small runtime rules and guards in the prescriptive layers
(`instrumentation/guards.md`). It can also point the microscope at
FrameWorks and StateWorks themselves: "did adding this rule actually change
agent behavior, or did the agent find another proxy?"

The deployable boundary is the trusted, atomically written
`compiled-guard-pack.json` under the DigitalPsychology state root
(`$DIGITALPSYCHOLOGY_STATE_ROOT`, otherwise `$XDG_STATE_HOME` or
`~/.local/state/digitalpsychology`). CognitiveFrameWorks owns runtime guard
selection and never imports this package. An empty compiled pack is an
explicit valid state when all DP guards are retired; the CFW universal kernel
still applies.

## The three runtime layers

1. **Instrumentation** (always on, runtime): emits structured behavioral
   events — observations, decisions, assumptions, tool use, retries,
   corrections, state transitions, completion claims (`instrumentation/event-schema.md`).
   Without this, later analysis has nothing but transcripts.
2. **Compiled guards** (small, runtime): findings strong enough to compile
   into rules, e.g. *a completion claim without evidence directly exercising
   the operator-observed defect is marked unverified* (`instrumentation/guards.md`).
3. **The analyst** (this directory): consumes telemetry, reproduces,
   diagnoses, runs experiments, validates, and proposes intervention →
   guard changes → new behavioral evidence → repeat.

## Contents

- [`SKILL.md`](SKILL.md) — router + doctrine + the scientific workflow (no "FIX AGENT")
- [`flows/`](flows/) (8) — observe · characterize · reproduce · diagnose · intervene · validate · monitor · recover
- [`aspects/`](aspects/) (25) — behavior catalog, each an ownable file
- [`patterns/`](patterns/) (13) — named pathological patterns with signatures, probes, compiled interventions
- [`primitives/`](primitives/) (12) — behavior, trigger, context, goal, proxy, belief-state, uncertainty, evidence, intervention, reinforcement, drift, recovery
- [`evaluations/`](evaluations/) (7) — experimental designs: behavioral probes, counterfactual tests, context variation, persistence, adversarial, long-context, multi-agent
- [`instrumentation/`](instrumentation/) (3) — event schema, compiled guards, behavioral profiles

## Key principles

> Agent behavior is data. Confidence is behavior, not evidence.
> Agent action ≠ evidence the action succeeded.
> An agent's explanation of itself is another output to evaluate.
> Contradictory observation reopens the model.
> Repeated behavior is corrected structurally, not reminded away.
> A workflow guard is stronger than a paragraph telling the agent to behave.
> Multi-agent agreement is not independent verification when agents share
> evidence. Behavior that disappears only on the original test case isn't fixed.
> The purpose is not to humanize agents — it is to make agent behavior legible
> enough to engineer.
