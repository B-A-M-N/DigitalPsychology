# Aspect: Abstraction Behavior

How the agent decides to introduce generalization. Feed it into the
over/under-engineering axis.

## Questions to probe

```text
When does the agent abstract?
Does abstraction appear before or after a second concrete case exists?
Does the abstraction serve a real shared contract, or was one invented?
How hard is it to retract an abstraction that turns out not to generalize?
```

Keys: premature generality (abstracting for call #1), invented contracts
(naming a relationship that doesn't exist), and abstraction as a way to *feel
robust under uncertainty*. Good abstraction follows a real shared invariant;
it must also be cheap to recognize as wrong. Behavioral invariant: abstraction
tracks evidence of a shared boundary, not the agent's tolerance for
ambiguity.