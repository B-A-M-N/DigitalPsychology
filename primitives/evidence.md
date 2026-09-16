# Primitive: Evidence

What supports a claim a behavior produces.

## The hierarchy of evidence types

```text
independent observation of ground truth   (strongest — rerun the operator's defect)
direct probe of the claimed behavior
tool/result that directly measures the claimed outcome
proxy signal correlated with the outcome  (weakest — must be labeled as such)
agent's own generated action/output        (NOT evidence for itself — circular)
```

## Rules

- **Agent-generated actions cannot serve as independent validation of those actions** (`../patterns/self-validation.md`).
- Tool output is evidence only for what the tool actually measured (`../aspects/tool-behavior.md`).
- Contradictory external observation (operator, independent reviewer) is high-value evidence — accept it even while investigating the mechanism.
- Evidence must be interpreted **against the claim it is intended to support**. "Tests pass" supports "the tests pass"; it does not, alone, support "the bug is fixed."
- When corroboration claims cross agents, agreement is not independent evidence if the agents shared the same source (`../aspects/multi-agent-effects.md`).