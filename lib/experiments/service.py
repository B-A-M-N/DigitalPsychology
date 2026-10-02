"""Execute immutable, host-authorized routing experiment registrations."""
from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
from typing import Any, Callable, Mapping

from ..feedback_loop import Event, EventFactory, SQLiteSink, trusted_state_root
from ..routing_lifecycle import RoutingLifecycleStore
from ..slow_loop import BoundedLearningController


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


class RegisteredExperimentService:
    """Own trusted experiment loading, authorization, and slow-loop execution."""

    def __init__(self, *, sink: SQLiteSink, profile_path: Path | None,
                 promotion_secret: bytes) -> None:
        self._sink = sink
        self._profile_path = profile_path
        self._promotion_secret = promotion_secret

    def run_slow_loop(self, events: list[Any],
                      task_context: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
        profile_path = self._profile_path or (trusted_state_root() / "active-routing-profile.json")
        lifecycle = RoutingLifecycleStore(profile_path.parent / "routing-lifecycle.json")
        parsed_events = [EventFactory.from_dict(dict(event)) if isinstance(event, Mapping) else event
                         for event in events]
        report = BoundedLearningController(lifecycle=lifecycle).run(parsed_events, task_context)
        return {
            "status": report.status,
            "promoted": report.promoted,
            "candidate_count": len(report.candidates),
            "rejected": report.rejected,
            "deployment_exposures": report.deployment_exposures,
            "observations_completed": report.observations_completed,
            "intervention_effectiveness": report.intervention_effectiveness,
            "behavior_policy_priors": report.behavior_policy_priors,
            "regressions": report.regressions,
            "rejection_reasons": report.rejection_reasons,
            "events": report.events,
        }

    def load_persisted_events(self, event_ids: list[Any], *, namespace_id: str,
                              application_instance_id: str) -> list[Any]:
        return self._sink.load_authenticated_events(
            event_ids, namespace_id=namespace_id,
            application_instance_id=application_instance_id)

    @staticmethod
    def load_experiment_record(registry: Any, *, experiment_id: str,
                               namespace_id: str,
                               application_instance_id: str) -> dict[str, Any]:
        values = (list(registry.values()) if isinstance(registry, Mapping)
                  else registry if isinstance(registry, list) else [])
        matches = [item for item in values if isinstance(item, Mapping)
                   and item.get("experiment_id") == experiment_id
                   and item.get("namespace_id") == namespace_id
                   and item.get("application_instance_id") == application_instance_id]
        if len(matches) != 1:
            raise ValueError("experiment registration must be unique for its exact origin")
        record = dict(matches[0])
        registration_revision = record.get("registration_revision")
        if not isinstance(registration_revision, str) or not registration_revision:
            raise ValueError("experiment registration requires an immutable revision")
        body = dict(record)
        supplied = body.pop("registration_hash", None)
        expected = hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()
        if supplied != expected:
            raise ValueError("experiment registration hash is invalid")
        return record

    def run_registered(self, experiment_id: str, authorization: Mapping[str, Any], *,
                       slow_loop: Callable[[list[Any], Mapping[str, Mapping[str, Any]]],
                                           dict[str, Any]] | None = None) -> dict[str, Any]:
        root = trusted_state_root()
        registry_path = root / "experiments.json"
        if not registry_path.exists():
            raise ValueError("experiment registry is unavailable")
        try:
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("experiment registry is unreadable") from exc
        namespace_id = str(authorization.get("namespace_id") or "")
        application_instance_id = str(authorization.get("application_instance_id") or "")
        if not namespace_id or not application_instance_id:
            raise ValueError("experiment authorization requires its exact origin")
        record = self.load_experiment_record(
            registry, experiment_id=experiment_id,
            namespace_id=namespace_id,
            application_instance_id=application_instance_id)
        if (authorization.get("registration_revision") != record.get("registration_revision")
                or authorization.get("registration_hash") != record.get("registration_hash")):
            raise ValueError(
                "experiment promotion authorization is not bound to the immutable registration")
        expected = str(record.get("promotion_authorization_id") or "")
        if not expected or authorization.get("authorization_id") != expected:
            raise ValueError("experiment promotion authorization is required")
        if not self._promotion_secret:
            raise ValueError("experiment promotion requires a separate promotion secret")
        signature = str(authorization.get("signature") or "")
        body = {key: value for key, value in authorization.items() if key != "signature"}
        expected_signature = hmac.new(
            self._promotion_secret, _canonical(body).encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected_signature):
            raise ValueError("experiment promotion authorization is invalid")
        event_ids = record.get("event_ids")
        task_context = record.get("task_context")
        if not isinstance(event_ids, list) or not isinstance(task_context, Mapping):
            raise ValueError("experiment record lacks authenticated event IDs or context")
        events = self.load_persisted_events(
            event_ids, namespace_id=str(record.get("namespace_id") or ""),
            application_instance_id=str(record.get("application_instance_id") or ""))
        runner = slow_loop or self.run_slow_loop
        return runner(events, task_context)
