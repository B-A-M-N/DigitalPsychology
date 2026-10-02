"""Rank only CSW-authorized StateWork choices for DP behavior candidates."""
from __future__ import annotations

from ..behavior_state import StateWorkOption
from .policy_ambiguity import PolicyAmbiguity


_MODE_TAGS = {
    "RECOVER": {"recovery", "recover", "repair", "diagnose"},
    "DIAGNOSE": {"diagnose", "diagnostic", "investigate"},
    "REINSPECT": {"reinspect", "reopen", "evidence"},
    "VERIFY": {"verify", "verification", "validation"},
    "READY_TO_COMPLETE": {"complete", "completion"},
    "DELIVER": {"deliver", "implementation", "build"},
    "REPLAN": {"plan", "replan", "decompose"},
}


def statework_scores(mode: str, eligible: tuple[StateWorkOption, ...],
                     active: tuple[str, ...] = ()) -> tuple[tuple[str, int], ...]:
    """Return stable deterministic scores over the eligible inactive set."""
    terms = _MODE_TAGS.get(mode, set())
    scored = [(item.statework_id,
               len({tag.lower() for tag in item.behavior_tags} & terms))
              for item in eligible if item.statework_id not in active]
    return tuple(sorted(scored, key=lambda item: (-item[1], item[0])))


def rank_stateworks(mode: str, eligible: tuple[StateWorkOption, ...],
                    active: tuple[str, ...] = (), *,
                    semantic_preference: str | None = None,
                    semantic_confidence: float = 0.0,
                    minimum_semantic_confidence: float = 0.80,
                    limit: int = 4) -> tuple[str, ...]:
    """Rank legal options; a calibrated preference may only reorder eligible IDs."""
    if (type(limit) is not int or not 1 <= limit <= 32
            or not 0.0 <= float(semantic_confidence) <= 1.0
            or not 0.0 <= float(minimum_semantic_confidence) <= 1.0):
        raise ValueError("StateWork ranking bounds are invalid")
    ranked = [statework_id for statework_id, _score
              in statework_scores(mode, eligible, active)]
    if (semantic_preference in ranked
            and semantic_confidence >= minimum_semantic_confidence):
        ranked.remove(semantic_preference)
        ranked.insert(0, semantic_preference)
    return tuple(ranked[:limit])


def statework_ambiguity(mode: str, eligible: tuple[StateWorkOption, ...],
                        active: tuple[str, ...] = (), *,
                        candidates_limit: int = 24) -> PolicyAmbiguity | None:
    """Describe a close top pair so route-preference VOI can be gated."""
    scored = statework_scores(mode, eligible, active)[:candidates_limit]
    if len(scored) < 2:
        return None
    (top_id, top_score), (second_id, second_score) = scored[:2]
    gap = max(0, top_score - second_score) / max(1, top_score)
    return PolicyAmbiguity(top_id, second_id, float(gap), ("statework_fit",))


def eligible_statework_descriptions(
        eligible: tuple[StateWorkOption, ...], active: tuple[str, ...] = (), *,
        limit: int = 24) -> tuple[tuple[str, str], ...]:
    """Build bounded model-facing descriptions without loading workflow files."""
    values = []
    active_ids = set(active)
    for item in eligible:
        if item.statework_id in active_ids:
            continue
        description = " ".join(part for part in (
            f"Purpose: {item.purpose}" if item.purpose else "",
            f"Domain: {item.domain}" if item.domain else "",
            "Behavior tags: " + ", ".join(item.behavior_tags)
            if item.behavior_tags else "",
        ) if part)
        values.append((item.statework_id, description[:512] or "Eligible StateWork."))
    return tuple(values[:limit])


__all__ = ["eligible_statework_descriptions", "rank_stateworks",
           "statework_ambiguity", "statework_scores"]
