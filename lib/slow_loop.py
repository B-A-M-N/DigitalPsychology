"""Bounded Digital Psychology slow-loop orchestration.

The controller coordinates existing trusted primitives. It never decides
eligibility, mints evaluator evidence, or promotes without a verified receipt.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .routing_lifecycle import RoutingLifecycleStore
from .routing_profiles import (BehavioralRoutingProfile, RoutingProfileError,
                               build_routing_pack, derive_profiles, promote_profile,
                               validate_profile)


@dataclass
class SlowLoopReport:
    status: str
    candidates: list[dict[str, Any]]
    promoted: list[str]
    rejected: list[dict[str, Any]]


class BoundedLearningController:
    """One bounded observe → derive → validate → promote cycle.

    Inputs are already authenticated events and host task contexts. The
    controller clusters independent trajectories, builds bounded candidate
    profiles, and only promotes a profile when its predeclared receipt passes.
    """

    def __init__(self, *, lifecycle: RoutingLifecycleStore,
                 minimum_samples: int = 10, minimum_effect: float = 0.10) -> None:
        if minimum_samples < 1:
            raise ValueError("slow-loop minimum_samples must be positive")
        self.lifecycle = lifecycle
        self.minimum_samples = minimum_samples
        self.minimum_effect = minimum_effect

    def run(self, events: Iterable[Any], task_context: Mapping[str, Mapping[str, Any]]) -> SlowLoopReport:
        event_list = list(events)
        candidates = derive_profiles(
            event_list, task_context, minimum_samples=self.minimum_samples,
            minimum_effect=self.minimum_effect)
        promoted: list[str] = []
        rejected: list[dict[str, Any]] = []
        for candidate in candidates:
            try:
                receipt = dict((candidate.get("evidence") or {}).get("experiment_receipt") or {})
                if receipt.get("decision") != "pass":
                    raise RoutingProfileError("candidate lacks a passing experiment receipt")
                active = promote_profile(candidate, receipt, status="active")
                # This is a deployment observation, not a new source of truth.
                active = self.lifecycle.observe(active, [{
                    "profile_hash": active["profile_hash"],
                    "trajectory_id": f"slow-loop-{active['profile_hash'][:20]}",
                    "eligible": True,
                }])
                validate_profile(active, require_deployable=True)
                promoted.append(str(active["profile_hash"]))
            except (RoutingProfileError, TypeError, ValueError) as exc:
                rejected.append({"profile_id": candidate.get("profile_id"),
                                 "reason": str(exc)})
        return SlowLoopReport(
            status="completed" if not rejected else "completed_with_rejections",
            candidates=candidates, promoted=promoted, rejected=rejected)
