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
    "laya_query_opportunities",
    "laya_valid_results",
    "laya_shadow_results",
    "laya_failures",
    "laya_timeouts",
    "laya_cache_hits",
    "laya_influenced_decisions",
    "laya_influenced_interventions",
    "laya_influenced_candidate_changes",
    "laya_influenced_mode_changes",
    "laya_influenced_framework_changes",
    "laya_influenced_statework_changes",
    "laya_influenced_intervention_effective",
    "laya_influenced_intervention_ineffective",
)

TIMERS = (
    "control_tick_prepare_duration_ms",
    "control_tick_commit_duration_ms",
    "reconciliation_duration_ms",
    "degraded_period_duration_ms",
    "dp_outage_duration_ms",
    "laya_query_latency_ms",
)

# Laya's telemetry dimensions are deliberately finite.  The per-family
# counters are generated from these fixed allowlists, never from caller text.
QUESTION_FAMILIES = (
    "action_relation",
    "progress_relation",
    "retry_relation",
    "goal_drift",
    "route_preference",
    "decomposition_preference",
    "integration_preference",
    "test_strategy_preference",
)

LAYA_EVENTS = {
    # An opportunity is a planner-emitted System-1 frame; an attempt is a
    # provider call made for that opportunity.
    "opportunity": "laya_query_opportunities",
    "attempt": "laya_query_attempts",
    "valid": "laya_valid_results",
    "shadow": "laya_shadow_results",
    "advisory": "laya_advisory_results",
    "failure": "laya_failures",
    "timeout": "laya_timeouts",
    "cache_hit": "laya_cache_hits",
    "influenced_decision": "laya_influenced_decisions",
    "intervention_created": "laya_influenced_interventions",
    "candidate_change": "laya_influenced_candidate_changes",
    "mode_change": "laya_influenced_mode_changes",
    "framework_change": "laya_influenced_framework_changes",
    "statework_change": "laya_influenced_statework_changes",
    "intervention_effective": "laya_influenced_intervention_effective",
    "intervention_ineffective": "laya_influenced_intervention_ineffective",
}

_FAMILY_EVENT_COUNTERS = {
    (family, event): f"laya_question_{family}_{event}"
    for family in QUESTION_FAMILIES
    for event in LAYA_EVENTS
}
COUNTERS = COUNTERS + tuple(_FAMILY_EVENT_COUNTERS.values())

# Each observation is stored in exactly one bucket.  Percentiles are reported
# as the selected bucket's upper bound; the final bucket is a saturation bound
# for durations at or above 60 seconds.  This keeps storage constant-sized.
_LAYA_LATENCY_BUCKET_UPPER_BOUNDS_MS = (
    1, 2, 5, 10, 20, 50, 100, 200, 500, 1_000, 2_000, 5_000,
    10_000, 30_000, 60_000,
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
                CREATE TABLE IF NOT EXISTS production_metric_timer_buckets (
                    name TEXT NOT NULL,
                    bucket_index INTEGER NOT NULL
                        CHECK(bucket_index >= 0 AND bucket_index < 15),
                    count INTEGER NOT NULL CHECK(count >= 0),
                    PRIMARY KEY(name, bucket_index)
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
            if name == "laya_query_latency_ms":
                database.execute("BEGIN IMMEDIATE")
            database.execute(
                "INSERT INTO production_metric_timers(name,count,total_ms,max_ms) "
                "VALUES(?,1,?,?) ON CONFLICT(name) DO UPDATE SET "
                "count=count+1,total_ms=total_ms+excluded.total_ms,"
                "max_ms=MAX(max_ms,excluded.max_ms)",
                (name, value, value))
            if name == "laya_query_latency_ms":
                bucket_index = next(
                    (index for index, upper_bound in enumerate(
                        _LAYA_LATENCY_BUCKET_UPPER_BOUNDS_MS)
                     if value <= upper_bound),
                    len(_LAYA_LATENCY_BUCKET_UPPER_BOUNDS_MS) - 1)
                database.execute(
                    "INSERT INTO production_metric_timer_buckets"
                    "(name,bucket_index,count) VALUES(?,?,1) "
                    "ON CONFLICT(name,bucket_index) DO UPDATE SET "
                    "count=count+1", (name, bucket_index))
                database.commit()

    def record_laya_event(self, event: str, *, question_family: str | None = None,
                          amount: int = 1) -> None:
        """Record a Laya event globally and, when supplied, by fixed question family.

        ``event`` is one of ``LAYA_EVENTS``.  Family breakdowns use the same
        event vocabulary and are bounded to ``QUESTION_FAMILIES``. An
        ``opportunity`` is a planner-emitted System-1 frame; ``attempt`` counts
        provider calls and may therefore be lower than opportunities.
        """
        try:
            global_counter = LAYA_EVENTS[event]
        except (KeyError, TypeError) as exc:
            raise ValueError("Laya production event is not allowlisted") from exc
        if type(amount) is not int or amount < 0:
            raise ValueError("production metric increment must be a non-negative integer")
        counters = [global_counter]
        if question_family is not None:
            try:
                counters.append(_FAMILY_EVENT_COUNTERS[(question_family, event)])
            except (KeyError, TypeError) as exc:
                raise ValueError("Laya question family is not allowlisted") from exc
        with self._connect() as database:
            if len(counters) > 1:
                database.execute("BEGIN IMMEDIATE")
            for counter in counters:
                database.execute(
                    "INSERT INTO production_metric_counters(name,value) VALUES(?,?) "
                    "ON CONFLICT(name) DO UPDATE SET value=value+excluded.value",
                    (counter, amount))
            if len(counters) > 1:
                database.commit()

    def record_laya_family_event(self, event: str, *, question_family: str,
                                 amount: int = 1) -> None:
        """Record one question's contribution without incrementing a global total.

        Use this when a frame contains several distinct questions: record the
        frame-level global event once with :meth:`record_laya_event`, then
        record each question-family breakdown here.
        """
        try:
            counter = _FAMILY_EVENT_COUNTERS[(question_family, event)]
        except (KeyError, TypeError) as exc:
            raise ValueError("Laya event or question family is not allowlisted") from exc
        if type(amount) is not int or amount < 0:
            raise ValueError("production metric increment must be a non-negative integer")
        with self._connect() as database:
            database.execute(
                "INSERT INTO production_metric_counters(name,value) VALUES(?,?) "
                "ON CONFLICT(name) DO UPDATE SET value=value+excluded.value",
                (counter, amount))

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
            laya_timer = timers["laya_query_latency_ms"]
            histogram = {int(index): int(count) for index, count in database.execute(
                "SELECT bucket_index,count FROM production_metric_timer_buckets "
                "WHERE name='laya_query_latency_ms'")}
            laya_timer.update(_laya_latency_summary(laya_timer, histogram))
            active_periods = {str(name): True for (name,) in database.execute(
                "SELECT name FROM production_metric_periods")}
        return {"counters": counters, "timers": timers,
                "active_periods": active_periods}


def _laya_latency_summary(timer: dict[str, Any], histogram: dict[int, int]
                          ) -> dict[str, float | int | bool | None]:
    """Summarize Laya latency; p50/p95 are fixed-bucket upper-bound estimates."""
    count = int(timer["count"])
    mean = float(timer["total_ms"]) / count if count else 0.0

    def percentile(fraction: float) -> tuple[int | None, bool]:
        if not count:
            return None, False
        rank = math.ceil(count * fraction)
        seen = 0
        for index, upper_bound in enumerate(_LAYA_LATENCY_BUCKET_UPPER_BOUNDS_MS):
            seen += histogram.get(index, 0)
            if seen >= rank:
                return upper_bound, index == len(_LAYA_LATENCY_BUCKET_UPPER_BOUNDS_MS) - 1
        # A mismatched histogram should be visible as unavailable rather than
        # silently reporting a plausible percentile.
        return None, False

    p50, p50_saturated = percentile(0.50)
    p95, p95_saturated = percentile(0.95)
    return {"mean_ms": mean, "p50_ms": p50, "p95_ms": p95,
            "p50_saturated": p50_saturated, "p95_saturated": p95_saturated}


__all__ = ["COUNTERS", "TIMERS", "QUESTION_FAMILIES", "LAYA_EVENTS",
           "ProductionMetrics",
           "default_production_metrics_path"]
