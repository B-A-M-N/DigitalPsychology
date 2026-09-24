from datetime import datetime, timezone, timedelta

from lib.feedback_loop import Event, SQLiteSink


def test_purge_preserves_aggregate_evidence(tmp_path):
    path = tmp_path / "events.sqlite3"
    sink = SQLiteSink(path)
    old = Event(event_id="old", timestamp="2020-01-01T00:00:00Z", task_id="t", agent_id="a", category="observation", event_type="routine", namespace_id="n", application_id="app", application_instance_id="i", behavioral_subject="s", payload={"summary": "raw"})
    new = Event(event_id="new", timestamp="2026-01-20T00:00:00Z", task_id="t", agent_id="a", category="observation", event_type="routine", namespace_id="n", application_id="app", application_instance_id="i", behavioral_subject="s", payload={"summary": "raw"})
    sink.emit(old); sink.emit(new)
    result = sink.purge_expired(retention_days=30, now=datetime(2026, 2, 1, tzinfo=timezone.utc))
    assert result["deleted_events"] == 1 and result["aggregate_rows"] == 1
    assert sink._conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
    assert sink._conn.execute("SELECT event_count FROM retention_aggregates").fetchone()[0] == 1
    sink.close()
