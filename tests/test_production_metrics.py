from __future__ import annotations

from pathlib import Path

import pytest

from lib.telemetry.production_metrics import ProductionMetrics


def test_production_metrics_persist_allowlisted_counters_and_timers(tmp_path: Path):
    path = tmp_path / "private" / "metrics.sqlite3"
    metrics = ProductionMetrics(path)

    metrics.increment("control_ticks_prepared", 2)
    metrics.observe_duration("control_tick_prepare_duration_ms", 12.5)
    reopened = ProductionMetrics(path)
    snapshot = reopened.snapshot()

    assert snapshot["counters"]["control_ticks_prepared"] == 2
    assert snapshot["timers"]["control_tick_prepare_duration_ms"] == {
        "count": 1, "total_ms": 12.5, "max_ms": 12.5,
    }
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700


def test_production_metric_periods_are_idempotent_and_record_duration(tmp_path: Path):
    metrics = ProductionMetrics(tmp_path / "metrics.sqlite3")

    assert metrics.begin_period("dp_outage") is True
    assert metrics.begin_period("dp_outage") is False
    assert metrics.period_active("dp_outage") is True
    assert metrics.end_period("dp_outage") is True
    assert metrics.end_period("dp_outage") is False

    snapshot = metrics.snapshot()
    assert snapshot["counters"]["dp_outages"] == 1
    assert snapshot["timers"]["dp_outage_duration_ms"]["count"] == 1
    assert snapshot["active_periods"] == {}


def test_production_metrics_reject_unknown_names_and_invalid_values(tmp_path: Path):
    metrics = ProductionMetrics(tmp_path / "metrics.sqlite3")

    with pytest.raises(ValueError):
        metrics.increment("arbitrary_user_key")
    with pytest.raises(ValueError):
        metrics.increment("control_ticks_prepared", -1)
    with pytest.raises(ValueError):
        metrics.observe_duration("control_tick_prepare_duration_ms", float("nan"))
    with pytest.raises(ValueError):
        metrics.begin_period("unknown")
