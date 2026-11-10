"""Emit CSW proposal-inbox artifacts from a System-1 judgment. Formatting only.

A semantic provider (JEV, Laya) judges; this module formats selected
answers into proposal payloads matching the CSW proposal inbox shape
(scripts/proposal_inbox.py). It performs no I/O, mints no evidence,
touches no workflow state, and grants no authority. The host reviews
each proposal and attests its own evidence or ignores it.

Family mapping (advisory lens -> proposal kind -> suggested owner):

- decomposition_preference -> partition_draft (godkiller)
- integration_preference -> integration_plan_draft (terraformer)
- test_strategy_preference -> test_selection_draft (testworks)

Answers for other families yield no proposal: this emitter only speaks
for the three advisory lenses. A staleness_flag is never emitted here;
flagging suspected staleness is a host observation task, not a model
output task.
"""

from __future__ import annotations

from typing import Any, Mapping

FAMILY_TO_KIND = {
    "decomposition_preference": "partition_draft",
    "integration_preference": "integration_plan_draft",
    "test_strategy_preference": "test_selection_draft",
}

FAMILY_TO_OWNER = {
    "decomposition_preference": "godkiller",
    "integration_preference": "terraformer",
    "test_strategy_preference": "testworks",
}

PROPOSAL_ORDER = (
    "test_strategy_preference",
    "decomposition_preference",
    "integration_preference",
)

MAX_SUPPORT_ENTRIES = 8


def _text(name: str, value: Any, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"proposal emitter field {name!r} must be bounded non-empty text")
    return value


def _distribution(answer: Any) -> tuple[tuple[str, float], ...]:
    """Authentic support distribution, calibrated first, raw fallback."""
    distribution = getattr(answer, "calibrated_probabilities", None)
    if distribution is None:
        distribution = getattr(answer, "raw_probabilities", None)
    if not distribution:
        raise ValueError("proposal emitter needs an answer distribution")
    out = []
    for item in distribution:
        label = getattr(item, "label", None)
        probability = getattr(item, "probability", None)
        if not isinstance(label, str) or not label:
            raise ValueError("proposal support label is malformed")
        if (isinstance(probability, bool) or not isinstance(probability, (int, float))
                or not 0.0 <= float(probability) <= 1.0):
            raise ValueError("proposal support probability is malformed")
        out.append((label, float(probability)))
    return tuple(out)


def emit_proposals(judgment: Any, *, subject_ref: str,
                   statework_id: str | None = None,
                   basis_revision: int | None = None,
                   top_n: int = 4) -> tuple[dict[str, Any], ...]:
    """Format one proposal payload per mapped advisory answer present.

    Returns proposal-inbox-shaped payloads (without proposal_id; the
    inbox assigns ids on submit). Families with no answer in the
    judgment are skipped. Raises ValueError on malformed input.
    """
    if not isinstance(top_n, int) or not 1 <= top_n <= MAX_SUPPORT_ENTRIES:
        raise ValueError("proposal emitter top_n is out of bounds")
    subject_ref = _text("subject_ref", subject_ref, 256)
    if statework_id is not None:
        statework_id = _text("statework_id", statework_id, 64)
    if basis_revision is not None and (
            not isinstance(basis_revision, int) or basis_revision < 0):
        raise ValueError("proposal emitter basis_revision must be a non-negative int or absent")
    answer_of = getattr(judgment, "answer", None)
    if not callable(answer_of):
        raise ValueError("proposal emitter needs a judgment with an answer() method")
    author_model = _text("author_model", getattr(judgment, "model_id", None), 256)
    model_revision = _text("model_revision", getattr(judgment, "model_revision", None), 256)
    request_id = _text("request_id", getattr(judgment, "request_id", None), 256)
    state_digest = getattr(judgment, "state_digest", None)

    proposals = []
    for family in PROPOSAL_ORDER:
        answer = answer_of(family)
        if answer is None:
            continue
        selected = _text("selected", getattr(answer, "selected", None), 128)
        support = _distribution(answer)
        matches = [value for label, value in support if label == selected]
        if not matches:
            raise ValueError("proposal emitter selected answer is outside its distribution")
        ranked = sorted(support, key=lambda item: (-item[1], item[0]))[:top_n]
        body = {
            "advisory_family": family,
            "selected": selected,
            "confidence": matches[0],
            "calibrated": bool(getattr(answer, "calibrated_probabilities", None) is not None),
            "support": [{"label": label, "probability": value} for label, value in ranked],
            "provider_decision_id": request_id,
        }
        if state_digest is not None:
            body["judged_state_digest"] = _text("judged_state_digest", state_digest, 128)
        proposals.append({
            "kind": FAMILY_TO_KIND[family],
            "author_model": author_model,
            "model_revision": model_revision,
            "subject_ref": subject_ref,
            "statework_id": statework_id or FAMILY_TO_OWNER[family],
            "body": body,
            "basis_revision": basis_revision,
        })
    return tuple(proposals)


__all__ = [
    "FAMILY_TO_KIND",
    "FAMILY_TO_OWNER",
    "PROPOSAL_ORDER",
    "emit_proposals",
]
