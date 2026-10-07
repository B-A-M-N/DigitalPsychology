"""Reversible application of prepared triad directives through authority ports."""
from __future__ import annotations

from dataclasses import dataclass
import logging
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from ...errors import ActuationFailure
from .dependency_closure import expected_csw_active_stateworks

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
    csw_snapshot: Any


@dataclass(frozen=True)
class ControlReconciliation:
    """Cross-authority result for recovering one prepared control decision."""

    applied: bool
    applied_directive_ids: tuple[str, ...]
    cfw_lifecycle_revision: int
    csw_snapshot: Any
    rollback_errors: tuple[str, ...] = ()

    @property
    def rollback_completed(self) -> bool:
        return not self.rollback_errors


class ControlActuator:
    """Apply prepared behavior directives through connected authority ports."""

    def __init__(self, *, cfw: Any, csw: Any,
                 cfw_projection: Callable[[Mapping[str, Any], Mapping[str, Any]],
                                          Mapping[str, Any]],
                 empty_csw_snapshot: Callable[[], Any],
                 csw_snapshot_provider: Callable[[], tuple[Any, Sequence[str]]] | None = None,
                 intervention_expiry: Callable[[Sequence[Any], str], int],
                 max_directives: int = 64,
                 production_metrics: Any = None) -> None:
        if not callable(cfw_projection) or not callable(empty_csw_snapshot):
            raise TypeError("runtime actuation requires projection and empty-snapshot adapters")
        if not callable(intervention_expiry):
            raise TypeError("runtime actuation requires an intervention-expiry adapter")
        if csw_snapshot_provider is not None and not callable(csw_snapshot_provider):
            raise TypeError("runtime actuation requires a callable CSW snapshot provider")
        if type(max_directives) is not int or not 1 <= max_directives <= 128:
            raise ValueError("runtime directive limit must be between one and 128")
        self.cfw = cfw
        self.csw = csw
        self.cfw_projection = cfw_projection
        self.empty_csw_snapshot = empty_csw_snapshot
        self.csw_snapshot_provider = csw_snapshot_provider
        self.intervention_expiry = intervention_expiry
        self.max_directives = max_directives
        self.production_metrics = production_metrics

    def apply(self, prepared: Mapping[str, Any]) -> ControlApplication:
        basis = dict(prepared.get("basis") or {})
        decision_id = prepared.get("decision_id")
        if prepared.get("status") != "prepared" or not decision_id:
            raise ValueError("actuator requires a prepared behavior decision")
        state = prepared.get("candidate_behavior_state")
        if not isinstance(state, Mapping):
            raise ValueError("prepared behavior decision has no candidate state")
        all_directives = self._directives(prepared.get("directives"))
        cfw_directives = self._directives(prepared.get("cfw_directives"))
        csw_directives = self._directives(prepared.get("csw_directives"))
        if {item["directive_id"] for item in all_directives} != {
                item["directive_id"] for item in cfw_directives + csw_directives}:
            raise ValueError("prepared directive partitions are incomplete")

        cfw_connected = self._cfw_connected()
        if (not cfw_connected and any(item["operation"] == "set_framework"
                                      for item in cfw_directives)):
            raise RuntimeError(
                "behavior decision requests component changes without a CFW authority")
        if csw_directives and self.csw is None:
            raise RuntimeError("behavior decision requests workflow changes without a CSW authority")
        candidate_ids, snapshot = self._initial_csw_snapshot()
        self._validate_basis(snapshot, basis)

        projection = self.cfw_projection(state, basis)
        if not isinstance(projection, Mapping):
            raise TypeError("CFW projection adapter must return a mapping")

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
                        "prepared behavior decision has no tick ID for CSW rollback")
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
                        "behavior decision requests component changes without a CFW authority")
                applied.update(item["directive_id"] for item in host_projectable)

            authority_stage = "csw" if csw_directives else None
            releases = [item for item in csw_directives
                        if item["operation"] == "release_statework"]
            activations = [item for item in csw_directives
                           if item["operation"] == "prefer_statework"]
            if len(releases) + len(activations) != len(csw_directives):
                raise ValueError("behavior decision contains an unsupported CSW operation")
            if len({item["target"] for item in csw_directives}) != len(csw_directives):
                raise ValueError("behavior decision requested duplicate workflow changes")

            # Activate newly selected workflows before releasing old ones.
            # A newly selected workflow may depend on a workflow being
            # replaced; CSW will retain that prerequisite under dependency
            # ownership when its release is processed afterward.
            for directive in activations + releases:
                statework_id = directive["target"]
                intervention_id = directive.get("intervention_id")
                if not isinstance(intervention_id, str) or not intervention_id:
                    raise ValueError("workflow directive lacks its owning intervention")
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
                    release_snapshot = getattr(release_result, "snapshot", None)
                    release_disposition = getattr(release_result, "disposition", None)
                    release_required_by = getattr(release_result, "required_by", ())
                    if release_snapshot is not None and release_disposition is not None:
                        snapshot = release_snapshot
                        if release_disposition == "owner_mismatch":
                            raise RuntimeError(
                                "CSW rejected StateWork release ownership")
                        if release_disposition == "retained_required":
                            if (statework_id not in snapshot.active_stateworks
                                    or not set(release_required_by).issubset(
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
                    owner_expiry = self.intervention_expiry(
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
                    raise RuntimeError("CSW selection changed during runtime actuation")
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

    def reconcile_prepared(
            self, prepared: Mapping[str, Any], *,
            committed_behavior_state: Mapping[str, Any],
            committed_projection_basis: Mapping[str, Any]
            ) -> ControlReconciliation:
        """Classify and recover a prepared decision after a host restart.

        This method owns only CFW/CSW application state. The caller remains
        responsible for committing or aborting the DP control decision.
        """
        basis = prepared.get("basis")
        candidate = prepared.get("candidate_behavior_state")
        if (prepared.get("status") != "prepared"
                or not isinstance(basis, Mapping)
                or not isinstance(candidate, Mapping)
                or not isinstance(committed_behavior_state, Mapping)
                or not isinstance(committed_projection_basis, Mapping)):
            raise ValueError("triad recovery requires a complete prepared decision")

        all_directives = self._directives(prepared.get("directives"))
        cfw_directives = self._directives(prepared.get("cfw_directives"))
        csw_directives = self._directives(prepared.get("csw_directives"))
        if {item["directive_id"] for item in all_directives} != {
                item["directive_id"] for item in cfw_directives + csw_directives}:
            raise ValueError("prepared directive partitions are incomplete")

        candidate_ids, snapshot = self._initial_csw_snapshot()
        expected_active = expected_csw_active_stateworks(
            basis, csw_directives, snapshot.dependency_retention)
        expected_cfw = self.cfw_projection(candidate, basis)
        if not isinstance(expected_cfw, Mapping):
            raise TypeError("CFW projection adapter must return a mapping")
        cfw_matches = not self._cfw_connected()
        if not cfw_matches:
            actual_cfw = self.applied_behavior_projection()
            cfw_matches = (
                isinstance(actual_cfw, Mapping)
                and dict(actual_cfw) == dict(expected_cfw)
                and self.lifecycle_revision() == int(candidate["revision"]))

        csw_matches = set(snapshot.active_stateworks) == expected_active
        owns_activation = getattr(self.csw, "activation_matches", None)
        for directive in csw_directives:
            if (directive["operation"] == "prefer_statework"
                    and directive["target"] in snapshot.active_stateworks
                    and (not callable(owns_activation)
                         or not owns_activation(
                             directive["target"],
                             intervention_id=directive.get("intervention_id")))):
                csw_matches = False

        if cfw_matches and csw_matches:
            return ControlReconciliation(
                applied=True,
                applied_directive_ids=tuple(
                    item["directive_id"] for item in all_directives),
                cfw_lifecycle_revision=self.lifecycle_revision(),
                csw_snapshot=snapshot,
            )

        rollback_errors: list[str] = []
        restored_checkpoint = None
        restore_checkpoint = getattr(self.csw, "restore_selection_checkpoint", None)
        if csw_directives and callable(restore_checkpoint):
            try:
                restored_checkpoint = restore_checkpoint(
                    tick_id=str(prepared.get("tick_id") or ""),
                    expected_selection_revision=snapshot.revision)
                if restored_checkpoint is not None:
                    snapshot = restored_checkpoint
            except Exception as exc:
                rollback_errors.append(f"CSW exact selection recovery failed: {exc}")

        if restored_checkpoint is None:
            desired_active = set(basis.get("csw_active_stateworks") or ())
            activations = {
                item["target"]: item for item in csw_directives
                if item["operation"] == "prefer_statework"
            }
            releases = {
                item["target"]: item for item in csw_directives
                if item["operation"] == "release_statework"
            }
            for statework_id in sorted(set(snapshot.active_stateworks) - desired_active):
                directive = activations.get(statework_id)
                if directive is None:
                    rollback_errors.append(
                        f"unrequested CSW activation {statework_id} preserved during recovery")
                    continue
                if (not callable(owns_activation) or not owns_activation(
                        statework_id,
                        intervention_id=directive.get("intervention_id"))):
                    rollback_errors.append(
                        f"CSW activation {statework_id} has another owner and was preserved")
                    continue
                try:
                    self.csw.deactivate_statework(
                        statework_id,
                        expected_selection_revision=snapshot.revision,
                        candidate_statework_ids=candidate_ids,
                        intervention_id=directive["intervention_id"],
                    )
                    snapshot = self._snapshot(candidate_ids)
                except Exception as exc:
                    rollback_errors.append(f"CSW release recovery failed: {exc}")
            for statework_id in sorted(desired_active - set(snapshot.active_stateworks)):
                directive = releases.get(statework_id)
                if directive is None:
                    rollback_errors.append(
                        f"unrequested CSW release {statework_id} was not reversed")
                    continue
                try:
                    self.csw.activate_statework(
                        statework_id,
                        expected_selection_revision=snapshot.revision,
                        candidate_statework_ids=candidate_ids,
                        intervention_id=directive["intervention_id"],
                        expires_at_action=self._directive_expiry(directive),
                    )
                    snapshot = self._snapshot(candidate_ids)
                except Exception as exc:
                    rollback_errors.append(f"CSW activation recovery failed: {exc}")

        if self._cfw_connected():
            try:
                committed_projection = self.cfw_projection(
                    committed_behavior_state, committed_projection_basis)
                if not isinstance(committed_projection, Mapping):
                    raise TypeError("CFW projection adapter must return a mapping")
                self.restore_behavior_projection(
                    committed_projection,
                    expected_current_revision=self.lifecycle_revision(),
                )
            except Exception as exc:
                rollback_errors.append(f"CFW state recovery failed: {exc}")

        try:
            snapshot = self._snapshot(candidate_ids)
        except Exception as exc:
            rollback_errors.append(f"CSW recovery snapshot failed: {exc}")
        return ControlReconciliation(
            applied=False,
            applied_directive_ids=(),
            cfw_lifecycle_revision=self.lifecycle_revision(),
            csw_snapshot=snapshot,
            rollback_errors=tuple(rollback_errors),
        )

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

    def reconcile_behavior_projection(self, projection: Mapping[str, Any]) -> bool:
        """Synchronize CFW to a host-supplied committed projection if needed."""
        if not isinstance(projection, Mapping):
            raise TypeError("CFW reconciliation projection must be a mapping")
        if not self._cfw_connected():
            return False
        actual = self.applied_behavior_projection()
        if isinstance(actual, Mapping) and dict(actual) == dict(projection):
            return False
        self.synchronize_behavior_projection(projection)
        return True

    def restore_selection_checkpoint(self, *, tick_id: str,
                                     expected_selection_revision: int) -> Any:
        """Restore a CSW selection checkpoint when the authority supports it."""
        restore = getattr(self.csw, "restore_selection_checkpoint", None)
        if not callable(restore):
            return None
        if self.csw_snapshot_provider is not None:
            current, _ = self.csw_snapshot_provider()
            if current.revision != expected_selection_revision:
                raise RuntimeError("CSW selection changed before checkpoint restoration")
        return restore(tick_id=tick_id,
                       expected_selection_revision=expected_selection_revision)

    def finalize_selection_checkpoint(self, *, tick_id: str) -> None:
        """Finalize a CSW selection checkpoint after DP commits its decision."""
        finalize = getattr(self.csw, "finalize_selection_checkpoint", None)
        if callable(finalize):
            if self.csw_snapshot_provider is not None:
                self.csw_snapshot_provider()
            finalize(tick_id=tick_id)

    @staticmethod
    def _directive_expiry(directive: Mapping[str, Any]) -> int:
        expiry = directive.get("expires_at_action")
        if type(expiry) is not int or expiry < 0:
            raise ValueError("CSW directive lacks its absolute lease expiry")
        return expiry

    def _cfw_connected(self) -> bool:
        return self.cfw is not None and bool(self.cfw.available_component_ids())

    def _initial_csw_snapshot(self) -> tuple[tuple[str, ...], Any]:
        if self.csw_snapshot_provider is not None:
            snapshot, candidates = self.csw_snapshot_provider()
            return tuple(candidates), snapshot
        if self.csw is None:
            return (), self.empty_csw_snapshot()
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

    def _snapshot(self, candidates: Sequence[str]) -> Any:
        if self.csw_snapshot_provider is not None:
            snapshot, current_candidates = self.csw_snapshot_provider()
            if tuple(current_candidates) != tuple(candidates):
                raise RuntimeError("CSW candidate set changed during runtime actuation")
            return snapshot
        if self.csw is None:
            return self.empty_csw_snapshot()
        return self.csw.behavioral_snapshot(candidate_statework_ids=candidates)

    @staticmethod
    def _validate_basis(snapshot: Any,
                        basis: Mapping[str, Any]) -> None:
        if (snapshot.revision != int(basis.get("csw_selection_revision", -1))
                or snapshot.snapshot_id != basis.get("csw_snapshot_id")
                or list(snapshot.active_stateworks) != list(
                    basis.get("csw_active_stateworks") or ())):
            raise RuntimeError("CSW snapshot no longer matches the runtime decision basis")

    def _directives(self, raw: Any) -> tuple[dict[str, Any], ...]:
        if not isinstance(raw, (tuple, list)) or len(raw) > self.max_directives:
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


__all__ = ["ActuationFailure", "ControlActuator", "ControlApplication",
           "ControlReconciliation"]
