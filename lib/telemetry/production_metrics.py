"""Bounded, local production counters shared by the host and DP process."""
from __future__ import annotations

from contextlib import contextmanager
import math
import os
from pathlib import Path
import sqlite3
import time
from typing import Any


COUNTERS = (
    "control_ticks_prepared",
    "control_ticks_committed",
    "control_ticks_aborted",
    "reconciliations",
    "degraded_periods",
    "dp_outages",
    "lease_expirations_during_outage",
    "cfw_directive_rejections",
    "csw_directive_rejections",
    "laya_query_attempts",
    "laya_advisory_results",
    "laya_fallbacks",
    "attention_rotations",
    "starvation_prevention_events",
    "criterion_local_recoveries",
    "global_blocked_transitions",
)

TIMERS = (
    "control_tick_prepare_duration_ms",
    "control_tick_commit_duration_ms",
    "reconciliation_duration_ms",
    "degraded_period_duration_ms",
    "dp_outage_duration_ms",
)

_PERIODS = {
    "degraded": ("degraded_periods", "degraded_period_duration_ms"),
    "dp_outage": ("dp_outages", "dp_outage_duration_ms"),
}


def default_production_metrics_path(*, environment: dict[str, str] | None = None,
                                    fallback: Path | None = None) -> Path:
    """Resolve the shared host/DP path using the subprocess environment contract."""
    env = os.environ if environment is None else environment
    configured = env.get("DIGITALPSYCHOLOGY_METRICS_DB")
    if configured:
        return Path(configured)
    behavior_state = env.get("DIGITALPSYCHOLOGY_BEHAVIOR_STATE_DB")
    if behavior_state:
        return Path(behavior_state).with_name("production-metrics.sqlite3")
    state_root = env.get("DIGITALPSYCHOLOGY_STATE_ROOT")
    if state_root:
        return Path(state_root) / "production-metrics.sqlite3"
    xdg_state = env.get("XDG_STATE_HOME")
    if xdg_state:
        return Path(xdg_state) / "digitalpsychology" / "production-metrics.sqlite3"
    if fallback is not None:
        return Path(fallback)
    return (Path.home() / ".local" / "state" / "digitalpsychology"
            / "production-metrics.sqlite3")


class ProductionMetrics:
    """Persist allowlisted counters and duration aggregates in a private SQLite DB."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        try:
            self.path.touch(mode=0o600, exist_ok=True)
            self.path.chmod(0o600)
        except OSError as exc:
            raise RuntimeError("could not secure the production metrics store") from exc
        with self._connect() as database:
            database.executescript("""
                CREATE TABLE IF NOT EXISTS production_metric_counters (
                    name TEXT PRIMARY KEY,
                    value INTEGER NOT NULL CHECK(value >= 0)
                );
                CREATE TABLE IF NOT EXISTS production_metric_timers (
                    name TEXT PRIMARY KEY,
                    count INTEGER NOT NULL CHECK(count >= 0),
                    total_ms REAL NOT NULL CHECK(total_ms >= 0),
                    max_ms REAL NOT NULL CHECK(max_ms >= 0)
                );
                CREATE TABLE IF NOT EXISTS production_metric_periods (
                    name TEXT PRIMARY KEY,
                    started_at REAL NOT NULL
                );
            """)

    @contextmanager
    def _connect(self):
        database = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        database.execute("PRAGMA journal_mode=WAL")
        database.execute("PRAGMA synchronous=FULL")
        database.execute("PRAGMA busy_timeout=5000")
        try:
            yield database
        finally:
            database.close()

    @staticmethod
    def _metric_name(name: str, allowed: tuple[str, ...]) -> str:
        if not isinstance(name, str) or name not in allowed:
            raise ValueError("production metric name is not allowlisted")
        return name

    def increment(self, name: str, amount: int = 1) -> None:
        name = self._metric_name(name, COUNTERS)
        if type(amount) is not int or amount < 0:
            raise ValueError("production metric increment must be a non-negative integer")
        with self._connect() as database:
            database.execute(
                "INSERT INTO production_metric_counters(name,value) VALUES(?,?) "
                "ON CONFLICT(name) DO UPDATE SET value=value+excluded.value",
                (name, amount))

    def observe_duration(self, name: str, duration_ms: float) -> None:
        name = self._metric_name(name, TIMERS)
        value = float(duration_ms)
        if not math.isfinite(value) or value < 0:
            raise ValueError("production metric duration must be finite and non-negative")
        with self._connect() as database:
            database.execute(
                "INSERT INTO production_metric_timers(name,count,total_ms,max_ms) "
                "VALUES(?,1,?,?) ON CONFLICT(name) DO UPDATE SET "
                "count=count+1,total_ms=total_ms+excluded.total_ms,"
                "max_ms=MAX(max_ms,excluded.max_ms)",
                (name, value, value))

    def begin_period(self, name: str) -> bool:
        try:
            counter, _ = _PERIODS[name]
        except (KeyError, TypeError) as exc:
            raise ValueError("production metric period is not allowlisted") from exc
        with self._connect() as database:
            database.execute("BEGIN IMMEDIATE")
            cursor = database.execute(
                "INSERT OR IGNORE INTO production_metric_periods(name,started_at) "
                "VALUES(?,?)", (name, time.time()))
            if cursor.rowcount:
                database.execute(
                    "INSERT INTO production_metric_counters(name,value) VALUES(?,1) "
                    "ON CONFLICT(name) DO UPDATE SET value=value+1", (counter,))
            database.commit()
            return bool(cursor.rowcount)

    def end_period(self, name: str) -> bool:
        try:
            _, timer = _PERIODS[name]
        except (KeyError, TypeError) as exc:
            raise ValueError("production metric period is not allowlisted") from exc
        with self._connect() as database:
            database.execute("BEGIN IMMEDIATE")
            row = database.execute(
                "SELECT started_at FROM production_metric_periods WHERE name=?",
                (name,)).fetchone()
            if row is None:
                database.commit()
                return False
            duration_ms = max(0.0, (time.time() - float(row[0])) * 1000.0)
            database.execute(
                "DELETE FROM production_metric_periods WHERE name=?", (name,))
            database.execute(
                "INSERT INTO production_metric_timers(name,count,total_ms,max_ms) "
                "VALUES(?,1,?,?) ON CONFLICT(name) DO UPDATE SET "
                "count=count+1,total_ms=total_ms+excluded.total_ms,"
                "max_ms=MAX(max_ms,excluded.max_ms)",
                (timer, duration_ms, duration_ms))
            database.commit()
            return True

    def period_active(self, name: str) -> bool:
        if name not in _PERIODS:
            raise ValueError("production metric period is not allowlisted")
        with self._connect() as database:
            return database.execute(
                "SELECT 1 FROM production_metric_periods WHERE name=?", (name,)
            ).fetchone() is not None

    def snapshot(self) -> dict[str, Any]:
        with self._connect() as database:
            counters = {name: 0 for name in COUNTERS}
            counters.update({str(name): int(value) for name, value in database.execute(
                "SELECT name,value FROM production_metric_counters")})
            timers = {name: {"count": 0, "total_ms": 0.0, "max_ms": 0.0}
                      for name in TIMERS}
            timers.update({str(name): {"count": int(count),
                                       "total_ms": float(total),
                                       "max_ms": float(maximum)}
                           for name, count, total, maximum in database.execute(
                               "SELECT name,count,total_ms,max_ms "
                               "FROM production_metric_timers")})
            active_periods = {str(name): True for (name,) in database.execute(
                "SELECT name FROM production_metric_periods")}
        return {"counters": counters, "timers": timers,
                "active_periods": active_periods}


__all__ = ["COUNTERS", "TIMERS", "ProductionMetrics",
           "default_production_metrics_path"]
