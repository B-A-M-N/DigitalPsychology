"""SQLite-backed revisioned DP behavior state and intervention receipts."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
from contextlib import contextmanager
from typing import Mapping

from ..behavior_state import BehaviorState


@dataclass(frozen=True)
class RuntimeStateRecord:
    state: BehaviorState
    action_index: int
    application_receipts: Mapping[str, tuple[str, ...]]


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
    def _read_receipts(database: sqlite3.Connection, scope: str
                       ) -> dict[str, tuple[str, ...]]:
        rows = database.execute(
            "SELECT intervention_id, directive_ids_json FROM intervention_receipts "
            "WHERE scope=?", (scope,)).fetchall()
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
            receipts = self._read_receipts(database, scope)
        if row is None:
            return RuntimeStateRecord(initial or BehaviorState(), -1, receipts)
        revision, action_index, encoded = row
        state = BehaviorState.from_mapping(json.loads(encoded))
        if state.revision != int(revision):
            raise RuntimeError("persisted DP state revision does not match its row")
        return RuntimeStateRecord(state, int(action_index), receipts)

    def save(self, scope: str, *, expected_revision: int,
             expected_action_index: int, action_index: int, state: BehaviorState,
             application_receipts: Mapping[str, tuple[str, ...]] | None = None) -> None:
        if state.revision != expected_revision + 1:
            raise ValueError("saved DP behavior state must advance exactly one revision")
        if action_index < 0:
            raise ValueError("host action index must be non-negative")
        receipts = dict(application_receipts or {})
        if len(receipts) > 16:
            raise ValueError("intervention application receipt count exceeds its bound")
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
            database.commit()


__all__ = ["BehaviorStateStore", "RuntimeStateRecord"]
