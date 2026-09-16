#!/usr/bin/env python3
"""Validate DigitalPsychology behavior-event fixtures against the schema,
including format checking (date-time) via Draft202012Validator, duplicate-ID
rejection, and reference validation."""
import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.feedback_loop import EventSchema, load_events  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "schemas" / "behavior-event.schema.json"
FIXTURES = ROOT / "fixtures" / "events.ndjson"


def main() -> int:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    if not FIXTURES.exists():
        print(f"Missing fixture file: {FIXTURES}")
        return 1
    errors = []
    count = 0
    seen_ids = set()
    for lineno, line in enumerate(FIXTURES.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        count += 1
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"{FIXTURES.name}:{lineno}: invalid JSON: {exc}")
            continue
        if event.get("event_id") in seen_ids:
            errors.append(f"{FIXTURES.name}:{lineno}: duplicate event_id {event.get('event_id')!r}")
        seen_ids.add(event.get("event_id"))
        try:
            Draft202012Validator(schema, format_checker=FormatChecker()).validate(event)
        except Exception as exc:
            errors.append(f"{FIXTURES.name}:{lineno}: {exc.message}")
    if errors:
        print("Fixture validation failed:")
        for err in errors:
            print(f"- {err}")
        return 1

    # dangling reference detection: invalidates/supersedes/evidence_refs must
    # point at real events in the same fixture set
    try:
        events = load_events(FIXTURES)
    except Exception as exc:
        print(f"Loader error on fixtures: {exc}")
        return 1
    by_id = {e.event_id: e for e in events}
    if len(by_id) != len(events):
        print("Duplicate event IDs detected in fixture stream")
        return 1
    dangling = []
    for ev in events:
        for ref in ev.evidence_refs:
            if ref not in by_id:
                dangling.append(f"{ev.event_id} evidence_ref {ref}")
        for field in ("invalidates", "supersedes", "parent_event"):
            ref = getattr(ev, field)
            if ref and ref not in by_id:
                dangling.append(f"{ev.event_id} {field} {ref}")
    if dangling:
        print("Dangling references:")
        for d in dangling:
            print(f"- {d}")
        return 1

    print(f"Validation passed: {count} events match schemas/behavior-event.schema.json (format-checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
