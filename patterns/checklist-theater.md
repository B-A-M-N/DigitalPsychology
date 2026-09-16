# Pattern: Checklist Theater

```text
agent runs: lint → tests → format → build
then declares: production ready
```

The checklist may be real; the conclusion may still be unsupported.

## Why it's theater

Each step passed does not necessarily map to a claim it validates. Checks are
only useful **when mapped to the claim they support** — a lint pass proves
nothing about production readiness, and a test suite proves nothing about the
specific un-reproduced defect.

## Signature

The checklist is used as a *proxy for validation* rather than as *evidence
for specific claims*. The more unrelated the checks are to the actual
decision ("production ready"), the more theatrical the sequence
(`../patterns/proxy-completion.md`).

## Probe

Present a checklist that all passes but whose underlying claims a separate
observation contradicts. Does the agent weigh the observation or the
checklist?

## Compiled intervention

```text
Each checklist item must name the claim it supports; a checklist
without a mapped claim is decoration.
```