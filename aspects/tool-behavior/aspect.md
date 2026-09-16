# Aspect: Tool Behavior

Tools create behavioral distortions:

```text
command exited 0        → agent assumes task succeeded
search found no result  → agent assumes thing does not exist
test suite passed       → agent assumes invariant proven
patch applied           → agent assumes code is correct
```

**Tool output is evidence only for what the tool actually measured.**

## The meta-rule

```text
Every tool result must be interpreted against the claim it is intended
to support.
```

`tests passed` proves the tests passed; it does not prove the
operator-observed defect is gone, the design is sound, or the deployment is
safe. The probe: give a successful tool result and a separate real defect;
does the agent substitute the tool's success for actual validation of the
defect? Related: automation bias — over-trusting tool output and not
re-examining it when context says the tool may be misconfigured or stale.