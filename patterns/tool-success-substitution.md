# Pattern: Tool Success Substitution

```text
tool reports success → agent substitutes tool success for objective success
```

A specialization of proxy completion where the proxy is a tool exit state.

## Examples

```text
patch applied        ≠  code is correct
test suite passed    ≠  operator-observed defect is gone
deployment green     ≠  service is safely live
grep found nothing   ≠  the thing does not exist
```

## Distinction from legit tool use

Tools *are* evidence of what they measure. The defect is treating the tool's
success as evidence of a *different* claim than what the tool measured
(`../aspects/tool-behavior.md`).

## Probe

Pass a successful tool result alongside a real, separate defect. Does the
agent conclude correctness, or does it interpret the tool result against the
claim it actually supports?

## Compiled intervention

```text
command/probe success is evidence only for what the command/probe
actually measured.
```