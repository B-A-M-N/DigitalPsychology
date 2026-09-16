# Flow: Characterize (OBSERVED → CHARACTERIZED)

Classify the behavior.

## Questions

```text
What exactly happened?
What should have happened?
What was the earliest divergence?
Was the defect epistemic, procedural, architectural, interactional, or executional?
What condition appears to trigger it?
Does it occur repeatedly?
Does it generalize across tasks?
```

## Output

```yaml
behavior:
  observed:
  expected:
  divergence_point:
  trigger:
  impact:
  reproducibility:
```

## Classify: trait, state, or induced?

- **Trait-like** — repeated across many contexts (high initiative, strong abstraction tendency, frequent premature closure).
- **State-like** — produced by the present situation (conservative after destructive tool failure, repetitive after context saturation, deferential after repeated corrections).
- **Environment-induced** — created by tooling or instructions ("agent appears obsessive because the harness automatically retries").
- **Interaction-induced** — emerges only between actors (Agent A alone doesn't over-expand; Agent B alone doesn't; B reviewing A repeatedly enters a review/fix/re-review scope-expansion loop).

The last category is especially valuable for multi-agent systems — an
agent-level fix would miss it entirely.
