# Flow: Reproduce (CHARACTERIZED → REPRODUCIBLE)

Determine whether the behavior is systematic.

## Vary

wording · task complexity · context length · tool availability · feedback
strength · task domain · authority framing · prior agent output · number of
agents · success/failure signals.

## Goal

Separate:

```text
one bad response
```

from:

```text
reliable behavioral tendency
```

## Attribution: separate agent effect from everything else

Profiles and findings must distinguish:

```text
AGENT EFFECT · ENVIRONMENT EFFECT · TASK EFFECT · TOOL EFFECT · INTERACTION EFFECT
```

Example: Agent A shows premature closure in harness X but not harness Y;
Agent B also shows it in harness X — likely a harness/environment effect, not
agent-specific. Without this separation you'll prescribe agent interventions
for environment problems.

Design reproduction around the evaluation primitives
(`../evaluations/behavioral-probes.md`, `counterfactual-tests.md`).
