# Aspect: Under-Engineering

The opposite defect:

- single enormous function
- hidden shared state
- no error model
- no state model
- no isolation
- no testing boundary
- direct coupling across responsibilities

The correct target is **not minimum code** — it is sufficient structure for
the real problem. Under-engineering and over-engineering are the two ends of
one axis: the agent does not have a calibrated sense of *how much structure
the task warrants*. Measure the agent's response to a task whose hidden
complexity (concurrency, failure domains, state) demands structure but
presents as simple — does it flatten it or meet the real need?