"""Stable, evidence-scoped identities for behavioral trigger instances."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Sequence

from .behavior_state import CSWBehaviorSnapshot, GoalSetSnapshot


@dataclass(frozen=True)
class BehaviorConditionIdentity:
    condition_id: str
    condition_basis_digest: str


def identify_condition(*, cause: str, target_criteria: Sequence[str],
                       goal_set: GoalSetSnapshot | None,
                       csw_snapshot: CSWBehaviorSnapshot,
                       evidence_refs: Sequence[str],
                       approach_epoch: str = "") -> BehaviorConditionIdentity:
    """Bind a trigger to the objective, workflow, evidence, and approach epoch.

    The identity deliberately excludes action index and behavior revision: those
    advance while an unchanged condition is being evaluated. Host-supplied
    ``approach_epoch`` distinguishes a genuinely new hypothesis even when it
    has not yet produced a new evidence reference.
    """
    criteria = tuple(sorted(set(target_criteria)))
    targets = ({item.criterion_id: item.to_dict() for item in goal_set.items
                if item.criterion_id in criteria} if goal_set is not None else {})
    workflow = {
        "active_stateworks": sorted(csw_snapshot.active_stateworks),
        "workflow_states": dict(sorted(csw_snapshot.workflow_states.items())),
        "workflow_revisions": dict(sorted(csw_snapshot.workflow_revisions.items())),
        "eligible_stateworks": sorted(
            item.statework_id for item in csw_snapshot.eligible_stateworks),
    }
    basis = {
        "cause": cause,
        "target_criteria": criteria,
        "goal_set_revision": goal_set.revision if goal_set is not None else None,
        "goal_items": targets,
        "workflow": workflow,
        "evidence_refs": tuple(sorted(set(evidence_refs))),
        "approach_epoch": approach_epoch,
    }
    digest = hashlib.sha256(json.dumps(
        basis, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")).hexdigest()
    return BehaviorConditionIdentity("condition-" + digest[:32], digest)


__all__ = ["BehaviorConditionIdentity", "identify_condition"]
