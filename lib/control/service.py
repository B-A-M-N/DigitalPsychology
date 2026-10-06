"""Stable facade for host-owned DigitalPsychology control operations."""
from __future__ import annotations

from typing import Any

from ..runtime.store import BehaviorStateStore
from .engine import ControlDecisionEngine
from .session_registry import SessionRegistryMethods
from .transaction_service import ControlTransactionMethods
from ..telemetry.trial_report import TrialReportMethods
from ..telemetry.production_metrics import (ProductionMetrics,
                                            default_production_metrics_path)


class ControlPlaneService(SessionRegistryMethods, ControlTransactionMethods,
                          TrialReportMethods):
    """Coordinate the focused session, transaction, and reporting services."""

    def __init__(self, *, decision_service: Any, state_store: BehaviorStateStore,
                 system_one_frame_factory: Any = None,
                 production_metrics: ProductionMetrics | None = None) -> None:
        self.decision_service = decision_service
        self.state_store = state_store
        if production_metrics is None:
            path = default_production_metrics_path(
                fallback=self.state_store.path.with_name("production-metrics.sqlite3"))
            production_metrics = ProductionMetrics(path)
        self.production_metrics = production_metrics
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

    def production_metrics_snapshot(self) -> dict[str, Any]:
        """Return bounded host/operator production counters."""
        return self.production_metrics.snapshot()


__all__ = ["ControlPlaneService"]
