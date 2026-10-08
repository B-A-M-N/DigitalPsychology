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
from ..compatibility import MAX_ACTIVE_STATEWORKS


@dataclass(frozen=True)
class RuntimeStateRecord:
    state: BehaviorState
    action_index: int
    application_receipts: Mapping[str, tuple[str, ...]]
    cfw_signal_cursor: int = 0


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
                    state_json TEXT NOT NULL,
                    cfw_signal_cursor INTEGER NOT NULL DEFAULT 0
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
                CREATE TABLE IF NOT EXISTS runtime_event_delivery (
                    scope TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    enqueued_at TEXT,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    last_attempt_at TEXT,
                    last_error_class TEXT,
                    last_failure_at TEXT,
                    delivered_at TEXT,
                    PRIMARY KEY (scope, event_id)
                );
                CREATE INDEX IF NOT EXISTS runtime_event_outbox_scope_order
                    ON runtime_event_outbox(scope, action_index, event_id);
                CREATE INDEX IF NOT EXISTS runtime_event_delivery_scope_type
                    ON runtime_event_delivery(scope, event_type, delivered_at);
                CREATE TABLE IF NOT EXISTS control_sessions (
                    scope TEXT PRIMARY KEY,
                    descriptor_json TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('open', 'closed'))
                );
                CREATE TABLE IF NOT EXISTS control_session_provider_identity (
                    scope TEXT PRIMARY KEY,
                    provider_identity_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS control_reconciliation_receipts (
                    scope TEXT NOT NULL,
                    tick_id TEXT NOT NULL,
                    acknowledged_at TEXT NOT NULL,
                    PRIMARY KEY (scope, tick_id)
                );
                CREATE TABLE IF NOT EXISTS control_ticks (
                    scope TEXT NOT NULL,
                    tick_id TEXT NOT NULL,
                    boundary_sequence INTEGER NOT NULL DEFAULT -1,
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
                CREATE TABLE IF NOT EXISTS behavioral_action_traces (
                    scope TEXT NOT NULL,
                    action_index INTEGER NOT NULL,
                    event_json TEXT NOT NULL,
                    PRIMARY KEY (scope, action_index)
                );
                CREATE TABLE IF NOT EXISTS behavioral_control_transitions (
                    scope TEXT NOT NULL,
                    boundary_sequence INTEGER NOT NULL,
                    behavior_revision INTEGER NOT NULL,
                    event_json TEXT NOT NULL,
                    PRIMARY KEY (scope, boundary_sequence),
                    UNIQUE (scope, boundary_sequence, behavior_revision)
                );
            CREATE UNIQUE INDEX IF NOT EXISTS control_ticks_one_pending
                ON control_ticks(scope) WHERE status='prepared';
            """)
            columns = {str(row[1]) for row in database.execute(
                "PRAGMA table_info(control_ticks)").fetchall()}
            if "boundary_sequence" not in columns:
                database.execute(
                    "ALTER TABLE control_ticks ADD COLUMN boundary_sequence "
                    "INTEGER NOT NULL DEFAULT -1")
            state_columns = {str(row[1]) for row in database.execute(
                "PRAGMA table_info(behavior_state)").fetchall()}
            if "cfw_signal_cursor" not in state_columns:
                database.execute(
                    "ALTER TABLE behavior_state ADD COLUMN cfw_signal_cursor "
                    "INTEGER NOT NULL DEFAULT 0")
            # Preserve visibility for events that were already pending when
            # durable delivery diagnostics were introduced.
            pending = database.execute(
                "SELECT scope, event_id, event_json FROM runtime_event_outbox"
            ).fetchall()
            migrated_at = datetime.now(timezone.utc).isoformat()
            for scope, event_id, encoded in pending:
                try:
                    payload = json.loads(encoded)
                except (TypeError, json.JSONDecodeError):
                    payload = {}
                event_type = (payload.get("event_type") or payload.get("kind")
                              if isinstance(payload, dict) else None)
                if not isinstance(event_type, str) or not event_type:
                    event_type = "unknown"
                database.execute(
                    "INSERT OR IGNORE INTO runtime_event_delivery "
                    "(scope, event_id, event_type, enqueued_at) VALUES (?, ?, ?, ?)",
                    (scope, event_id, event_type[:128], migrated_at))

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
                "SELECT revision, action_index, state_json, cfw_signal_cursor "
                "FROM behavior_state WHERE scope=?",
                (scope,)).fetchone()
            if row is None:
                return RuntimeStateRecord(initial or BehaviorState(), -1, {}, 0)
            revision, action_index, encoded, signal_cursor = row
            state = BehaviorState.from_mapping(json.loads(encoded))
            if state.revision != int(revision):
                raise RuntimeError("persisted DP state revision does not match its row")
            active_ids = tuple(item.intervention_id for item in state.active_interventions)
            receipts = self._read_receipts(database, scope, active_ids)
        return RuntimeStateRecord(state, int(action_index), receipts, int(signal_cursor))

    def save(self, scope: str, *, expected_revision: int,
             expected_action_index: int, action_index: int, state: BehaviorState,
             application_receipts: Mapping[str, tuple[str, ...]] | None = None,
             outbox_events: Sequence[Mapping[str, Any]] = (),
             control_tick_id: str | None = None,
             control_tick_result: Mapping[str, Any] | None = None,
             behavioral_action_trace: Mapping[str, Any] | None = None,
             behavioral_control_transition: Mapping[str, Any] | None = None,
             cfw_signal_cursor: int | None = None) -> None:
        if state.revision != expected_revision + 1:
            raise ValueError("saved DP behavior state must advance exactly one revision")
        if action_index < 0 and control_tick_id is None:
            raise ValueError("host action index must be non-negative")
        if (cfw_signal_cursor is not None
                and (type(cfw_signal_cursor) is not int or cfw_signal_cursor < 0)):
            raise ValueError("CFW signal cursor must be a non-negative integer")
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
        trace_json = None
        if behavioral_action_trace is not None:
            if (not isinstance(behavioral_action_trace, Mapping)
                    or type(behavioral_action_trace.get("action_index")) is not int
                    or behavioral_action_trace.get("action_index") != action_index
                    or control_tick_id is None):
                raise ValueError("committed action trace does not match its control commit")
            trace_json = json.dumps(dict(behavioral_action_trace), sort_keys=True,
                                    separators=(",", ":"), allow_nan=False)
        transition_json = None
        transition_sequence = None
        if behavioral_control_transition is not None:
            transition = dict(behavioral_control_transition)
            transition_sequence = transition.get("boundary_sequence")
            if (control_tick_id is None
                    or transition.get("control_session_id") != scope
                    or type(transition_sequence) is not int
                    or transition_sequence < 0
                    or transition.get("behavior_revision") != state.revision):
                raise ValueError(
                    "control transition trace does not match its committed control state")
            transition_json = json.dumps(
                transition, sort_keys=True, separators=(",", ":"), allow_nan=False)
        elif control_tick_id is not None:
            raise ValueError("every committed control tick requires a transition trace")
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
                        or int(basis.get("commit_action_index",
                                         basis.get("host_action_index", -1))) != action_index):
                    database.rollback()
                    raise RuntimeError("control tick basis differs from the behavior-state commit")
                if (transition_sequence is None
                        or int(basis.get("boundary_sequence", -1)) != transition_sequence):
                    database.rollback()
                    raise RuntimeError("control transition sequence differs from its tick basis")
            row = database.execute(
                "SELECT revision, action_index, cfw_signal_cursor "
                "FROM behavior_state WHERE scope=?",
                (scope,)).fetchone()
            actual_revision, actual_action = ((int(row[0]), int(row[1]))
                                              if row is not None else (0, -1))
            actual_signal_cursor = int(row[2]) if row is not None else 0
            if (actual_revision != expected_revision
                    or actual_action != expected_action_index):
                database.rollback()
                raise RuntimeError(
                    "DP runtime compare-and-swap conflict; re-read session state")
            basis = json.loads(tick[1]) if tick is not None else {}
            committed_signal_cursor = cfw_signal_cursor
            if committed_signal_cursor is None and tick is not None:
                committed_signal_cursor = int(basis.get(
                    "cfw_signal_cursor", actual_signal_cursor))
            if committed_signal_cursor is None:
                committed_signal_cursor = actual_signal_cursor
            if committed_signal_cursor < actual_signal_cursor:
                database.rollback()
                raise ValueError("CFW signal cursor cannot move backwards")
            advances_action = basis.get("boundary_kind", basis.get(
                "boundary_phase", "post_action")) == "post_action"
            valid_action_index = (action_index > actual_action if advances_action
                                  else action_index == actual_action)
            if not valid_action_index:
                database.rollback()
                raise ValueError("control boundary action index is inconsistent")
            database.execute(
                "INSERT INTO behavior_state(scope, revision, action_index, state_json, "
                "cfw_signal_cursor) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(scope) DO UPDATE SET "
                "revision=excluded.revision, action_index=excluded.action_index, "
                "state_json=excluded.state_json, "
                "cfw_signal_cursor=excluded.cfw_signal_cursor",
                (scope, state.revision, action_index, state_json,
                 committed_signal_cursor))
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
            active_intervention_ids = tuple(
                item.intervention_id for item in state.active_interventions)
            if active_intervention_ids:
                placeholders = ",".join("?" for _ in active_intervention_ids)
                database.execute(
                    "DELETE FROM intervention_receipts WHERE scope=? "
                    f"AND intervention_id NOT IN ({placeholders})",
                    (scope, *active_intervention_ids))
            else:
                database.execute(
                    "DELETE FROM intervention_receipts WHERE scope=?", (scope,))
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
                try:
                    event_payload = json.loads(encoded)
                    event_type = (event_payload.get("event_type")
                                  or event_payload.get("kind") or "unknown")
                except (AttributeError, TypeError, json.JSONDecodeError):
                    event_type = "unknown"
                if not isinstance(event_type, str) or not event_type:
                    event_type = "unknown"
                database.execute(
                    "INSERT OR IGNORE INTO runtime_event_delivery "
                    "(scope, event_id, event_type, enqueued_at) VALUES (?, ?, ?, ?)",
                    (scope, event_id, event_type[:128],
                     datetime.now(timezone.utc).isoformat()))
            if trace_json is not None:
                prior_trace = database.execute(
                    "SELECT event_json FROM behavioral_action_traces "
                    "WHERE scope=? AND action_index=?", (scope, action_index)).fetchone()
                if prior_trace is not None and prior_trace[0] != trace_json:
                    database.rollback()
                    raise RuntimeError("committed action trace changed")
                database.execute(
                    "INSERT OR IGNORE INTO behavioral_action_traces "
                    "(scope, action_index, event_json) VALUES (?, ?, ?)",
                    (scope, action_index, trace_json))
            if transition_json is not None:
                prior_transition = database.execute(
                    "SELECT event_json FROM behavioral_control_transitions "
                    "WHERE scope=? AND boundary_sequence=?",
                    (scope, transition_sequence)).fetchone()
                if (prior_transition is not None
                        and prior_transition[0] != transition_json):
                    database.rollback()
                    raise RuntimeError("committed control transition changed")
                database.execute(
                    "INSERT OR IGNORE INTO behavioral_control_transitions "
                    "(scope, boundary_sequence, behavior_revision, event_json) "
                    "VALUES (?, ?, ?, ?)",
                    (scope, transition_sequence, state.revision, transition_json))
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

    def register_control_session(self, scope: str, descriptor: Mapping[str, Any], *,
                                  provider_identity: Mapping[str, Any] | None = None,
                                  initial_state: BehaviorState | None = None) -> None:
        encoded = json.dumps(dict(descriptor), sort_keys=True, separators=(",", ":"))
        encoded_provider = json.dumps(
            dict(provider_identity or {}), sort_keys=True, separators=(",", ":"))
        seeded_state = initial_state or BehaviorState()
        if not isinstance(seeded_state, BehaviorState) or seeded_state.revision != 0:
            raise ValueError("control session initial state must be revision zero")
        encoded_state = json.dumps(seeded_state.to_dict(), sort_keys=True,
                                  separators=(",", ":"))
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
                database.execute(
                    "INSERT INTO control_session_provider_identity(scope, provider_identity_json) "
                    "VALUES (?, ?)", (scope, encoded_provider))
            if row is not None:
                provider_row = database.execute(
                    "SELECT provider_identity_json FROM control_session_provider_identity "
                    "WHERE scope=?", (scope,)).fetchone()
                # Legacy sessions have no provider pin. Keep them explicitly
                # unbound rather than claiming the current process was original.
                if provider_row is not None and provider_row[0] != encoded_provider:
                    database.rollback()
                    raise RuntimeError("control session provider identity has changed")
            database.execute(
                "INSERT OR IGNORE INTO behavior_state(scope, revision, action_index, state_json) "
                "VALUES (?, 0, -1, ?)", (scope, encoded_state))
            database.commit()

    def control_session(self, scope: str) -> Mapping[str, Any] | None:
        with self._connect() as database:
            row = database.execute(
                "SELECT descriptor_json, status FROM control_sessions WHERE scope=?",
                (scope,)).fetchone()
            provider_row = database.execute(
                "SELECT provider_identity_json FROM control_session_provider_identity "
                "WHERE scope=?", (scope,)).fetchone()
        if row is None:
            return None
        return {"descriptor": json.loads(row[0]), "status": str(row[1]),
                "provider_identity": (json.loads(provider_row[0])
                                      if provider_row is not None else None)}

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
            reconciliation = database.execute(
                "SELECT 1 FROM control_ticks AS tick "
                "LEFT JOIN control_reconciliation_receipts AS receipt "
                "ON receipt.scope=tick.scope AND receipt.tick_id=tick.tick_id "
                "WHERE tick.scope=? AND tick.status='aborted' "
                "AND json_extract(tick.detail_json, '$.requires_reconciliation')=1 "
                "AND receipt.tick_id IS NULL LIMIT 1", (scope,)
            ).fetchone()
            if reconciliation is not None:
                database.rollback()
                raise RuntimeError(
                    "control session requires reconciliation before a new control tick")
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
            latest_boundary = database.execute(
                "SELECT COALESCE(MAX(boundary_sequence), -1) FROM control_ticks "
                "WHERE scope=?", (scope,)).fetchone()[0]
            if int(basis.get("boundary_sequence", -1)) <= int(latest_boundary):
                database.rollback()
                raise RuntimeError("control boundary sequence is stale")
            try:
                database.execute(
                    "INSERT INTO control_ticks(scope, tick_id, boundary_sequence, decision_id, request_digest, "
                    "basis_json, decision_json, status, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 'prepared', ?)",
                    (scope, tick_id, int(basis["boundary_sequence"]), decision_id, request_digest,
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

    def control_reconciliation_required(self, scope: str) -> bool:
        """Check whether an aborted partial apply still needs host recovery."""
        with self._connect() as database:
            row = database.execute(
                "SELECT 1 FROM control_ticks AS tick "
                "LEFT JOIN control_reconciliation_receipts AS receipt "
                "ON receipt.scope=tick.scope AND receipt.tick_id=tick.tick_id "
                "WHERE tick.scope=? AND tick.status='aborted' "
                "AND json_extract(tick.detail_json, '$.requires_reconciliation')=1 "
                "AND receipt.tick_id IS NULL LIMIT 1", (scope,)
            ).fetchone()
        return row is not None

    def behavioral_action_traces(self, scope: str) -> tuple[Mapping[str, Any], ...]:
        """Read committed-action telemetry, separate from control transitions."""
        with self._connect() as database:
            rows = database.execute(
                "SELECT event_json FROM behavioral_action_traces "
                "WHERE scope=? ORDER BY action_index", (scope,)).fetchall()
        return tuple(json.loads(row[0]) for row in rows)

    def committed_control_transitions(self, scope: str
                                      ) -> tuple[Mapping[str, Any], ...]:
        """Read durable committed boundary traces, including rejected candidates."""
        with self._connect() as database:
            rows = database.execute(
                "SELECT event_json FROM behavioral_control_transitions "
                "WHERE scope=? ORDER BY boundary_sequence",
                (scope,)).fetchall()
        return tuple(json.loads(row[0]) for row in rows)

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

    def latest_control_tick(self, scope: str) -> Mapping[str, Any] | None:
        """Return the durable disposition needed to settle a CSW checkpoint."""
        with self._connect() as database:
            row = database.execute(
                "SELECT tick_id, status FROM control_ticks "
                "WHERE scope=? ORDER BY created_at DESC LIMIT 1", (scope,)
            ).fetchone()
        return ({"tick_id": str(row[0]), "status": str(row[1])}
                if row is not None else None)

    def synchronize_csw_state(self, scope: str, *, expected_revision: int,
                               expected_action_index: int,
                               active_stateworks: Sequence[str]) -> None:
        """Refresh DP's CSW-owned selection projection without changing DP revision."""
        if (len(active_stateworks) > MAX_ACTIVE_STATEWORKS
                or len(set(active_stateworks)) != len(active_stateworks)):
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

    def latest_control_tick_basis(self, scope: str, *,
                                  boundary_kind: str | None = None
                                  ) -> Mapping[str, Any] | None:
        with self._connect() as database:
            if boundary_kind is None:
                row = database.execute(
                    "SELECT basis_json FROM control_ticks WHERE scope=? AND status='committed' "
                    "ORDER BY boundary_sequence DESC LIMIT 1", (scope,)).fetchone()
            else:
                row = database.execute(
                    "SELECT basis_json FROM control_ticks WHERE scope=? AND status='committed' "
                    "AND json_extract(basis_json, '$.boundary_kind')=? "
                    "ORDER BY boundary_sequence DESC LIMIT 1",
                    (scope, boundary_kind)).fetchone()
        return json.loads(row[0]) if row is not None else None

    def control_tick_input_basis_matches(self, scope: str, *,
                                         boundary_kind: str,
                                         expected_revision: int,
                                         expected_action_index: int,
                                         boundary_sequence: int,
                                         input_digest: str) -> bool:
        """Atomically validate the unchanged fast-path's state and prior basis."""
        with self._connect() as database:
            database.execute("BEGIN IMMEDIATE")
            try:
                session = database.execute(
                    "SELECT status FROM control_sessions WHERE scope=?", (scope,)
                ).fetchone()
                state = database.execute(
                    "SELECT revision, action_index FROM behavior_state WHERE scope=?",
                    (scope,),
                ).fetchone()
                pending = database.execute(
                    "SELECT 1 FROM control_ticks WHERE scope=? AND status='prepared' LIMIT 1",
                    (scope,),
                ).fetchone()
                reconciliation = database.execute(
                    "SELECT 1 FROM control_ticks AS tick "
                    "LEFT JOIN control_reconciliation_receipts AS receipt "
                    "ON receipt.scope=tick.scope AND receipt.tick_id=tick.tick_id "
                    "WHERE tick.scope=? AND tick.status='aborted' "
                    "AND json_extract(tick.detail_json, '$.requires_reconciliation')=1 "
                    "AND receipt.tick_id IS NULL LIMIT 1", (scope,),
                ).fetchone()
                outbox = database.execute(
                    "SELECT 1 FROM runtime_event_outbox WHERE scope=? LIMIT 1",
                    (scope,),
                ).fetchone()
                latest_boundary = database.execute(
                    "SELECT COALESCE(MAX(boundary_sequence), -1) "
                    "FROM control_ticks WHERE scope=?", (scope,),
                ).fetchone()
                basis = database.execute(
                    "SELECT basis_json FROM control_ticks WHERE scope=? "
                    "AND status='committed' "
                    "AND json_extract(basis_json, '$.boundary_kind')=? "
                    "ORDER BY boundary_sequence DESC LIMIT 1",
                    (scope, boundary_kind),
                ).fetchone()
                matches = bool(
                    session is not None and session[0] == "open"
                    and state is not None
                    and int(state[0]) == expected_revision
                    and int(state[1]) == expected_action_index
                    and pending is None and reconciliation is None
                    and outbox is None and latest_boundary is not None
                    and boundary_sequence > int(latest_boundary[0])
                    and basis is not None
                    and json.loads(basis[0]).get("decision_input_digest") == input_digest
                )
                database.commit()
                return matches
            except Exception:
                database.rollback()
                raise

    def acknowledge_control_reconciliation(self, scope: str) -> None:
        """Record that the host completed reconciliation for aborted partial applies."""
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as database:
            database.execute("BEGIN IMMEDIATE")
            try:
                session = database.execute(
                    "SELECT status FROM control_sessions WHERE scope=?", (scope,)
                ).fetchone()
                if session is None or session[0] != "open":
                    database.rollback()
                    raise RuntimeError("control session is unavailable")
                database.execute(
                    "INSERT OR IGNORE INTO control_reconciliation_receipts "
                    "(scope, tick_id, acknowledged_at) "
                    "SELECT tick.scope, tick.tick_id, ? FROM control_ticks AS tick "
                    "WHERE tick.scope=? AND tick.status='aborted' "
                    "AND json_extract(tick.detail_json, '$.requires_reconciliation')=1",
                    (now, scope),
                )
                database.commit()
            except Exception:
                database.rollback()
                raise

    def latest_boundary_sequence(self, scope: str) -> int:
        with self._connect() as database:
            row = database.execute(
                "SELECT COALESCE(MAX(boundary_sequence), -1) FROM control_ticks "
                "WHERE scope=?", (scope,)).fetchone()
        return int(row[0]) if row is not None else -1

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

    def record_outbox_dispatch_attempt(self, scope: str, event_id: str) -> None:
        """Persist the start of a delivery attempt without storing event content."""
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as database:
            database.execute("BEGIN IMMEDIATE")
            database.execute(
                "UPDATE runtime_event_delivery SET attempt_count=attempt_count+1, "
                "last_attempt_at=? WHERE scope=? AND event_id=? "
                "AND EXISTS (SELECT 1 FROM runtime_event_outbox "
                "WHERE scope=? AND event_id=?)",
                (now, scope, event_id, scope, event_id))
            database.commit()

    def record_outbox_dispatch_failure(self, scope: str, event_id: str,
                                       error_class: str) -> None:
        """Persist a bounded failure class; exception text may contain private data."""
        if not isinstance(error_class, str) or not error_class:
            error_class = "UnknownError"
        safe_error_class = "".join(
            char for char in error_class if char.isalnum() or char in "._")[:96]
        if not safe_error_class:
            safe_error_class = "UnknownError"
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as database:
            database.execute("BEGIN IMMEDIATE")
            database.execute(
                "UPDATE runtime_event_delivery SET last_error_class=?, "
                "last_failure_at=? WHERE scope=? AND event_id=?",
                (safe_error_class, now, scope, event_id))
            database.commit()

    def outbox_delivery_records(self, scope: str) -> tuple[dict[str, Any], ...]:
        """Read durable delivery metadata for one private control-session scope."""
        with self._connect() as database:
            rows = database.execute(
                "SELECT d.event_id, d.event_type, d.enqueued_at, d.attempt_count, "
                "d.last_attempt_at, d.last_error_class, d.last_failure_at, "
                "d.delivered_at, CASE WHEN o.event_id IS NULL THEN 0 ELSE 1 END "
                "FROM runtime_event_delivery AS d LEFT JOIN runtime_event_outbox AS o "
                "ON o.scope=d.scope AND o.event_id=d.event_id "
                "WHERE d.scope=? ORDER BY d.enqueued_at, d.event_id", (scope,)
            ).fetchall()
        return tuple({
            "event_id": str(row[0]), "event_type": str(row[1]),
            "enqueued_at": row[2], "attempt_count": int(row[3]),
            "last_attempt_at": row[4], "last_error_class": row[5],
            "last_failure_at": row[6], "delivered_at": row[7],
            "pending": bool(row[8]),
        } for row in rows)

    def outbox_delivery_status(self, scope: str, *,
                               authenticated_event_ids: Sequence[str] | None = None,
                               authenticated_event_type_counts: Mapping[str, int] | None = None
                               ) -> dict[str, Any]:
        """Summarize committed, pending, delivered, and authenticated events."""
        with self._connect() as database:
            rows = database.execute(
                "SELECT d.event_type, COUNT(*), "
                "SUM(CASE WHEN d.delivered_at IS NULL THEN 0 ELSE 1 END), "
                "SUM(CASE WHEN o.event_id IS NULL THEN 0 ELSE 1 END) "
                "FROM runtime_event_delivery AS d "
                "LEFT JOIN runtime_event_outbox AS o "
                "ON o.scope=d.scope AND o.event_id=d.event_id "
                "WHERE d.scope=? GROUP BY d.event_type ORDER BY d.event_type",
                (scope,)).fetchall()
            latest_failure = database.execute(
                "SELECT last_failure_at, last_error_class "
                "FROM runtime_event_delivery WHERE scope=? "
                "AND last_failure_at IS NOT NULL "
                "ORDER BY last_failure_at DESC LIMIT 1", (scope,)).fetchone()
            last_success = database.execute(
                "SELECT MAX(delivered_at) FROM runtime_event_delivery WHERE scope=?",
                (scope,)).fetchone()
        by_type: dict[str, dict[str, int]] = {}
        for event_type, committed, delivered, pending in rows:
            by_type[str(event_type)] = {
                "committed": int(committed), "enqueued": int(committed),
                "delivered": int(delivered), "authenticated": 0,
                "pending": int(pending),
            }
        authenticated = set(authenticated_event_ids or ())
        if authenticated_event_ids is not None:
            records = self.outbox_delivery_records(scope)
            for item in records:
                if item["event_id"] in authenticated:
                    by_type[item["event_type"]]["authenticated"] += 1
        elif authenticated_event_type_counts is not None:
            for event_type, counts in by_type.items():
                observed = authenticated_event_type_counts.get(event_type, 0)
                if type(observed) is int and observed >= 0:
                    counts["authenticated"] = min(counts["committed"], observed)
        total = sum(item["committed"] for item in by_type.values())
        delivered = sum(item["delivered"] for item in by_type.values())
        pending = sum(item["pending"] for item in by_type.values())
        authenticated_total = sum(item["authenticated"] for item in by_type.values())
        return {
            "committed_count": total,
            # Enqueue and behavior commit share one SQLite transaction.
            "enqueued_count": total,
            "delivered_count": delivered,
            "authenticated_count": authenticated_total,
            "pending_count": pending,
            "last_dispatch_failure": ({
                "at": latest_failure[0], "error_class": latest_failure[1],
            } if latest_failure is not None else None),
            "last_successful_delivery_at": (last_success[0]
                                             if last_success is not None else None),
            "authenticated_status_available": (
                authenticated_event_ids is not None
                or authenticated_event_type_counts is not None),
            "by_event_type": by_type,
        }

    def acknowledge_outbox_event(self, scope: str, event_id: str) -> None:
        """Remove an event only after the idempotent event sink accepts it."""
        with self._connect() as database:
            database.execute("BEGIN IMMEDIATE")
            now = datetime.now(timezone.utc).isoformat()
            database.execute(
                "UPDATE runtime_event_delivery SET delivered_at=COALESCE(delivered_at, ?) "
                "WHERE scope=? AND event_id=?",
                (now, scope, event_id))
            database.execute(
                "DELETE FROM runtime_event_outbox WHERE scope=? AND event_id=?",
                (scope, event_id))
            database.commit()


__all__ = ["BehaviorStateStore", "RuntimeOutboxEvent", "RuntimeStateRecord"]
