"""SQLite-backed revisioned DP behavior state and intervention receipts."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from ..behavior_state import BehaviorState


@dataclass(frozen=True)
class RuntimeStateRecord:
    state: BehaviorState
    action_index: int
    application_receipts: Mapping[str, tuple[str, ...]]


@dataclass(frozen=True)
class RuntimeOutboxEvent:
    scope: str
    event_id: str
    event: Mapping[str, Any]


class BehaviorStateStore:
    """Persist DP-owned state with compare-and-swap across runtime processes."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        try:
            self.path.touch(mode=0o600, exist_ok=True)
            self.path.chmod(0o600)
        except OSError as exc:
            raise RuntimeError("could not secure the DP runtime state file") from exc
        with self._connect() as database:
            database.executescript("""
                CREATE TABLE IF NOT EXISTS behavior_state (
                    scope TEXT PRIMARY KEY,
                    revision INTEGER NOT NULL,
                    action_index INTEGER NOT NULL,
                    state_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS intervention_receipts (
                    scope TEXT NOT NULL,
                    intervention_id TEXT NOT NULL,
                    directive_ids_json TEXT NOT NULL,
                    action_index INTEGER NOT NULL,
                    PRIMARY KEY (scope, intervention_id)
                );
                CREATE TABLE IF NOT EXISTS runtime_event_outbox (
                    scope TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    event_json TEXT NOT NULL,
                    action_index INTEGER NOT NULL,
                    PRIMARY KEY (scope, event_id)
                );
                CREATE INDEX IF NOT EXISTS runtime_event_outbox_scope_order
                    ON runtime_event_outbox(scope, action_index, event_id);
                CREATE TABLE IF NOT EXISTS control_sessions (
                    scope TEXT PRIMARY KEY,
                    descriptor_json TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('open', 'closed'))
                );
                CREATE TABLE IF NOT EXISTS control_ticks (
                    scope TEXT NOT NULL,
                    tick_id TEXT NOT NULL,
                    decision_id TEXT NOT NULL,
                    request_digest TEXT NOT NULL,
                    basis_json TEXT NOT NULL,
                    decision_json TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('prepared', 'committed', 'aborted')),
                    result_json TEXT,
                    detail_json TEXT,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (scope, tick_id),
                    UNIQUE (scope, decision_id)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS control_ticks_one_pending
                    ON control_ticks(scope) WHERE status='prepared';
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
    def _read_receipts(database: sqlite3.Connection, scope: str,
                       intervention_ids: Sequence[str]
                       ) -> dict[str, tuple[str, ...]]:
        if not intervention_ids:
            return {}
        if len(intervention_ids) > 16 or len(set(intervention_ids)) != len(intervention_ids):
            raise ValueError("active intervention receipt query exceeds its bound")
        placeholders = ",".join("?" for _ in intervention_ids)
        rows = database.execute(
            "SELECT intervention_id, directive_ids_json FROM intervention_receipts "
            f"WHERE scope=? AND intervention_id IN ({placeholders})",
            (scope, *intervention_ids)).fetchall()
        result = {}
        for intervention_id, encoded in rows:
            values = json.loads(encoded)
            if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
                raise RuntimeError("persisted intervention receipt is malformed")
            result[str(intervention_id)] = tuple(values)
        return result

    def load(self, scope: str, *, initial: BehaviorState | None = None
             ) -> RuntimeStateRecord:
        with self._connect() as database:
            row = database.execute(
                "SELECT revision, action_index, state_json FROM behavior_state WHERE scope=?",
                (scope,)).fetchone()
            if row is None:
                return RuntimeStateRecord(initial or BehaviorState(), -1, {})
            revision, action_index, encoded = row
            state = BehaviorState.from_mapping(json.loads(encoded))
            if state.revision != int(revision):
                raise RuntimeError("persisted DP state revision does not match its row")
            active_ids = tuple(item.intervention_id for item in state.active_interventions)
            receipts = self._read_receipts(database, scope, active_ids)
        return RuntimeStateRecord(state, int(action_index), receipts)

    def save(self, scope: str, *, expected_revision: int,
             expected_action_index: int, action_index: int, state: BehaviorState,
             application_receipts: Mapping[str, tuple[str, ...]] | None = None,
             outbox_events: Sequence[Mapping[str, Any]] = (),
             control_tick_id: str | None = None,
             control_tick_result: Mapping[str, Any] | None = None) -> None:
        if state.revision != expected_revision + 1:
            raise ValueError("saved DP behavior state must advance exactly one revision")
        if action_index < 0:
            raise ValueError("host action index must be non-negative")
        receipts = dict(application_receipts or {})
        if len(receipts) > 16:
            raise ValueError("intervention application receipt count exceeds its bound")
        if not isinstance(outbox_events, (tuple, list)) or len(outbox_events) > 32:
            raise ValueError("runtime outbox event batch exceeds its bound")
        encoded_outbox: dict[str, str] = {}
        for event in outbox_events:
            if not isinstance(event, Mapping):
                raise ValueError("runtime outbox events must be mappings")
            event_id = event.get("event_id")
            if (not isinstance(event_id, str) or not event_id
                    or len(event_id) > 128 or event.get("scope") != scope):
                raise ValueError("runtime outbox event identity escapes its DP scope")
            encoded = json.dumps(dict(event), sort_keys=True, separators=(",", ":"))
            prior = encoded_outbox.get(event_id)
            if prior is not None and prior != encoded:
                raise ValueError("runtime outbox event ID has conflicting payloads")
            encoded_outbox[event_id] = encoded
        for intervention_id, directive_ids in receipts.items():
            if (not isinstance(intervention_id, str) or not intervention_id
                    or len(intervention_id) > 128
                    or not isinstance(directive_ids, (tuple, list))
                    or not 1 <= len(directive_ids) <= 32
                    or any(not isinstance(item, str) or not item or len(item) > 128
                           for item in directive_ids)
                    or len(directive_ids) != len(set(directive_ids))):
                raise ValueError("intervention application receipt is malformed")
        state_json = json.dumps(state.to_dict(), sort_keys=True, separators=(",", ":"))
        with self._connect() as database:
            database.execute("BEGIN IMMEDIATE")
            tick = None
            if control_tick_id is not None:
                tick = database.execute(
                    "SELECT status, basis_json FROM control_ticks "
                    "WHERE scope=? AND tick_id=?", (scope, control_tick_id)).fetchone()
                if tick is None or tick[0] != "prepared":
                    database.rollback()
                    raise RuntimeError("control tick is not prepared for commit")
                basis = json.loads(tick[1])
                if (int(basis.get("expected_behavior_revision", -1)) != expected_revision
                        or int(basis.get("expected_action_index", -2)) != expected_action_index
                        or int(basis.get("host_action_index", -1)) != action_index):
                    database.rollback()
                    raise RuntimeError("control tick basis differs from the behavior-state commit")
            row = database.execute(
                "SELECT revision, action_index FROM behavior_state WHERE scope=?",
                (scope,)).fetchone()
            actual_revision, actual_action = ((int(row[0]), int(row[1]))
                                              if row is not None else (0, -1))
            if (actual_revision != expected_revision
                    or actual_action != expected_action_index):
                database.rollback()
                raise RuntimeError(
                    "DP runtime compare-and-swap conflict; re-read session state")
            if action_index <= actual_action:
                database.rollback()
                raise ValueError("runtime action indexes must increase monotonically")
            database.execute(
                "INSERT INTO behavior_state(scope, revision, action_index, state_json) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(scope) DO UPDATE SET "
                "revision=excluded.revision, action_index=excluded.action_index, "
                "state_json=excluded.state_json",
                (scope, state.revision, action_index, state_json))
            for intervention_id, directive_ids in receipts.items():
                encoded = json.dumps(list(directive_ids), separators=(",", ":"))
                prior = database.execute(
                    "SELECT directive_ids_json FROM intervention_receipts "
                    "WHERE scope=? AND intervention_id=?",
                    (scope, intervention_id)).fetchone()
                if prior is not None and prior[0] != encoded:
                    database.rollback()
                    raise RuntimeError("intervention application receipt changed")
                database.execute(
                    "INSERT OR IGNORE INTO intervention_receipts "
                    "(scope, intervention_id, directive_ids_json, action_index) "
                    "VALUES (?, ?, ?, ?)",
                    (scope, intervention_id, encoded, action_index))
            for event_id, encoded in encoded_outbox.items():
                prior = database.execute(
                    "SELECT event_json FROM runtime_event_outbox "
                    "WHERE scope=? AND event_id=?", (scope, event_id)).fetchone()
                if prior is not None and prior[0] != encoded:
                    database.rollback()
                    raise RuntimeError("runtime outbox event payload changed")
                database.execute(
                    "INSERT OR IGNORE INTO runtime_event_outbox "
                    "(scope, event_id, event_json, action_index) VALUES (?, ?, ?, ?)",
                    (scope, event_id, encoded, action_index))
            if control_tick_id is not None:
                result = dict(control_tick_result or {"behavior_revision": state.revision})
                updated = database.execute(
                    "UPDATE control_ticks SET status='committed', result_json=? "
                    "WHERE scope=? AND tick_id=? AND status='prepared'",
                    (json.dumps(result, sort_keys=True, separators=(",", ":")),
                     scope, control_tick_id))
                if updated.rowcount != 1:
                    database.rollback()
                    raise RuntimeError("prepared control tick changed during commit")
            database.commit()

    def register_control_session(self, scope: str, descriptor: Mapping[str, Any]) -> None:
        encoded = json.dumps(dict(descriptor), sort_keys=True, separators=(",", ":"))
        with self._connect() as database:
            database.execute("BEGIN IMMEDIATE")
            row = database.execute(
                "SELECT descriptor_json, status FROM control_sessions WHERE scope=?",
                (scope,)).fetchone()
            if row is not None:
                if row[0] != encoded:
                    database.rollback()
                    raise RuntimeError("control session identity is already bound to another task")
                if row[1] != "open":
                    database.rollback()
                    raise RuntimeError("control session is closed")
            else:
                database.execute(
                    "INSERT INTO control_sessions(scope, descriptor_json, status) "
                    "VALUES (?, ?, 'open')", (scope, encoded))
            database.commit()

    def control_session(self, scope: str) -> Mapping[str, Any] | None:
        with self._connect() as database:
            row = database.execute(
                "SELECT descriptor_json, status FROM control_sessions WHERE scope=?",
                (scope,)).fetchone()
        if row is None:
            return None
        return {"descriptor": json.loads(row[0]), "status": str(row[1])}

    def prepare_control_tick(self, scope: str, *, tick_id: str,
                             request_digest: str, basis: Mapping[str, Any],
                             decision: Mapping[str, Any]) -> Mapping[str, Any]:
        decision_id = str(decision.get("decision_id") or "")
        if not decision_id:
            raise ValueError("prepared control decision requires an ID")
        with self._connect() as database:
            database.execute("BEGIN IMMEDIATE")
            session = database.execute(
                "SELECT status FROM control_sessions WHERE scope=?", (scope,)).fetchone()
            if session is None or session[0] != "open":
                database.rollback()
                raise RuntimeError("control session is unavailable")
            existing = database.execute(
                "SELECT request_digest, decision_json, basis_json, status, result_json, detail_json "
                "FROM control_ticks WHERE scope=? AND tick_id=?", (scope, tick_id)).fetchone()
            if existing is not None:
                if existing[0] != request_digest:
                    database.rollback()
                    raise RuntimeError("control tick ID was reused with different input")
                database.commit()
                return {"request_digest": existing[0], "decision": json.loads(existing[1]),
                        "basis": json.loads(existing[2]), "status": existing[3],
                        "result": json.loads(existing[4]) if existing[4] else None,
                        "detail": json.loads(existing[5]) if existing[5] else None}
            state_row = database.execute(
                "SELECT revision, action_index FROM behavior_state WHERE scope=?", (scope,)).fetchone()
            actual_revision, actual_action = ((int(state_row[0]), int(state_row[1]))
                                              if state_row is not None else (0, -1))
            if (actual_revision != int(basis["expected_behavior_revision"])
                    or actual_action != int(basis["expected_action_index"])):
                database.rollback()
                raise RuntimeError("control tick basis is stale")
            try:
                database.execute(
                    "INSERT INTO control_ticks(scope, tick_id, decision_id, request_digest, "
                    "basis_json, decision_json, status, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, 'prepared', ?)",
                    (scope, tick_id, decision_id, request_digest,
                     json.dumps(dict(basis), sort_keys=True, separators=(",", ":")),
                     json.dumps(dict(decision), sort_keys=True, separators=(",", ":")),
                     datetime.now(timezone.utc).isoformat()))
            except sqlite3.IntegrityError as exc:
                database.rollback()
                raise RuntimeError("another control tick is already prepared") from exc
            database.commit()
        return {"request_digest": request_digest, "decision": dict(decision),
                "basis": dict(basis), "status": "prepared", "result": None,
                "detail": None}

    def control_tick(self, scope: str, tick_id: str) -> Mapping[str, Any] | None:
        with self._connect() as database:
            row = database.execute(
                "SELECT request_digest, decision_json, basis_json, status, result_json, detail_json "
                "FROM control_ticks WHERE scope=? AND tick_id=?", (scope, tick_id)).fetchone()
        if row is None:
            return None
        return {"request_digest": row[0], "decision": json.loads(row[1]),
                "basis": json.loads(row[2]), "status": row[3],
                "result": json.loads(row[4]) if row[4] else None,
                "detail": json.loads(row[5]) if row[5] else None}

    def abort_control_tick(self, scope: str, tick_id: str, *,
                           decision_id: str, detail: Mapping[str, Any]) -> Mapping[str, Any]:
        encoded = json.dumps(dict(detail), sort_keys=True, separators=(",", ":"))
        with self._connect() as database:
            database.execute("BEGIN IMMEDIATE")
            row = database.execute(
                "SELECT decision_id, status, detail_json FROM control_ticks "
                "WHERE scope=? AND tick_id=?", (scope, tick_id)).fetchone()
            if row is None or row[0] != decision_id:
                database.rollback()
                raise RuntimeError("control tick is unknown")
            if row[1] == "committed":
                database.rollback()
                raise RuntimeError("committed control tick cannot be aborted")
            if row[1] == "aborted":
                if row[2] != encoded:
                    database.rollback()
                    raise RuntimeError("control tick abort receipt changed")
            else:
                database.execute(
                    "UPDATE control_ticks SET status='aborted', detail_json=? "
                    "WHERE scope=? AND tick_id=? AND status='prepared'",
                    (encoded, scope, tick_id))
            database.commit()
        return {"control_session_id": scope, "tick_id": tick_id,
                "decision_id": decision_id, "status": "aborted",
                "detail": dict(detail)}

    def close_control_session(self, scope: str) -> None:
        with self._connect() as database:
            database.execute("BEGIN IMMEDIATE")
            pending = database.execute(
                "SELECT 1 FROM control_ticks WHERE scope=? AND status='prepared' LIMIT 1",
                (scope,)).fetchone()
            if pending is not None:
                database.rollback()
                raise RuntimeError("cannot close a control session with a prepared decision")
            changed = database.execute(
                "UPDATE control_sessions SET status='closed' WHERE scope=? AND status='open'",
                (scope,)).rowcount
            if not changed:
                database.rollback()
                raise RuntimeError("control session is unavailable")
            database.commit()

    def pending_control_ticks(self, scope: str) -> tuple[Mapping[str, Any], ...]:
        with self._connect() as database:
            rows = database.execute(
                "SELECT tick_id, decision_id, request_digest, basis_json, decision_json, created_at "
                "FROM control_ticks WHERE scope=? AND status='prepared' ORDER BY created_at LIMIT 1",
                (scope,)).fetchall()
        return tuple({"tick_id": row[0], "decision_id": row[1],
                      "request_digest": row[2], "basis": json.loads(row[3]),
                      "decision": json.loads(row[4]), "status": "prepared",
                      "result": None, "detail": None, "created_at": row[5]}
                     for row in rows)

    def synchronize_csw_state(self, scope: str, *, expected_revision: int,
                               expected_action_index: int,
                               active_stateworks: Sequence[str]) -> None:
        """Refresh DP's CSW-owned selection projection without changing DP revision."""
        if len(active_stateworks) > 16 or len(set(active_stateworks)) != len(active_stateworks):
            raise ValueError("authoritative CSW selection is malformed")
        with self._connect() as database:
            database.execute("BEGIN IMMEDIATE")
            row = database.execute(
                "SELECT revision, action_index, state_json FROM behavior_state WHERE scope=?",
                (scope,)).fetchone()
            actual_revision, actual_action = ((int(row[0]), int(row[1]))
                                              if row is not None else (0, -1))
            if (actual_revision != expected_revision
                    or actual_action != expected_action_index):
                database.rollback()
                raise RuntimeError("behavior state changed during authority reconciliation")
            state = BehaviorState.from_mapping(json.loads(row[2]) if row is not None else {})
            if tuple(state.active_stateworks) != tuple(active_stateworks):
                synchronized = BehaviorState.from_mapping({
                    **state.to_dict(), "active_stateworks": list(active_stateworks)})
                database.execute(
                    "INSERT INTO behavior_state(scope, revision, action_index, state_json) "
                    "VALUES (?, ?, ?, ?) ON CONFLICT(scope) DO UPDATE SET "
                    "state_json=excluded.state_json",
                    (scope, actual_revision, actual_action,
                     json.dumps(synchronized.to_dict(), sort_keys=True, separators=(",", ":"))))
            database.commit()

    def latest_control_tick_basis(self, scope: str) -> Mapping[str, Any] | None:
        with self._connect() as database:
            row = database.execute(
                "SELECT basis_json FROM control_ticks WHERE scope=? AND status='committed' "
                "ORDER BY created_at DESC LIMIT 1", (scope,)).fetchone()
        return json.loads(row[0]) if row is not None else None

    def pending_outbox(self, scope: str, *, limit: int = 64
                       ) -> tuple[RuntimeOutboxEvent, ...]:
        if type(limit) is not int or not 1 <= limit <= 256:
            raise ValueError("runtime outbox read limit must be between one and 256")
        with self._connect() as database:
            rows = database.execute(
                "SELECT event_id, event_json FROM runtime_event_outbox "
                "WHERE scope=? ORDER BY action_index, event_id LIMIT ?",
                (scope, limit)).fetchall()
        events = []
        for event_id, encoded in rows:
            payload = json.loads(encoded)
            if (not isinstance(payload, dict) or payload.get("event_id") != event_id
                    or payload.get("scope") != scope):
                raise RuntimeError("persisted runtime outbox event is malformed")
            events.append(RuntimeOutboxEvent(scope, str(event_id), payload))
        return tuple(events)

    def acknowledge_outbox_event(self, scope: str, event_id: str) -> None:
        """Remove an event only after the idempotent event sink accepts it."""
        with self._connect() as database:
            database.execute("BEGIN IMMEDIATE")
            database.execute(
                "DELETE FROM runtime_event_outbox WHERE scope=? AND event_id=?",
                (scope, event_id))
            database.commit()


__all__ = ["BehaviorStateStore", "RuntimeOutboxEvent", "RuntimeStateRecord"]
