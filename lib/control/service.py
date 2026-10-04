"""Stable facade for host-owned DigitalPsychology control operations."""
from __future__ import annotations

from typing import Any

from ..runtime.store import BehaviorStateStore
from .engine import ControlDecisionEngine
from .session_registry import SessionRegistryMethods
from .transaction_service import ControlTransactionMethods
from ..telemetry.trial_report import TrialReportMethods


class ControlPlaneService(SessionRegistryMethods, ControlTransactionMethods,
                          TrialReportMethods):
    """Coordinate the focused session, transaction, and reporting services."""

    def __init__(self, *, decision_service: Any, state_store: BehaviorStateStore,
                 system_one_frame_factory: Any = None) -> None:
        self.decision_service = decision_service
        self.state_store = state_store
        if system_one_frame_factory is None:
            factory_provider = getattr(
                decision_service, "control_system_one_frame_factory", None)
            if callable(factory_provider):
                system_one_frame_factory = factory_provider()
        self.engine = ControlDecisionEngine(
            decision_service=decision_service,
            system_one_frame_factory=system_one_frame_factory)

    def close(self) -> None:
        close = getattr(self.decision_service, "close", None)
        if callable(close):
            close()


__all__ = ["ControlPlaneService"]
