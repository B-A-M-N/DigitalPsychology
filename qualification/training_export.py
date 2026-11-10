"""Export contract-grounded cases into Laya's training JSONL format.

The pinned `.venv-laya` ships laya **0.3.22**, which has NO `laya.train` module --
fine-tuning arrived in 0.4.6 (`laya/train.py`, `laya.train_cli:main` -> `laya-train`).
So this module writes the *data* in the PUBLIC input schema
`laya.train.finetune` consumes, so that a training run is possible the moment a
>=0.4.6 environment exists. It does not import laya, so it runs on the pinned
environment unchanged.

The upstream contract, read from the laya 0.4.7 wheel (`agent.py`, `train.py`):

  A JSONL row is `{state, questions, gold}`.

  `questions` maps question-id -> a PUBLIC question definition, which is the
  shape `Agent._check_question` accepts:

      {"type": "choice", "instructions": <str>, "criteria": {label: text}}

  NOT `{"t", "crit", "ins"}`. That triple is the OUTPUT of `Agent._to_internal`
  -- training calls `to_internal(qid, question)` itself, so emitting the internal
  shape means the public check sees none of the keys it reads and rejects the
  row as `invalid_question`. This was a real defect: the earlier export used the
  internal-looking shape and its local precheck could not catch it.

  `gold` maps question-id -> `{probabilities: {label: p}}`, which is correct in
  both shapes and is what `target_from_gold` consumes.

  `target_from_gold` looks probabilities up BY CHOICE LABEL, normalises, and
  turns a row whose probabilities sum to zero into a UNIFORM target.

That last point is why ambiguous cases are handled explicitly here rather than
dropped: `no_eligible_obligation_satisfiable` and `multiple_eligible` both mean
"no defensible preference", and the honest target is a uniform distribution. We
label them so the eval can measure abstention instead of guessing.
"""
from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from qualification.canonical_input import (
    CANONICAL_INSTRUCTION,
    canonical_description,
)
from qualification.canonical_input import (
    canonical_state as _canon_state,
)
from qualification.contrastive_fixtures import (
    ContrastiveCase,
    default_partitions,
    generate_cases,
)

QUESTION_ID = "route_preference"
# Labels are the StateWork ids, NOT A/B/C. Upstream renders each option as its
# label plus description, and the production adapter speaks StateWork ids; using
# letters here was a second, subtler train/serve skew.
ROUTE_LABELS = {"getter": "getter", "gitter": "gitter", "infrae": "infrae"}

# The instruction is shared with serving so training and runtime text match.
INSTRUCTION = CANONICAL_INSTRUCTION


def _option_text(case: ContrastiveCase, sid: str) -> str:
    """The canonical rendering, shared with serving (see canonical_input).

    Previously this returned a bespoke string that matched neither the
    production adapter's Purpose/Domain/Behavior-tags form nor anything else --
    a train/serve skew. It now goes through the one shared path.
    """
    return canonical_description(sid, case.obligations.get(sid, ()),
                                 case.held_evidence)


def build_question(case: ContrastiveCase) -> dict[str, Any]:
    """The PUBLIC Laya question: `type`/`instructions`/`criteria`.

    Options carry obligation + availability, which is the pre-decision fact that
    distinguishes the routes. They never carry the answer.

    `criteria` is a dict of label -> text, which `_check_question` accepts for
    `choice` (a list is also legal and would become `{label: None}`, losing every
    description -- so a dict is required here).
    """
    options = {}
    for sid in case.eligible:
        options[ROUTE_LABELS[sid]] = _option_text(case, sid)
    return {
        "type": "choice",
        "instructions": INSTRUCTION,
        "criteria": options,
    }


def build_gold(case: ContrastiveCase) -> dict[str, Any]:
    """Target distribution over labels.

    * unambiguous  -> the contracted route gets 1.0, others 0.0
    * ambiguous    -> uniform, which is the honest "no defensible preference"
      and is exactly what laya's zero-sum normalisation would produce anyway;
      being explicit documents intent instead of relying on that fallback.
    """
    probabilities: dict[str, float] = {ROUTE_LABELS[s]: 0.0
                                       for s in case.eligible}
    if case.gold is not None:
        probabilities[ROUTE_LABELS[case.gold]] = 1.0
    else:
        share = 1.0 / len(probabilities)
        probabilities = {label: share for label in probabilities}
    return {"probabilities": probabilities}


def build_state(case: ContrastiveCase) -> str:
    """The task state Laya reads, built by the one shared canonical path."""
    return _canon_state(case.objective, case.eligible, case.held_evidence)


def case_to_row(case: ContrastiveCase) -> dict[str, Any]:
    return {
        "state": build_state(case),
        "questions": {QUESTION_ID: build_question(case)},
        "gold": {QUESTION_ID: build_gold(case)},
    }


def export_rows(cases: Iterable[ContrastiveCase]) -> list[dict[str, Any]]:
    return [case_to_row(case) for case in cases]


def write_jsonl(path: str | Path, cases: Iterable[ContrastiveCase]) -> int:
    rows = export_rows(cases)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8") as handle:
        handle.writelines(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    return len(rows)


def split_disjoint(cases: Sequence[ContrastiveCase],
                   calib_frac: float = 0.1,
                   seed: int = 20260922
                   ) -> tuple[list[ContrastiveCase], list[ContrastiveCase],
                              list[ContrastiveCase]]:
    """Train / calibration / holdout, DISJOINT by CONSTRUCTION -- by task instance.

    Distinct case ids do not imply independent examples: two cases about the same
    referent share wording and referent, so holding out one and training on the
    other leaks. This split therefore assigns whole TASK INSTANCES to partitions
    and never splits an instance, which is the property the earlier
    rotation-based split could not give.

    It also keeps the adversarial property: because every capability contributes
    all of its rotations within each partition, the holdout still contains gold
    that contradicts the canonical route names -- a name-matching model cannot
    pass it, and now it cannot pass it on unseen targets either.

    Delegates to `default_partitions`, which is where the block logic lives; kept
    here as the name callers already use.
    """
    partitions = default_partitions(cases)
    return (partitions["train"], partitions["calibration"],
            partitions["holdout"])


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="/tmp/dp-audit/laya-train")
    args = parser.parse_args()

    cases = generate_cases()
    train, calibration, holdout = split_disjoint(cases)
    out = Path(args.out)
    counts = {}
    for name, subset in (("train", train), ("calibration", calibration),
                         ("holdout", holdout)):
        counts[name] = write_jsonl(out / f"{name}.jsonl", subset)
    print(json.dumps({
        "cases": len(cases), "splits": counts,
        "holdout_rule": "task instances held out per family; gold contradicts canonical names",
        "advisory_mode": "closed",
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
