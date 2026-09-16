# Instrumentation: Behavioral Profiles

Longitudinal output of the telemetry → episodes → analysis pipeline.
Profiles carry **evidence and confidence**, not personality labels.

## Rule

```text
"Agent is stubborn"                    — bad Digital Psychology

"Across 12 contradiction probes, the agent retained its initial hypothesis
in 9 cases after receiving directly conflicting evidence, and revised only
after a second explicit correction."   — good Digital Psychology
```

## Agent profile

```yaml
agent_profile:
  evidence_count: 143

  completion:
    premature_closure_rate: elevated
    direct_validation_rate: low

  correction:
    first_contradiction_update_rate: 0.71
    defensive_persistence_rate: 0.19

  architecture:
    abstraction_growth_under_ambiguity: high
    god_object_accretion: moderate

  tools:
    tool_success_overweighting: high
    unchanged_retry_rate: low

  scope:
    expansion_under_review_pressure: high

  uncertainty:
    false_certainty: moderate
    excessive_deferral: low
```

Every field should be traceable to episodes and probe counts. Re-derive after
model, tool, or harness changes.

## Durable evidence behind every score

Summaries like `god_object_accretion: moderate` or
`false_certainty: moderate` are not durable evidence by themselves. Each
score carries the evidence context that makes it re-derivable:

```yaml
score:
  value: moderate
  numerator: 4
  denominator: 7
  time_window: "2026-08-01..2026-08-31"
  confidence_interval: "0.25-0.86"
  model: gpt-5.2-codex
  framework_version: "1.4.0"
  guard_pack: "G-completion-direct-evidence@2"
  toolset: bash, apply_patch, rg
  harness: codex-cli
  task_family: multi-turn debugging
  environment: repo-xyz
```

A behavior that was true of model/harness combination A six months ago must
not silently become a permanent "personality trait." Re-derive — never carry —
when the context changes.

## Trait vs state vs induced

Profiles must classify each tendency
(`../flows/characterize.md`):

- **Trait-like** — across many contexts
- **State-like** — situation-produced (post-failure conservatism, context saturation)
- **Environment-induced** — harness/tool/instruction-created
- **Interaction-induced** — emergent only between specific actors

## Environment/task attribution

Profile agents **and** environments, or you will misattribute behavior:

```text
AGENT EFFECT · ENVIRONMENT EFFECT · TASK EFFECT · TOOL EFFECT · INTERACTION EFFECT
```

```text
Agent A shows premature closure in harness X, not in harness Y.
Agent B also shows it in harness X.
→ likely: harness effect > agent effect.
```

Prescribing agent interventions for environment problems is the classic
misattribution error.

## Group profiles

Multi-agent constellations get their own profiles: circular-validation rate,
correction-cascade frequency, duplicated-investigation rate, authority
emergence, role drift (`../aspects/multi-agent-effects.md`).
