"""Model the dependency-owned portion of a CSW selection across a tick."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def expected_csw_active_stateworks(
        basis: Mapping[str, Any], directives: Sequence[Mapping[str, Any]],
        post_dependency_retention: Mapping[str, Sequence[str]]) -> set[str]:
    """Apply DP requests and CSW dependency retention with cascade semantics.

    CSW may remove dependency-owned ancestors when their final active dependent
    is released. Apply that closure against the frozen basis first, then account
    for dependencies introduced or retained by the post-application snapshot.
    """
    active = set(basis.get("csw_active_stateworks") or ())
    basis_retention = basis.get("csw_dependency_retention") or {}
    if not isinstance(basis_retention, Mapping):
        raise ValueError("CSW dependency retention in the basis is malformed")

    for directive in directives:
        operation = directive.get("operation")
        target = str(directive.get("target") or "")
        if operation == "release_statework":
            if target not in active:
                raise ValueError("CSW release target was not active in the basis")
            active.remove(target)
        elif operation == "prefer_statework":
            if target in active:
                raise ValueError("CSW activation target was already active")
            active.add(target)

    def cascade(retention: Mapping[str, Sequence[str]]) -> None:
        changed = True
        while changed:
            changed = False
            for retained_id, required_by in retention.items():
                if (retained_id in active
                        and not (set(required_by) & active)):
                    active.remove(retained_id)
                    changed = True

    cascade(basis_retention)

    if not isinstance(post_dependency_retention, Mapping):
        raise ValueError("CSW post-application dependency retention is malformed")
    for retained_id, required_by in post_dependency_retention.items():
        dependents = set(required_by)
        if not dependents or not dependents.issubset(active):
            raise ValueError("CSW retained a prerequisite without active dependents")
        active.add(retained_id)
    cascade(post_dependency_retention)
    return active
