"""Reversible local application of DP directives through CFW and CSW ports."""
from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any, Mapping, Sequence

from ..behavior_state import (BehaviorState, CSWBehaviorSnapshot,
                              StateWorkReleaseResult)
from ..compatibility import MAX_CONTROL_BATCH_ITEMS
from ..lease_window import intervention_expiry
from ..runtime.cfw_projection import build_cfw_behavior_projection

_LOG = logging.getLogger(__name__)


def _metric_increment(metrics: Any, name: str) -> None:
    increment = getattr(metrics, "increment", None)
    if callable(increment):
        try:
            increment(name)
        except Exception:
            _LOG.warning("could not persist an authority rejection metric", exc_info=True)


@dataclass(frozen=True)
class ControlApplication:
    applied_directive_ids: tuple[str, ...]
    cfw_lifecycle_revision: int
    csw_snapshot: CSWBehaviorSnapshot


class ActuationFailure(RuntimeError):
    def __init__(self, message: str, *, applied_directive_ids: Sequence[str],
                 rollback_completed: bool) -> None:
        super().__init__(message)
        self.applied_directive_ids = tuple(applied_directive_ids)
        self.rollback_completed = rollback_completed


class ControlActuator:
    """Apply host-local changes; DP has no reference to this actuator."""

    def __init__(self, *, cfw: Any = None, csw: Any = None,
                 production_metrics: Any = None) -> None:
        if cfw is None:
            from ..runtime.adapters.standalone import NullCFWComponentPort
            cfw = NullCFWComponentPort()
        self.cfw = cfw
        self.csw = csw
        self.production_metrics = production_metrics

    def apply(self, prepared: Mapping[str, Any]) -> ControlApplication:
        basis = dict(prepared.get("basis") or {})
        decision_id = prepared.get("decision_id")
        if prepared.get("status") != "prepared" or not decision_id:
            raise ValueError("actuator requires a prepared DP decision")
        state = prepared.get("candidate_behavior_state")
        if not isinstance(state, Mapping):
            raise ValueError("prepared DP decision has no candidate behavior state")
        all_directives = self._directives(prepared.get("directives"))
        cfw_directives = self._directives(prepared.get("cfw_directives"))
        csw_directives = self._directives(prepared.get("csw_directives"))
        if {item["directive_id"] for item in all_directives} != {
                item["directive_id"] for item in cfw_directives + csw_directives}:
            raise ValueError("prepared directive partitions are incomplete")

        cfw_connected = self._cfw_connected()
        if (not cfw_connected and any(item["operation"] == "set_framework"
                                      for item in cfw_directives)):
            raise RuntimeError("DP requested component changes without a CFW authority")
        if csw_directives and self.csw is None:
            raise RuntimeError("DP requested CSW changes without a CSW authority")
        candidate_ids, snapshot = self._initial_csw_snapshot()
        self._validate_basis(snapshot, basis)

        projection = build_cfw_behavior_projection(
            BehaviorState.from_mapping(state),
            host_action_index=int(basis.get(
                "cfw_projection_action_index", basis["host_action_index"])),
        ).to_dict()

        cfw_prepared = None
        cfw_attempted = False
        csw_changes: list[tuple[str, dict[str, Any]]] = []
        checkpoint_created = False
        tick_id = str(prepared.get("tick_id") or "")
        applied: set[str] = set()
        authority_stage: str | None = None
        try:
            if csw_directives:
                authority_stage = "csw"
                checkpoint = getattr(self.csw, "checkpoint_selection", None)
                restore = getattr(self.csw, "restore_selection_checkpoint", None)
                finalize = getattr(self.csw, "finalize_selection_checkpoint", None)
                if not all(callable(method) for method in
                           (checkpoint, restore, finalize)):
                    raise RuntimeError(
                        "CSW StateWork changes require durable selection checkpoints")
                if not tick_id:
                    raise ValueError(
                        "prepared DP decision has no tick ID for CSW rollback")
                checkpoint(tick_id=tick_id,
                           expected_selection_revision=snapshot.revision)
                checkpoint_created = True

            if cfw_connected:
                authority_stage = "cfw"
                cfw_prepared = self.cfw.prepare_behavior_directives(cfw_directives)
                cfw_attempted = True
                returned = tuple(self.cfw.apply_behavior_projection(
                    cfw_prepared, projection))
                expected = {item["directive_id"] for item in cfw_directives}
                if len(returned) != len(set(returned)) or set(returned) != expected:
                    raise RuntimeError("CFW did not confirm the exact prepared directives")
                applied.update(returned)
            else:
                # The trusted host owns model-facing projections when CFW is
                # absent. Component-mode changes still require CFW's runtime.
                host_projectable = tuple(
                    item for item in cfw_directives
                    if item["operation"] in {"set_mode", "set_control"})
                if len(host_projectable) != len(cfw_directives):
                    raise RuntimeError(
                        "DP requested component changes without a CFW authority")
                applied.update(item["directive_id"] for item in host_projectable)

            authority_stage = "csw" if csw_directives else None
            releases = [item for item in csw_directives
                        if item["operation"] == "release_statework"]
            activations = [item for item in csw_directives
                           if item["operation"] == "prefer_statework"]
            if len(releases) + len(activations) != len(csw_directives):
                raise ValueError("DP requested an unsupported CSW operation")
            if len({item["target"] for item in csw_directives}) != len(csw_directives):
                raise ValueError("DP requested duplicate CSW StateWork changes")

            # Activate newly selected workflows before releasing old ones.
            # A newly selected workflow may depend on a workflow being
            # replaced; CSW will retain that prerequisite under dependency
            # ownership when its release is processed afterward.
            for directive in activations + releases:
                statework_id = directive["target"]
                intervention_id = directive.get("intervention_id")
                if not isinstance(intervention_id, str) or not intervention_id:
                    raise ValueError("CSW directive lacks its owning DP intervention")
                if directive["operation"] == "release_statework":
                    if directive.get("value") != "unselected" or statework_id not in \
                            snapshot.active_stateworks:
                        raise RuntimeError("CSW release does not match the current selection")
                    csw_changes.append(("release_statework", {
                        **directive,
                        "expires_at_action": self._directive_expiry(directive),
                    }))
                    release_result = self.csw.deactivate_statework(
                        statework_id,
                        expected_selection_revision=snapshot.revision,
                        candidate_statework_ids=candidate_ids,
                        intervention_id=intervention_id,
                    )
                    if isinstance(release_result, StateWorkReleaseResult):
                        snapshot = release_result.snapshot
                        if release_result.disposition == "owner_mismatch":
                            raise RuntimeError(
                                "CSW rejected StateWork release ownership")
                        if release_result.disposition == "retained_required":
                            if (statework_id not in snapshot.active_stateworks
                                    or not set(release_result.required_by).issubset(
                                        snapshot.active_stateworks)):
                                raise RuntimeError(
                                    "CSW retained StateWork without an active dependent")
                        elif statework_id in snapshot.active_stateworks:
                            raise RuntimeError("CSW did not confirm StateWork release")
                    else:
                        snapshot = release_result
                        if statework_id in snapshot.active_stateworks:
                            raise RuntimeError("CSW did not confirm StateWork release")
                else:
                    eligible = {item.statework_id for item in snapshot.eligible_stateworks}
                    if directive.get("value") != "preferred":
                        raise RuntimeError("CSW activation directive value is invalid")
                    if statework_id not in eligible:
                        raise RuntimeError(
                            "CSW activation target is outside current eligibility "
                            f"(target={statework_id!r}, eligible={sorted(eligible)}, "
                            f"active={sorted(snapshot.active_stateworks)})")
                    expires_at_action = self._directive_expiry(directive)
                    owner_expiry = intervention_expiry(
                        state.get("active_interventions") or (), intervention_id)
                    if owner_expiry != expires_at_action:
                        raise RuntimeError(
                            "CSW activation expiry differs from its owning intervention")
                    csw_changes.append(("prefer_statework", {
                        **directive, "expires_at_action": expires_at_action}))
                    snapshot = self.csw.activate_statework(
                        statework_id,
                        expected_selection_revision=snapshot.revision,
                        candidate_statework_ids=candidate_ids,
                        intervention_id=intervention_id,
                        expires_at_action=expires_at_action,
                    )
                    if statework_id not in snapshot.active_stateworks:
                        raise RuntimeError("CSW did not confirm StateWork activation")
                applied.add(directive["directive_id"])

            if self.csw is not None:
                confirmed = self._snapshot(candidate_ids)
                if confirmed != snapshot:
                    raise RuntimeError("CSW selection changed during DP actuation")
                snapshot = confirmed
            cfw_revision = (self.cfw.lifecycle_revision() if cfw_connected
                            else int(basis["cfw_lifecycle_revision"]))
            if cfw_connected and cfw_revision != int(state["revision"]):
                raise RuntimeError("CFW did not materialize the candidate behavior revision")
            return ControlApplication(tuple(sorted(applied)), cfw_revision, snapshot)
        except Exception as failure:
            if authority_stage == "cfw" and cfw_directives:
                _metric_increment(
                    self.production_metrics, "cfw_directive_rejections")
            elif authority_stage == "csw" and csw_directives:
                _metric_increment(
                    self.production_metrics, "csw_directive_rejections")
            errors = []
            if cfw_attempted:
                try:
                    self.cfw.rollback_behavior_directives(cfw_prepared)
                except Exception as exc:
                    errors.append(f"CFW rollback failed: {exc}")
            if checkpoint_created:
                try:
                    current = self._snapshot(candidate_ids)
                    restored = self.csw.restore_selection_checkpoint(
                        tick_id=tick_id,
                        expected_selection_revision=current.revision)
                    if restored is None:
                        raise RuntimeError("CSW rollback checkpoint disappeared")
                except Exception as exc:
                    errors.append(f"CSW exact selection restore failed: {exc}")
            else:
                for operation, directive in reversed(csw_changes):
                    try:
                        statework_id = directive["target"]
                        intervention_id = directive["intervention_id"]
                        current = self._snapshot(candidate_ids)
                        if operation == "prefer_statework":
                            if statework_id in current.active_stateworks:
                                current = self.csw.deactivate_statework(
                                    statework_id,
                                    expected_selection_revision=current.revision,
                                    candidate_statework_ids=candidate_ids,
                                    intervention_id=intervention_id,
                                )
                            if statework_id in current.active_stateworks:
                                raise RuntimeError("CSW did not confirm activation rollback")
                        else:
                            if statework_id not in current.active_stateworks:
                                current = self.csw.activate_statework(
                                    statework_id,
                                    expected_selection_revision=current.revision,
                                    candidate_statework_ids=candidate_ids,
                                    intervention_id=intervention_id,
                                    expires_at_action=int(directive["expires_at_action"]),
                                )
                            if statework_id not in current.active_stateworks:
                                raise RuntimeError("CSW did not confirm release rollback")
                    except Exception as exc:
                        errors.append(
                            f"CSW rollback failed for {directive.get('target')}: {exc}")
            message = str(failure)
            if errors:
                message += "; " + "; ".join(errors)
            raise ActuationFailure(
                message,
                applied_directive_ids=tuple(sorted(applied)),
                rollback_completed=not errors,
            ) from failure

    def lifecycle_revision(self) -> int:
        return self.cfw.lifecycle_revision() if self._cfw_connected() else 0

    def applied_behavior_projection(self) -> Mapping[str, Any] | None:
        if not self._cfw_connected():
            return None
        return self.cfw.applied_behavior_projection()

    def synchronize_behavior_projection(self, projection: Mapping[str, Any]) -> None:
        if self._cfw_connected():
            self.cfw.synchronize_behavior_projection(projection)

    def restore_behavior_projection(self, projection: Mapping[str, Any], *,
                                    expected_current_revision: int) -> None:
        if self._cfw_connected():
            self.cfw.restore_behavior_projection(
                projection, expected_current_revision=expected_current_revision)

    @staticmethod
    def _directive_expiry(directive: Mapping[str, Any]) -> int:
        expiry = directive.get("expires_at_action")
        if type(expiry) is not int or expiry < 0:
            raise ValueError("CSW directive lacks its absolute lease expiry")
        return expiry

    def _cfw_connected(self) -> bool:
        return self.cfw is not None and bool(self.cfw.available_component_ids())

    def _initial_csw_snapshot(self) -> tuple[tuple[str, ...], CSWBehaviorSnapshot]:
        if self.csw is None:
            from ..runtime.adapters.standalone import NullCSWWorkflowPort
            port = NullCSWWorkflowPort()
            return (), port.behavioral_snapshot()
        snapshot = self.csw.behavioral_snapshot()
        available = getattr(self.csw, "available_statework_ids", None)
        if callable(available):
            candidates = tuple(sorted(set(available()) |
                                      set(snapshot.active_stateworks) |
                                      {item.statework_id
                                       for item in snapshot.eligible_stateworks}))
            snapshot = self._snapshot(candidates)
        else:
            candidates = tuple(sorted(set(snapshot.active_stateworks) |
                                      {item.statework_id
                                       for item in snapshot.eligible_stateworks}))
        return candidates, snapshot

    def _snapshot(self, candidates: Sequence[str]) -> CSWBehaviorSnapshot:
        if self.csw is None:
            from ..runtime.adapters.standalone import NullCSWWorkflowPort
            return NullCSWWorkflowPort().behavioral_snapshot()
        return self.csw.behavioral_snapshot(candidate_statework_ids=candidates)

    @staticmethod
    def _validate_basis(snapshot: CSWBehaviorSnapshot,
                        basis: Mapping[str, Any]) -> None:
        if (snapshot.revision != int(basis.get("csw_selection_revision", -1))
                or snapshot.snapshot_id != basis.get("csw_snapshot_id")
                or list(snapshot.active_stateworks) != list(
                    basis.get("csw_active_stateworks") or ())):
            raise RuntimeError("CSW snapshot no longer matches the DP decision basis")

    @staticmethod
    def _directives(raw: Any) -> tuple[dict[str, Any], ...]:
        if not isinstance(raw, (tuple, list)) or len(raw) > MAX_CONTROL_BATCH_ITEMS:
            raise ValueError("prepared directives are malformed")
        items = []
        for value in raw:
            if not isinstance(value, Mapping):
                raise ValueError("prepared directive must be an object")
            item = dict(value)
            directive_id = item.get("directive_id")
            operation = item.get("operation")
            target = item.get("target")
            if not all(isinstance(part, str) and part for part in
                       (directive_id, operation, target)):
                raise ValueError("prepared directive identity is malformed")
            items.append(item)
        ids = [item["directive_id"] for item in items]
        if len(ids) != len(set(ids)):
            raise ValueError("prepared directive IDs must be unique")
        return tuple(items)


__all__ = ["ActuationFailure", "ControlActuator", "ControlApplication"]
