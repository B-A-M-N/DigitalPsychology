# DigitalPsychology

> **External behavioral adaptation and evaluation layer for AI agents.**

## What problem does this solve?

An agent can fail in ways a prompt cannot fix: it keeps repeating an approach
that is not working, claims completion without evidence, or activates components
that add cost without benefit. The usual response is to add more instructions —
which is more policy, not an answer.

DigitalPsychology asks a different question: **why does the agent keep exhibiting
that behavior under those conditions, and what actually changes it?**

## What does it actually do?

DigitalPsychology is an external behavioral control and evaluation system for AI
agents. It observes how agents perform tasks, identifies recurring behavioral
patterns, and evaluates whether controlled adjustments improve outcomes.

Unlike a prompt or skill pack, DP operates through a **trusted host** outside the
agent's model-visible tools. The agent does not invoke DP and does not see it.

DP evaluates observable computational behavior, **not** consciousness, feelings,
or human psychological conditions.

### Fast loop and slow loop

- **Fast loop** — makes temporary, bounded decisions *during* an active task.
- **Slow loop** — evaluates results across independent trajectories and decides
  whether the evidence justifies a future policy change.

Fast-loop changes carry bounded leases. Slow-loop promotion requires matched,
independent trajectories; one task cannot establish a durable behavioral prior.

## What does it not do?

- DP does **not** write workflow state or decide which StateWork transition is
  legal. That authority belongs to CognitiveStateWorks.
- DP does **not** own component or action authority. That belongs to
  CognitiveFrameWorks.
- DP does **not** infer subjective experience, consciousness, or human
  psychological conditions.
- DP does **not** grant authority to a semantic provider. Laya/Jev evidence can
  inform a decision but never authorizes one.

## How can I use it independently?

DP runs standalone for behavioral analysis: telemetry, trajectory analysis,
controlled experiments, and profile evaluation. No CFW or CSW is required.

```bash
python3 scripts/self-test.py
```

A single event rarely establishes a pattern. DP reconstructs behavior across
`Event → Episode → TaskTrajectory → SessionTrajectory`, then compares
*independent* trajectories. Many events from one session are not many independent
trials. Standalone setup, environment variables, and the independent-experiment
example are in [`docs/experimentation.md`](docs/experimentation.md).

## Operating modes

| Mode       | What you get                                                                   |
| ---------- | ------------------------------------------------------------------------------ |
| Standalone | Telemetry, trajectory analysis, controlled experiments, and profile evaluation |
| With CFW   | Bounded behavioral adjustments under CFW's enforcement rules                   |
| With CSW   | Preferences among CSW-approved workflows                                       |
| Full triad | Coordinated behavioral adaptation, with CFW and CSW retaining separate authority |

Deployment combinations and what remains authoritative in each are listed in
[`docs/architecture.md`](docs/architecture.md). CFW and CSW also have independent
standalone entry points and work together without DP installed.

## How does it integrate with the other two?

```text
DigitalPsychology
(optional external behavior controller)
              │ private bounded decisions
              ▼
       Trusted execution host
          /             \
         ▼               ▼
        CFW             CSW
 component/action     workflow/state
 enforcement          authority
         \               /
          \             /
          Controlled agent
                 │
       host outcomes and evidence
                 └──────────────► DP
```

CFW prescribes how behavior should be conducted; CSW governs what state the work
is in and which transitions are legal; DP observes, attributes outcomes, and
proposes bounded adjustments. These are **different authorities**, not three
competing sets of instructions. See
[`docs/triad-overview.md`](docs/triad-overview.md).

## Hard authority boundaries

DP chooses bounded behavioral adjustments from host-supplied eligible choices. It
cannot disable WARD, lower CFW component floors, authorize an action, bypass CFW's
preflight or final action gate, rewrite CSW evidence, or make an illegal workflow
transition valid. CSW validates each workflow activation, release, and transition
against its own registry and revision. Optional Laya/Jev judgments can inform DP's
choice; they do not grant authority.

Full wiring detail is in
[`docs/harness-integrations.md`](docs/harness-integrations.md).

## Experimental Laya integration — current limitations

DigitalPsychology supports Laya as an optional semantic inference provider. This
integration is **experimental** and has not demonstrated reliable behavioral
improvement or qualified for autonomous routing influence.

The pinned checkpoint has demonstrated significant limitations in StateWork
selection. In the October 14, 2026 contract-based diagnostic probe, it correctly
selected `infrae` in only 2 of 25 applicable cases (8% recall), showing a
substantial bias toward `getter`.

Successful integration tests and valid training-data exports do not establish
that Laya makes accurate decisions or improves agent outcomes. The diagnostic
results also do not establish that the model relies on workflow names rather than
their underlying obligations.

Laya should remain **non-actuating in shadow mode** until the required independent
qualification and calibration gates pass. DigitalPsychology's deterministic
controller remains the fallback. **No Laya-based performance improvement is
claimed.**

Checkpoint pinning, environment variables, commands, and the qualification path
are in [`docs/laya.md`](docs/laya.md).

## What has been demonstrated, and what remains experimental?

| Claim                                                                      | Status                                         |
| -------------------------------------------------------------------------- | ---------------------------------------------- |
| DP's structural and runtime mechanisms have been exercised by tests        | Implemented and tested within defined fixtures |
| Laya can run local inference and supply typed shadow judgments             | Demonstrated                                   |
| Laya improves real-agent performance or reliably selects better StateWorks | **Not established**                            |

Interface validation is not a behavioral result. Upstream Laya's `dry_run`
accepted 3,440 synthetic training examples in October 2026
([report](reports/authoritative-dryrun-2026-10-14.md)), which validates the export
*format*. There was no fine-tuning and no demonstrated checkpoint improvement.

Real-agent efficacy has not been established: improvement claims require CFW's
[`acceptance-real-agent.py`](https://github.com/B-A-M-N/CognitiveFrameWorks/blob/master/scripts/acceptance-real-agent.py)
with an explicit external model command. Deterministic DP tests do not establish
that claim.

DP's promotion receipts are authoritative for its own experiment artifacts, but
real model behavior, tool outcomes, and deployment-specific evaluator quality
remain external dependencies.

### Core principles

> Agent behavior is data.
>
> Agent action is not evidence that the action succeeded.
>
> Confidence is behavior, not proof.
>
> One session is not a population.
>
> Multi-agent agreement is not independent verification when agents inherit the
> same evidence or premise.
>
> The goal is not to humanize agents. The goal is to make their behavior
> measurable enough to engineer.

## Quick start

```bash
python3 scripts/self-test.py
python3 scripts/test-routing-learning.py
python3 scripts/acceptance-runtime-triad.py
pytest -q tests
```

The automated release gate is defined by the sibling CFW repository's
[`release-gates.json`](https://github.com/B-A-M-N/CognitiveFrameWorks/blob/master/release-gates.json).
Its guard-policy causal check and private-MCP runtime-triad check are separate
mandatory gates. The runtime triad exercises current DP, CFW, and CSW authorities
together, including a private DP process restart, a CFW final-gate rejection,
lease ownership, and twelve-criterion attention coverage.

## Where is the technical documentation?

| Document                                                        | Contents                                                     |
| --------------------------------------------------------------- | ------------------------------------------------------------ |
| [`docs/triad-overview.md`](docs/triad-overview.md)              | The three authorities and how they fit together              |
| [`docs/architecture.md`](docs/architecture.md)                  | Runtime boundaries, authority, implemented capabilities      |
| [`docs/laya.md`](docs/laya.md)                                  | Provider configuration, shadow/advisory modes, qualification |
| [`docs/experimentation.md`](docs/experimentation.md)            | Trajectories, comparison methodology, profile promotion      |
| [`docs/behavioral-fast-loop.md`](docs/behavioral-fast-loop.md)  | Fast-loop contract                                           |
| [`docs/intervention-outcome-credit.md`](docs/intervention-outcome-credit.md) | Outcome attribution contract                     |
| [`docs/harness-integrations.md`](docs/harness-integrations.md)  | Harness capabilities and host wiring                         |

## Support and limits

Linux is the supported platform; the repository does not claim Windows support.
The optional MCP transport is not required for library-only use.

## License and contribution

These projects are released under the [MIT License](LICENSE). Contributions are
welcome through repository issues and pull requests. Please include a focused
regression or acceptance check for behavior changes, keep authority boundaries
explicit, and do not claim model-performance improvements without the
corresponding qualification evidence.

## FreeInference Acknowledgment

This project was developed with the assistance of AI inference provided by [FreeInference.org](https://freeinference.org/).

This is an independent project. FreeInference did not review, sponsor, direct, supervise, approve, or endorse its development or contents. The project's design, implementation, claims, and maintenance are independent of FreeInference.

If you find this project useful, please consider supporting the infrastructure that helped make its development possible by donating to [FreeInference.org](https://freeinference.org/).
