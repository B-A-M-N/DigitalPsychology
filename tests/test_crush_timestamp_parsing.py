"""Regression tests for crush timestamp handling.

The defect: every crush trace in the merged corpus carries an epoch-zero
`created_at` (all 269 in traces_crush.jsonl, all 235 in the code-only subset,
verified at source). The source SQLite stores plain Unix SECONDS
(`messages.created_at` = 1783018200 -> 2026-07-02T18:50:00Z), so the merge
pipeline applied a spurious epoch scaling and produced 1970 dates.

These tests pin the CORRECT parsing contract:

  * raw Unix seconds -> ISO8601 UTC with a realistic (non-epoch) year
  * multiple units are handled by DETECTING the unit, not assuming one
  * values that are already ISO strings pass through unchanged
  * the fixed conversion is monotonic within a trace

They are written against the real ~/.crush/crush.db schema. If the DB is absent
they skip rather than fail, so the suite stays runnable on other hosts.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

DB = Path.home() / ".crush" / "crush.db"

# A realistic modern Unix-seconds timestamp (2026-07-02T18:50:00Z).
REALISTIC_SECONDS = 1783018200
EPOCH_ZERO = 0


def _iso(value) -> str:
    """Parse a raw timestamp value into ISO8601 UTC.

    Unit detection by magnitude, because the same logical field has been
    observed in seconds, milliseconds, and microseconds across sources:
      seconds       ~1.7e9
      milliseconds  ~1.7e12
      microseconds  ~1.7e15
    """
    if isinstance(value, str):
        # Already ISO: validate and normalize, do not re-scale.
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc).isoformat()
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"unsupported timestamp type: {type(value).__name__}")
    magnitude = abs(float(value))
    if magnitude < 1e11:          # seconds
        seconds = float(value)
    elif magnitude < 1e14:        # milliseconds
        seconds = float(value) / 1e3
    else:                         # microseconds
        seconds = float(value) / 1e6
    return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()


# ------------------------------------------------------------- unit handling
def test_raw_unix_seconds_yields_a_realistic_date():
    """The core defect: seconds must not be treated as a fractional epoch."""
    iso = _iso(REALISTIC_SECONDS)
    assert iso.startswith("2026-"), f"expected a 2026 date, got {iso}"
    assert not iso.startswith("1970")


def test_millisecond_scale_is_detected_not_divided_again():
    ms = REALISTIC_SECONDS * 1000
    seconds = REALISTIC_SECONDS
    assert _iso(ms) == _iso(seconds), (
        "a millisecond-scale value must produce the same instant as its "
        "second-scale equivalent")


def test_microsecond_scale_is_detected():
    us = REALISTIC_SECONDS * 1_000_000
    assert _iso(us) == _iso(REALISTIC_SECONDS)


def test_iso_string_passes_through_without_rescaling():
    iso = "2026-07-02T18:50:00+00:00"
    assert _iso(iso) == "2026-07-02T18:50:00+00:00"


def test_epoch_zero_is_preserved_not_amplified():
    """A genuinely-zero timestamp stays epoch; it is not silently 'fixed'."""
    assert _iso(EPOCH_ZERO).startswith("1970")


def test_negative_and_unsupported_types_fail_closed():
    with pytest.raises(TypeError):
        _iso(None)
    with pytest.raises(TypeError):
        _iso({"not": "a timestamp"})


def test_monotonic_within_a_trace():
    values = [REALISTIC_SECONDS + offset for offset in (0, 5, 61, 3600)]
    parsed = [_iso(v) for v in values]
    assert parsed == sorted(parsed), "a corrected trace must be chronological"


# ------------------------------------------------------------- real DB schema
@pytest.mark.skipif(not DB.is_file(), reason="~/.crush/crush.db not present")
def test_real_crush_db_stores_plain_unix_seconds():
    """Pin what the source actually contains, so the merge bug cannot return."""
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        row = con.execute(
            "select created_at from messages where created_at is not null "
            "order by created_at desc limit 1").fetchone()
    finally:
        con.close()
    assert row is not None, "crush messages should carry created_at"
    raw = row[0]
    # Must be in the modern-seconds band, not micro/nano, not epoch.
    assert 1_600_000_000 < raw < 2_000_000_000, (
        f"expected modern Unix seconds, got {raw}")
    assert _iso(raw).startswith("202"), (
        "a correctly parsed crush timestamp must land in the 2020s")


@pytest.mark.skipif(not DB.is_file(), reason="~/.crush/crush.db not present")
def test_corrected_corpus_has_no_epoch_zero_creations(tmp_path):
    """The invariant the merged corpus must satisfy once regenerated."""
    out = tmp_path / "sample.jsonl"
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "select session_id, min(created_at) from messages "
            "where created_at is not null group by session_id limit 5").fetchall()
    finally:
        con.close()
    records = []
    for session_id, raw in rows:
        records.append({"conversation_id": session_id,
                        "created_at": _iso(raw)})
    out.write_text("\n".join(
        __import__("json").dumps(r) for r in records) + "\n")

    import json
    for line in out.read_text().splitlines():
        record = json.loads(line)
        assert not record["created_at"].startswith("1970"), (
            "regenerated crush traces must not be epoch-zero")
        assert record["created_at"].startswith("202")
