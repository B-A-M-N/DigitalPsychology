"""Pre-validate exported rows against laya's real `items_from_rows` contract.

`laya.train.dry_run` is the authoritative check, but it ships in laya >= 0.4.6 and
the pinned `.venv-laya` has 0.3.22. The 16 GB GPU the plan assumes is also not on
this box (`nvidia-smi` absent, torch 2.5.1+cpu). So a training run cannot even be
staged here yet.

This module reimplements the documented rejection rules by reading them out of the
0.4.7 wheel, so the data can be validated now and re-checked authoritatively
later. It mirrors `items_from_rows`:

    empty_text            state missing or blank
    invalid_question      `to_internal` raised (ValueError/TypeError/KeyError)
    invalid_target        `target_from_gold` raised
    target_mismatch       target length != option count
    options_collapsed     two options collapsed to the same token span
    options_beyond_max_len  last option marker >= max_len

IMPORTANT: the question shape validated here is the PUBLIC input contract
`type`/`instructions`/`criteria`, because `to_internal` reads it. Validating the
internal `t`/`crit`/`ins` triple instead is a real bug -- `_to_internal` PRODUCES
that triple, so a row written in it has none of the keys the public check reads
and is rejected as `invalid_question`. This module checks the input contract.

The last three skip reasons come from `make_item` and need a tokenizer, so they
are checked structurally (option counts and length ceilings) rather than by
running the real code. Anything this module marks clean STILL needs `dry_run` on
a laya>=0.4.6 environment before training -- this is the cheap first pass, not
the authority.
"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Skip reasons, in the order items_from_rows assigns them.
SKIP_REASONS = (
    "empty_text", "invalid_question", "invalid_target", "target_mismatch",
    "options_collapsed", "options_beyond_max_len",
)

# Question types laya understands, from the 0.4.7 agent.py QTYPES table.
KNOWN_TYPES = {"choice", "noul", "score"}

# The PUBLIC question keys `_check_question` reads.
PUBLIC_TYPE_KEY = "type"
PUBLIC_INSTRUCTIONS_KEY = "instructions"
PUBLIC_CRITERIA_KEY = "criteria"

# The INTERNAL keys `_to_internal` produces. A row carrying these is in the
# wrong shape and must be caught rather than silently accepted.
INTERNAL_KEYS = {"t", "crit", "ins"}


@dataclass(frozen=True)
class RowVerdict:
    index: int
    case_id: str | None
    ok: bool
    reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return {"index": self.index, "case_id": self.case_id,
                "ok": self.ok, "reason": self.reason}


def _option_count(question: Mapping[str, Any]) -> int | None:
    """Number of options as laya counts them, or None if malformed.

    For a `choice` question laya counts `criteria` entries. A tuple/list form is
    legal at input (`_to_internal` turns it into `{label: None}`) but would discard
    every description, so it is reported as malformed here -- the exporter is
    supposed to emit a dict.
    """
    if not isinstance(question, Mapping):
        return None
    qtype = question.get(PUBLIC_TYPE_KEY)
    if qtype not in KNOWN_TYPES:
        return None
    if qtype != "choice":
        return None
    criteria = question.get(PUBLIC_CRITERIA_KEY)
    if not isinstance(criteria, Mapping) or not criteria:
        return None
    return len(criteria)


def _check_question_shape(question: Mapping[str, Any]) -> str | None:
    """Mirror `Agent._check_question`, the PUBLIC input contract.

    Correct per 0.4.7 agent.py:
      * `type` must be a known question type
      * `instructions` must be present and non-empty (str, dict or list)
      * `criteria` must be a dict/list of non-empty entries for `choice`
      * `option_order` IS accepted at input (only popped internally), so its
        presence is NOT a rejection -- an earlier version wrongly rejected it.
    """
    if not isinstance(question, Mapping):
        return "invalid_question"

    # A row written in the internal shape is the wrong contract entirely.
    if set(question) & INTERNAL_KEYS:
        return "invalid_question"

    qtype = question.get(PUBLIC_TYPE_KEY)
    if not isinstance(qtype, str) or qtype not in KNOWN_TYPES:
        return "invalid_question"

    if PUBLIC_INSTRUCTIONS_KEY not in question:
        return "invalid_question"
    instruction = question[PUBLIC_INSTRUCTIONS_KEY]
    if instruction is None:
        return "invalid_question"
    if isinstance(instruction, (list, dict)) and not instruction:
        return "invalid_question"
    if not isinstance(instruction, (str, dict, list, int, float)):
        return "invalid_question"
    if isinstance(instruction, str) and not instruction.strip():
        return "invalid_question"

    criteria = question.get(PUBLIC_CRITERIA_KEY)
    if not isinstance(criteria, Mapping):
        return "invalid_question"
    if not criteria:
        return "invalid_question"
    for label, text in criteria.items():
        if not isinstance(label, str):
            # laya looks probabilities up by choice label; non-str is a KeyError.
            return "invalid_target"
        if isinstance(text, str) and not text.strip():
            return "invalid_question"
    return None


def _target_length_satisfiable(question: Mapping[str, Any],
                              gold: Mapping[str, Any]) -> str | None:
    """Mirror `target_from_gold` + the `target_mismatch` rule in `make_item`."""
    count = _option_count(question)
    if count is None:
        return "invalid_question"
    probabilities = gold.get("probabilities")
    if not isinstance(probabilities, Mapping):
        return "invalid_target"
    if not probabilities:
        return "invalid_target"
    labels = set(question[PUBLIC_CRITERIA_KEY])
    if not set(probabilities) <= labels:
        # Unknown labels are dropped by target_from_gold, which can leave a
        # target of the wrong length -- make_item's target_mismatch.
        return "target_mismatch"
    values = list(probabilities.values())
    for value in values:
        if not isinstance(value, (int, float)):
            return "invalid_target"
        if isinstance(value, bool):
            return "invalid_target"
        if value < 0:
            # target_from_gold raises on negative or non-finite values.
            return "invalid_target"
    if sum(values) > 0 and len(values) != count:
        return "target_mismatch"
    return None


def validate_row(index: int, row: Mapping[str, Any],
                 max_len: int = 512) -> RowVerdict:
    """Validate one row against laya's documented per-row rules.

    `max_len` defaults to the checkpoint's own `rl_agent_config.json` budget;
    the caller can override it with the value the real config declares.
    """
    case_id = row.get("case_id")

    state = row.get("state")
    if state is None or (isinstance(state, str) and not state.strip()):
        return RowVerdict(index, case_id, False, "empty_text")

    # Structural stand-in for options_beyond_max_len: the state text that laya
    # tokenises for this row must plausibly fit the checkpoint budget. The real
    # check is on option token spans, which needs a tokenizer.
    if isinstance(state, str) and len(state) > max_len * 4:
        return RowVerdict(index, case_id, False, "options_beyond_max_len")

    questions = row.get("questions")
    golds = row.get("gold")
    if not isinstance(questions, Mapping) or not questions:
        return RowVerdict(index, case_id, False, "invalid_question")
    if not isinstance(golds, Mapping):
        return RowVerdict(index, case_id, False, "invalid_target")

    for qid, question in questions.items():
        reason = _check_question_shape(question)
        if reason is not None:
            return RowVerdict(index, case_id, False, reason)
        gold_q = golds.get(qid)
        if gold_q is None:
            # Unlabelled questions are not counted as skipped by laya.
            continue
        if not isinstance(gold_q, Mapping):
            return RowVerdict(index, case_id, False, "invalid_target")
        reason = _target_length_satisfiable(question, gold_q)
        if reason is not None:
            return RowVerdict(index, case_id, False, reason)
    return RowVerdict(index, case_id, True, None)


def validate_jsonl(path: str | Path, max_len: int = 512
                   ) -> tuple[list[RowVerdict], dict[str, int]]:
    """Validate every row in a JSONL file.

    Returns (verdicts, skipped_counts) where skipped_counts uses laya's own
    reason vocabulary, so the numbers can be compared directly against a real
    `dry_run` later.
    """
    verdicts: list[RowVerdict] = []
    skipped: dict[str, int] = {}
    with open(path, encoding="utf-8") as handle:
        for index, line in enumerate(handle):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            verdict = validate_row(index, json.loads(line), max_len=max_len)
            verdicts.append(verdict)
            if not verdict.ok:
                reason = verdict.reason or "unknown"
                skipped[reason] = skipped.get(reason, 0) + 1
    return verdicts, skipped


def summarise(verdicts: Sequence[RowVerdict]) -> dict[str, Any]:
    ok = sum(1 for v in verdicts if v.ok)
    bad = [v for v in verdicts if not v.ok]
    by_reason: dict[str, int] = {}
    for verdict in bad:
        reason = verdict.reason or "unknown"
        by_reason[reason] = by_reason.get(reason, 0) + 1
    return {
        "rows": len(verdicts),
        "valid": ok,
        "skipped": len(bad),
        "skip_reasons": by_reason,
        "examples": [v.to_dict() for v in bad[:5]],
    }


def main(argv: Sequence[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", help="JSONL files to validate")
    parser.add_argument("--max-len", type=int, default=512)
    args = parser.parse_args(argv)

    all_ok = True
    for path in args.paths:
        target = Path(path)
        if not target.exists():
            print(f"{path}: MISSING")
            all_ok = False
            continue
        verdicts, skipped = validate_jsonl(target, max_len=args.max_len)
        summary = summarise(verdicts)
        print(f"\n=== {target.name} ===")
        print(json.dumps(summary, indent=2))
        if skipped:
            all_ok = False
    print()
    print("ALL ROWS CLEAN" if all_ok else "SKIPS PRESENT -- fix before dry_run")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
