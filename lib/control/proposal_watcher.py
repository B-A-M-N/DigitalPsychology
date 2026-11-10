"""Host-side watcher: feed judgments into the proposal inbox, surface inbox for review.

This module is host glue, not authority. It does two things:

1. Feed in: take a finished System-1 judgment, format advisory answers
   via proposal_emitter, and submit each payload through an injected
   ``submit`` callable (the CSW ProposalInbox.submit in production, a
   fake in tests). Submitting never advances workflow state; the inbox
   is bookkeeping and the host still attests its own evidence.
2. Review out: turn an inbox listing into a bounded operator snapshot
   (kind, owner, selected lens, confidence, age) for review tooling.

Dependency direction is deliberate: this module never imports CSW. The
host wires the real inbox ``submit`` / ``list_open`` callables in. A
submit failure is reported per item, never swallowed: the receipt marks
it failed with the error text so the host sees the gap.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

from .proposal_emitter import emit_proposals

REVIEW_SUPPORT_PREVIEW = 3
REVIEW_SELECTED_LIMIT = 128


def drain_judgment(judgment: Any, *, submit: Callable[[Mapping[str, Any]], str],
                   subject_ref: str, statework_id: str | None = None,
                   basis_revision: int | None = None) -> list[dict[str, Any]]:
    """Emit advisory proposals from one judgment and submit each.

    Returns one receipt per emitted proposal: submitted ones carry the
    inbox proposal_id; failed ones carry ok=False plus the error. A
    judgment with no mapped advisory answers yields no receipts (the
    common case) rather than an error.
    """
    if not callable(submit):
        raise ValueError("proposal watcher needs a submit callable")
    receipts = []
    for payload in emit_proposals(judgment, subject_ref=subject_ref,
                                  statework_id=statework_id,
                                  basis_revision=basis_revision):
        try:
            proposal_id = submit(payload)
        except Exception as exc:  # noqa: BLE001 - reported, not swallowed
            receipts.append({"ok": False, "kind": payload["kind"],
                             "error": f"{type(exc).__name__}: {exc}"[:256]})
            continue
        receipts.append({"ok": True, "kind": payload["kind"],
                         "proposal_id": proposal_id})
    return receipts


def review_snapshot(open_proposals: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Reduce an inbox open-listing to a bounded operator review snapshot."""
    if not isinstance(open_proposals, list):
        raise ValueError("proposal watcher needs an inbox open-listing")
    rows = []
    for item in open_proposals:
        if not isinstance(item, Mapping):
            raise ValueError("proposal listing entry is malformed")
        body = item.get("body")
        support = body.get("support") if isinstance(body, Mapping) else None
        preview = []
        if isinstance(support, list):
            for entry in support[:REVIEW_SUPPORT_PREVIEW]:
                if not isinstance(entry, Mapping):
                    raise ValueError("proposal support preview is malformed")
                preview.append({
                    "label": str(entry.get("label"))[:64],
                    "probability": entry.get("probability"),
                })
        rows.append({
            "proposal_id": str(item.get("proposal_id"))[:64],
            "kind": str(item.get("kind"))[:64],
            "statework_id": (None if item.get("statework_id") is None
                             else str(item.get("statework_id"))[:64]),
            "selected": (str(body.get("selected"))[:REVIEW_SELECTED_LIMIT]
                         if isinstance(body, Mapping) else None),
            "confidence": body.get("confidence") if isinstance(body, Mapping) else None,
            "author": (f"{item.get('author_model')}:{item.get('model_revision')}"[:128]
                       if item.get("author_model") else None),
            "submitted_at": item.get("submitted_at"),
            "support_preview": preview,
        })
    return rows


__all__ = [
    "drain_judgment",
    "review_snapshot",
]
