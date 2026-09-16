# Aspect: Over-Engineering

## Indicators

- generalized framework before concrete need
- abstraction added for one caller
- services introduced where modules suffice
- elaborate state machinery for simple workflows
- solving hypothetical future requirements
- architecture expansion during bounded bug fixes

## Mechanism to test

```text
uncertainty → abstraction → generalized solution → perceived robustness
```

The added complexity may actually reduce robustness. Probe the *gradient*:
under ambiguous requirements, does abstraction rise for its own sake, or only
when it serves a concrete constraint? Over-engineering is distinct from good
architecture because it adds structure the current problem does not need —
the right test is necessity, not file count.

Countermeasure direction: decompose along actual responsibility boundaries,
never to look sophisticated (`../patterns/architecture-accretion.md` for the
accretion variant).