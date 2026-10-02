"""Bounded, host-only summaries of optional System-1 provider results."""
from __future__ import annotations

from typing import Any, Mapping


def summarize_system_one_observation(
        prepared: Mapping[str, Any], *, tick_status: str
        ) -> dict[str, Any] | None:
    """Extract provider identity and typed answers without retaining its frame."""
    basis = prepared.get("basis")
    if not isinstance(basis, Mapping):
        return None
    system_one = basis.get("system_one")
    if not isinstance(system_one, Mapping):
        return None
    frame = system_one.get("frame")
    result = system_one.get("result")
    if not isinstance(frame, Mapping) or not isinstance(result, Mapping):
        return None
    judgment = result.get("judgment")
    if not isinstance(judgment, Mapping):
        judgment = {}

    answers = []
    raw_answers = judgment.get("answers")
    if isinstance(raw_answers, (tuple, list)):
        for answer in raw_answers[:8]:
            if not isinstance(answer, Mapping):
                continue
            question_id = answer.get("question_id")
            selected = answer.get("selected")
            if not isinstance(question_id, str) or not isinstance(selected, str):
                continue
            row: dict[str, Any] = {
                "question_id": question_id,
                "selected": selected,
            }
            for source, target in (("raw_probabilities", "raw_confidence"),
                                   ("calibrated_probabilities", "calibrated_confidence")):
                probabilities = answer.get(source)
                if not isinstance(probabilities, (tuple, list)):
                    continue
                match = next((item for item in probabilities
                              if isinstance(item, Mapping)
                              and item.get("label") == selected), None)
                if match is not None and isinstance(match.get("probability"), (int, float)):
                    row[target] = float(match["probability"])
            answers.append(row)

    return {
        "tick_id": str(prepared.get("tick_id") or ""),
        "tick_status": tick_status,
        "status": str(result.get("status") or "unknown"),
        "reason": result.get("reason"),
        "cache_hit": bool(result.get("cache_hit", False)),
        "calibrated_question_ids": list(
            result.get("calibrated_question_ids") or ())[:8],
        "backend_id": judgment.get("backend_id"),
        "model_id": judgment.get("model_id"),
        "model_revision": judgment.get("model_revision"),
        "model_content_hash": judgment.get("model_content_hash"),
        "provider_protocol_hash": judgment.get("provider_protocol_hash"),
        "calibration_bundle_hash": judgment.get("calibration_bundle_hash"),
        "state_digest": judgment.get("state_digest"),
        "question_bundle_digest": judgment.get("question_bundle_digest"),
        "sequence": judgment.get("sequence"),
        "answers": answers,
    }


__all__ = ["summarize_system_one_observation"]
