"""Rank only CSW-authorized StateWork choices for DP behavior candidates."""
from __future__ import annotations

import math

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


ROUTE_PREFERENCE_PROVENANCE = frozenset({"model_advisory", "host_experiment"})

# Per-decision ambiguity floor. Distinct from the Stage-B *aggregate*
# qualification margin (mean top1-top2 separation over a benchmark, floor 0.15).
# The Stage-B run's own per-case margins spanned 0.0298-0.2090 with mean 0.1274,
# so 0.15 is demonstrably an aggregate bar, not a per-decision one. This floor
# rejects only genuinely uninformative splits (ties and near-ties) while leaving
# the qualified model's real per-case behaviour admissible.
DEFAULT_ROUTE_PREFERENCE_MARGIN = 0.05


def _validated_preference_support(
        preference_support: tuple[tuple[str, float], ...] | None,
        eligible_ids: set[str]) -> dict[str, float] | None:
    """Validate a preference distribution; return None when none was supplied.

    A semantic advisory must carry an authentic distribution so ambiguity can be
    detected. A host experiment assignment carries none: it is an authorized
    forced choice, not a prediction.
    """
    if preference_support is None:
        return None
    if not isinstance(preference_support, (tuple, list)) or not preference_support:
        raise ValueError("route preference support must be a non-empty sequence")
    support: dict[str, float] = {}
    for item in preference_support:
        if not isinstance(item, (tuple, list)) or len(item) != 2:
            raise ValueError("route preference support entries must be pairs")
        statework_id, value = item
        if not isinstance(statework_id, str) or not statework_id or len(statework_id) > 64:
            raise ValueError("route preference support identity is malformed")
        if statework_id in support:
            raise ValueError("route preference support repeats a StateWork")
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or not 0.0 <= float(value) <= 1.0):
            raise ValueError("route preference support must be a finite probability")
        support[statework_id] = float(value)
    unknown = set(support) - eligible_ids
    if unknown:
        raise ValueError(f"route preference support names ineligible StateWorks: {sorted(unknown)}")
    return support


def rank_stateworks(mode: str, eligible: tuple[StateWorkOption, ...],
                    active: tuple[str, ...] = (), *,
                    semantic_preference: str | None = None,
                    semantic_confidence: float = 0.0,
                    minimum_semantic_confidence: float = 0.80,
                    minimum_preference_margin: float = DEFAULT_ROUTE_PREFERENCE_MARGIN,
                    preference_support: tuple[tuple[str, float], ...] | None = None,
                    provenance: str | None = None,
                    limit: int = 4) -> tuple[str, ...]:
    """Rank legal options; a qualified preference may only reorder eligible IDs.

    Provenance separates the two legitimate reasons a preference may be placed
    first:

    ``model_advisory``
        A calibrated model prediction. It must carry an authentic preference
        distribution, clear the confidence threshold, and clear the preference
        margin. Confidence alone is never sufficient: a 0.50/0.50 split carries
        no information regardless of how confident the model reports itself.
    ``host_experiment``
        A host-authorized treatment assignment. It is not a prediction and
        carries no distribution, so it must be declared explicitly and is
        auditable as an experimental intervention rather than as model evidence.

    Omitting provenance when a preference is supplied fails closed: an
    undeclared source cannot be distinguished from a forged confidence value.
    """
    if (type(limit) is not int or not 1 <= limit <= 32
            or not 0.0 <= float(semantic_confidence) <= 1.0
            or not 0.0 <= float(minimum_semantic_confidence) <= 1.0
            or not 0.0 <= float(minimum_preference_margin) <= 1.0):
        raise ValueError("StateWork ranking bounds are invalid")
    if provenance is not None and provenance not in ROUTE_PREFERENCE_PROVENANCE:
        raise ValueError(f"unsupported route preference provenance: {provenance!r}")
    if semantic_preference is not None and provenance is None:
        raise ValueError(
            "a route preference requires explicit provenance "
            "(model_advisory or host_experiment)")
    ranked = [statework_id for statework_id, _score
              in statework_scores(mode, eligible, active)]
    if semantic_preference is None or semantic_preference not in ranked:
        return tuple(ranked[:limit])
    if provenance == "host_experiment":
        # A host-authorized assignment is deliberately forced. It is still
        # bounded to the CSW-eligible set above and remains auditable.
        ranked.remove(semantic_preference)
        ranked.insert(0, semantic_preference)
        return tuple(ranked[:limit])
    if semantic_confidence < minimum_semantic_confidence:
        return tuple(ranked[:limit])
    support = _validated_preference_support(
        preference_support, {item.statework_id for item in eligible})
    if support is None:
        # A model advisory without an authentic distribution cannot
        # demonstrate an unambiguous preference.
        return tuple(ranked[:limit])
    ordered = sorted(support.items(), key=lambda item: (-item[1], item[0]))
    if len(ordered) < 2:
        return tuple(ranked[:limit])
    top_id, top_value = ordered[0]
    margin = top_value - ordered[1][1]
    if top_id != semantic_preference or margin < minimum_preference_margin:
        return tuple(ranked[:limit])
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
