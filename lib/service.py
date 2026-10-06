"""DigitalPsychology application services, independent of MCP transport."""
from __future__ import annotations

import os
import json
from pathlib import Path
from typing import Any, Mapping

from .feedback_loop import SQLiteSink, trusted_state_root
from .fast_loop import BehavioralController, FastLoopInput
from .control.policy_selector import BehaviorPolicyPrior
from .outcomes import (InterventionEvaluationFrame, InterventionEvaluator,
                       InterventionEventContext, OutcomeEvidenceAuthority)
from .behavior_state import BehaviorIntervention, BehaviorDirective, InterventionOutcome
from .routing_profiles import SharingDecisionAuthority
from .system_one_contracts import SystemOneFrame, SystemOneIdentity
from .system_one_service import SystemOneService
from .security.attestation import EventAttestationAuthority
from .telemetry.ingestion import BehaviorEventIngestor
from .policy_priors.prior_store import BehaviorPolicyPriorStore
from .interventions.event_service import InterventionEventService
from .experiments.service import RegisteredExperimentService


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


class DigitalPsychologyService:
    """Small service object kept separate from MCP transport for testing."""

    def __init__(self, *, event_db: Path | None = None,
                 profile_path: Path | None = None,
                 trusted_producers: Mapping[str, Mapping[str, Any]] | None = None,
                 max_pending_events_per_origin: int = 256,
                 max_ingest_seconds_per_origin: float = 0.25,
                 busy_timeout_ms: int = 250,
                 per_origin_write_limit: int = 100_000,
                 backpressure_recovery_seconds: float = 5.0,
                 sharing_authority: SharingDecisionAuthority | None = None,
                 system_one_service: SystemOneService | None = None,
                 behavioral_controller: BehavioralController | None = None) -> None:
        root = trusted_state_root()
        self.event_db = event_db or Path(
            os.environ.get("DIGITALPSYCHOLOGY_EVENT_DB", root / "events.sqlite3"))
        self.profile_path = profile_path
        self._sharing_authority = sharing_authority
        self._system_one_service = system_one_service
        self._behavioral_controller = behavioral_controller or BehavioralController()
        self._intervention_evaluator = InterventionEvaluator()
        if not 10 <= int(busy_timeout_ms) <= 5000 or int(per_origin_write_limit) < 1:
            raise ValueError("per-origin persistence bounds must be positive")
        self._sink = SQLiteSink(
            self.event_db, busy_timeout_ms=busy_timeout_ms,
            per_origin_daily_write_limit=per_origin_write_limit)
        attestation = EventAttestationAuthority(
            trusted_producers=trusted_producers,
            legacy_secret=os.environ.get("DIGITALPSYCHOLOGY_INGESTION_SECRET", "").encode())
        self._event_ingestor = BehaviorEventIngestor(
            sink=self._sink, attestation=attestation,
            max_pending_events_per_origin=max_pending_events_per_origin,
            max_ingest_seconds_per_origin=max_ingest_seconds_per_origin,
            backpressure_recovery_seconds=backpressure_recovery_seconds)
        self._policy_priors = BehaviorPolicyPriorStore(
            profile_path=self.profile_path, sharing_authority=self._sharing_authority)
        self._intervention_events = InterventionEventService(self._sink)
        self._experiments = RegisteredExperimentService(
            sink=self._sink, profile_path=self.profile_path,
            promotion_secret=os.environ.get("DIGITALPSYCHOLOGY_PROMOTION_SECRET", "").encode())

    def close(self) -> None:
        self._sink.close()
        if self._system_one_service is not None:
            self._system_one_service.close()

    def capability(self):
        """Host-only System-1 capability handshake; not exposed through MCP."""
        if self._system_one_service is None:
            raise RuntimeError("System-1 service is not configured")
        return self._system_one_service.capability()

    def judge(self, frame: SystemOneFrame, *, mode: str | None = None):
        """Host-only behavioral judgment path; never registered as an agent tool."""
        if self._system_one_service is None:
            raise RuntimeError("System-1 service is not configured")
        return self._system_one_service.judge(frame, mode=mode)

    def control_system_one_frame_factory(self):
        """Build frames only when an enabled host-only System-1 backend exists."""
        if (self._system_one_service is None
                or self._system_one_service.mode == "disabled"):
            return None
        from .control.system_one import create_system_one_frame_factory
        return create_system_one_frame_factory(self._system_one_service)

    def reset_identity(self, identity: SystemOneIdentity) -> None:
        if self._system_one_service is None:
            raise RuntimeError("System-1 service is not configured")
        self._system_one_service.reset_identity(identity)

    def resync(self, frame: SystemOneFrame) -> None:
        if self._system_one_service is None:
            raise RuntimeError("System-1 service is not configured")
        self._system_one_service.resync(frame)

    def decide_behavior(self, context: FastLoopInput, *,
                        judgment: Any = None) -> dict[str, Any]:
        """Host-only fast-loop decision; it is deliberately not an agent tool.

        The runtime host supplies its current DP state, CFW signals, bounded
        CSW snapshot, and optional DP System-1 result. It must check the
        expected revision, enforce hard policy floors, persist the returned
        state, and ask CSW to validate any StateWork preference.
        """
        # A raw/shadow System-1 judgment is diagnostic only. Behavioral policy
        # consumes semantic advice only after the DP service marks the complete
        # requested question set advisory-ready.
        if hasattr(judgment, "status"):
            judgment = judgment.judgment if judgment.status == "ok" else None
        priors = self.control_behavior_policy_priors(context.policy_context)
        decision = self._behavioral_controller.decide(
            context, judgment=judgment, policy_priors=priors)
        return decision.to_dict()

    def control_behavior_policy_priors(
            self, policy_context: Mapping[str, Any]
            ) -> tuple[BehaviorPolicyPrior, ...]:
        """Read exact-scope priors from active validated profiles."""
        return self._policy_priors.policy_priors(policy_context)

    def assess_intervention_outcome(
            self, intervention: BehaviorIntervention,
            frame: InterventionEvaluationFrame, *,
            evidence_authority: OutcomeEvidenceAuthority) -> dict[str, Any]:
        """Host-only independent credit assignment; never an agent MCP tool.

        The supplied authority must resolve refs from CSW and host validators.
        Agent/model statements are not valid outcome evidence.
        """
        return self._intervention_evaluator.assess(
            intervention, frame, authority=evidence_authority).to_dict()

    def record_intervention_application(
            self, intervention: BehaviorIntervention,
            directives: tuple[BehaviorDirective, ...],
            applied_directive_ids: tuple[str, ...], *,
            event_context: InterventionEventContext) -> str:
        """Persist a host receipt after the runtime actually applies directives."""
        return self._intervention_events.record_application(
            intervention, directives, applied_directive_ids, event_context=event_context)

    def prepare_intervention_application(
            self, intervention: BehaviorIntervention,
            directives: tuple[BehaviorDirective, ...],
            applied_directive_ids: tuple[str, ...], *,
            event_context: InterventionEventContext) -> dict[str, Any]:
        """Build the authenticated receipt for atomic insertion in the host outbox."""
        return self._intervention_events.prepare_application(
            intervention, directives, applied_directive_ids, event_context=event_context)

    def record_intervention_outcome(
            self, outcome: InterventionOutcome, *,
            event_context: InterventionEventContext) -> str:
        """Durably join independent outcome evidence to the applied intervention."""
        return self._intervention_events.record_outcome(
            outcome, event_context=event_context)

    def prepare_intervention_outcome(
            self, outcome: InterventionOutcome, *,
            event_context: InterventionEventContext) -> dict[str, Any]:
        """Build an assessed outcome for atomic insertion in the host outbox."""
        return self._intervention_events.prepare_outcome(
            outcome, event_context=event_context)

    def dispatch_runtime_event(self, value: Mapping[str, Any]) -> str:
        """Idempotently deliver a trusted host event from the runtime outbox."""
        return self._intervention_events.dispatch(value)

    def _record_intervention_event(
            self, event_context: InterventionEventContext, *, category: str,
            event_type: str, scope: str, evidence_refs: tuple[str, ...],
            payload: Mapping[str, Any]) -> str:
        return self._intervention_events.record_event(
            event_context, category=category, event_type=event_type, scope=scope,
            evidence_refs=evidence_refs, payload=payload)

    @staticmethod
    def _build_intervention_event(
            event_context: InterventionEventContext, *, category: str,
            event_type: str, scope: str, evidence_refs: tuple[str, ...],
            payload: Mapping[str, Any]):
        return InterventionEventService._build_event(
            event_context, category=category, event_type=event_type, scope=scope,
            evidence_refs=evidence_refs, payload=payload)

    def _producer_key(self, key_id: str) -> bytes:
        return self._event_ingestor.attestation._producer_key(key_id)

    def _attest_event(self, data: Mapping[str, Any],
                      attestation: Optional[Mapping[str, Any]]) -> bool:
        return self._event_ingestor.attestation.attest(data, attestation)

    def ingest_behavior_event(self, event: Mapping[str, Any]) -> dict[str, Any]:
        return self._event_ingestor.ingest(event)

    @staticmethod
    def _eligible_route_keys(routes: list[Any]) -> set[tuple[str, str]]:
        return BehaviorPolicyPriorStore.eligible_route_keys(routes)

    def _load_profiles(self) -> tuple[list[dict[str, Any]], str]:
        return self._policy_priors.load_profiles()

    def behavioral_advice(self, *, context: Mapping[str, Any],
                          eligible_routes: list[Any],
                          static_policy: Mapping[str, Any]) -> dict[str, Any]:
        """Return bounded advice only for host-declared eligible routes."""
        return self._policy_priors.behavioral_advice(
            context=context, eligible_routes=eligible_routes, static_policy=static_policy)

    def _run_slow_loop(self, events: list[Any], task_context: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
        """Internal authenticated-cycle primitive; never exposed as an MCP tool."""
        return self._experiments.run_slow_loop(events, task_context)

    def _load_persisted_events(self, event_ids: list[Any], *,
                              namespace_id: str,
                              application_instance_id: str) -> list[Any]:
        """Load authenticated events by complete, immutable origin."""
        return self._experiments.load_persisted_events(
            event_ids, namespace_id=namespace_id,
            application_instance_id=application_instance_id)

    @staticmethod
    def _load_experiment_record(registry: Any, *, experiment_id: str,
                                namespace_id: str, application_instance_id: str) -> dict[str, Any]:
        """Select one immutable registration from exact origin."""
        return RegisteredExperimentService.load_experiment_record(
            registry, experiment_id=experiment_id, namespace_id=namespace_id,
            application_instance_id=application_instance_id)

    def run_registered_slow_loop(self, experiment_id: str,
                                 authorization: Mapping[str, Any]) -> dict[str, Any]:
        """Run a protected experiment record; promotion requires host authorization."""
        return self._experiments.run_registered(
            experiment_id, authorization, slow_loop=self._run_slow_loop)

    def purge_expired_events(self, *, retention_days: int) -> dict[str, Any]:
        """Host-only retention operation; deliberately not an MCP tool."""
        return self._sink.purge_expired(retention_days=retention_days)

    def validate_routing_profile(self, profile: Mapping[str, Any]) -> dict[str, Any]:
        return self._policy_priors.validate_profile(profile)

    def _profile_revision(self) -> int | None:
        return self._policy_priors.profile_revision()
