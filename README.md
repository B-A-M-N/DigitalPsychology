# Digital Psychology

> **Behavioral science for AI agents.**
> Observe what agents actually do, determine the conditions under which behavior recurs, experimentally test interventions, and measure whether anything truly improved.

Digital Psychology (DP) is the **behavioral observation, experimentation, and learning layer** of a larger closed-loop architecture for AI agents.

It was originally conceived as another behavioral skill pack. That became the wrong abstraction.

CognitiveFrameWorks already prescribed how an agent should behave. CognitiveStateWork encoded what operational behavior and transitions were appropriate in particular workflows. Adding another set of instructions would have created more policy without answering a more fundamental question:

> **Why does the agent continue exhibiting a behavior under particular conditions — and what actually changes it?**

Digital Psychology therefore became an independent measurement discipline. It does not begin by telling the agent what to do. It begins by observing what the agent **actually did**.

## The larger architecture

| System | Primary question | Character |
| --- | --- | --- |
| **CognitiveFrameWorks** | **How should the agent behave?** | Prescriptive |
| **CognitiveStateWork** | **What behavior and transitions are appropriate now?** | Prescriptive / state-governed |
| **Digital Psychology** | **Why is the agent behaving this way under these conditions?** | Observational / experimental |

Together they form a **closed-loop cognitive-behavioral control architecture**:

```text
                    PRESCRIPTIVE FAST LOOP

        CognitiveFrameWorks + CognitiveStateWork
                       │
                       ▼
                     Agent
                       │
                actions / results
                       │
                       ▼
              behavioral telemetry
                       │
                       ▼
                    SLOW LOOP

               Digital Psychology
        observe → reconstruct → compare
              → experiment → validate
                       │
             validated findings
                       │
             ┌─────────┴─────────┐
             ▼                   ▼
      behavioral guards    routing profiles
             │                   │
             └─────────┬─────────┘
                       ▼
               future CFW policy
```

This makes the system adaptive without making it self-modifying in an uncontrolled way.

## What “why” means here

Digital Psychology does **not** claim privileged access to a model's internal subjective experience. Statements such as `"I became confused"` or `"I thought the test was enough"` are behavioral outputs, not authoritative causal explanations.

“Why” means something more rigorous:

> **Under which measurable conditions does this behavior occur, which variables predict it, and which controlled interventions reliably change it?**

DP can therefore distinguish possibilities such as agent effect, model effect, harness effect, task effect, tool/environment effect, state/workflow effect, delegation effect, interaction effect, and policy effect without anthropomorphizing the system.

## Why Digital Psychology is separate

If the system that creates a rule also decides whether the rule worked, confirmation bias becomes architecture.

```text
CFW:
"the agent should do X"

CSW:
"the workflow requires X before Y"

DP:
"does the agent actually do X?
 under which conditions?
 did introducing X improve the outcome?
 did the failure simply move somewhere else?"
```

Digital Psychology remains independent so it can evaluate CognitiveFrameWorks and CognitiveStateWork themselves.

## Behavioral telemetry

DP should not need to live inside the active model context. The host runtime emits small structured events describing what it already knows:

* decisions, observations, tool requests and tool results;
* contradictions, retries, delegations and corrections;
* state transitions, completion claims and evidence relationships;
* routing selections and routing suppressions.

Behavioral identity is hierarchical:

```text
agent instance
    └── session
         └── task
              └── attempt
                   └── segment
                        └── event
```

Multi-agent work may additionally include interaction, parent-session, delegator, delegate, delegation, and role identities. This prevents unrelated sessions from being collapsed into one behavioral history.

## From events to behavior

A single event rarely establishes a behavioral pattern. DP reconstructs behavior over progressively larger temporal units:

```text
Event → Episode → TaskTrajectory → SessionTrajectory
                                      │
                                      ├── interaction analysis
                                      ▼
                         context-stratified profile
                                      │
                                      ▼
                                  Experiment
                                      │
                                      ▼
                              validated finding
```

For example, a completion claim followed by an operator contradiction, defense of the prior conclusion, fresh tool use, and a revised conclusion is more meaningful than counting those events independently.

### Independent sessions are the experimental unit

Cross-session learning must not treat multiple log events as multiple independent observations. A promotion-grade trial is closer to:

```text
agent instance + session + task + attempt + behavioral subject
```

Events are reconstructed inside that trajectory first. Only then are independent trajectories compared statistically.

## Three classes of intervention

### Structural intervention

Change what the runtime permits:

```text
unsafe action → host gate blocks action
```

A deterministic fake-agent harness is useful here because the test asks whether the structural control works independently of model reasoning.

### Cognitive or prompt intervention

Change what the model sees or how it is instructed to reason:

```text
explicit contradiction framing → lower stale-hypothesis persistence
```

This requires real-model behavioral evaluation.

### Routing or composition intervention

Change which optional behavioral/workflow components are activated:

```text
specific model + harness + task family
→ unnecessary specialist repeatedly increases work
→ controlled suppression experiment
→ same success, lower correction/tool/context burden
```

This also requires real-agent qualification. A structural test cannot prove that a cognitive or routing intervention improved model behavior.

## Deployable output

Validated findings can become two different runtime products.

### Behavioral guards

```text
observed recurring failure
        ↓
candidate → experiment → validated → canary → active guard
```

Example:

```text
unresolved contradictory evidence
→ completion remains illegal
```

### Behavioral routing profiles

A routing profile changes which **already legal** options should be preferred or suppressed for a particular context. It does not create authority, make an illegal StateWork transition legal, or remove mandatory safety policy.

```text
agent/model/harness/task-family
        + repeated unnecessary FLOW activation
        + controlled evidence showing no outcome benefit
        ↓
validated routing profile
        ↓
future matching tasks suppress optional FLOW activation
```

The goal is not endlessly accumulating rules. It is better behavior with the smallest effective active control surface.

## Learning should be subtractive too

Validated evidence may justify suppressing an unnecessary flow or optional specialist, avoiding ineffective delegation, reducing retry amplification, removing a stale guard, retiring a superseded intervention, or preferring a simpler legal composition.

## Real-agent behavioral experiments

A behavioral claim requires observing an actual agent performing an actual task. A useful experiment preserves:

* the same task goal and fixture/environment;
* comparable model, tool access, and host conditions;
* blinded cohort identity;
* explicit control and treatment policies;
* independent sessions and objective outcome evaluators.

Possible measurements include task success, correction cycles, redundant tool calls, unnecessary routing, premature completion, hypothesis revision after contradiction, retry amplification, context consumption, latency, token use, state-transition errors, and delegation failures.

An intervention should not be declared successful merely because one preferred metric improved. Task outcome and safety/regression metrics must remain acceptable.

## Multi-agent behavior

Multiple agents agreeing with one another is not necessarily independent evidence. They may share the same premise, source context, delegator, or failure mode.

DP therefore records interaction and delegation provenance and distinguishes individual-agent tendency, inherited premise, delegator effect, delegate effect, interaction effect, topology effect, circular validation, role drift, correction cascade, and duplicated investigation.

## Fast loop versus slow loop

Digital Psychology primarily belongs to the **slow loop**.

```text
During a task:  CFW + CSW → govern current execution

Across tasks:   DP → reconstruct → compare → experiment
                  → validate → affect future composition
```

Allowing DP to casually rewrite a running task's policy would make it difficult to determine whether an observed change was caused by the intervention. Emergency containment is separate from longitudinal behavioral learning.

## Core principles

> **Agent behavior is data.**

> **Agent action is not evidence that the action succeeded.**

> **Confidence is behavior, not proof.**

> **The agent's explanation of itself is another output to evaluate.**

> **One session is not a population.**

> **Many events from one session are not many independent trials.**

> **Multi-agent agreement is not independent verification when agents inherit the same evidence or premise.**

> **A repeated failure should become an experiment, not another reminder.**

> **The goal is not to humanize agents. The goal is to make their behavior measurable enough to engineer.**

## The three-system model

```text
CognitiveFrameWorks
    HOW should the agent behave?

CognitiveStateWork
    WHAT behavior and transitions are appropriate NOW?

Digital Psychology
    WHY does this behavior recur under THESE CONDITIONS,
    and WHAT ACTUALLY CHANGES IT?
```

CFW provides behavioral policy. CSW provides state-aware workflow control. Digital Psychology provides the observation and experimental loop that determines whether those policies are actually doing what they were intended to do.

Together, they form an **adaptive agent control stack**: not a single giant prompt, not an unconstrained self-modifying agent, but a layered system in which behavior can be prescribed, state can be governed, outcomes can be observed, and future policy can improve from evidence.
