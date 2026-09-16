#!/usr/bin/env python3
"""Run the runtime-neutral guard compiler conformance fixture.

The fixture is deliberately JSON-only so CognitiveFrameWorks can consume the
same expected cases without importing DigitalPsychology Python code.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.feedback_loop import Guard, GuardCompiler  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    fixture = json.loads((ROOT / "fixtures" / "guard-conformance.json").read_text(encoding="utf-8"))
    guards = [Guard(**{key: value for key, value in raw.items()
                       if key not in {"id", "key", "family", "version"}},
                    id=raw["id"], family=raw.get("family"), version=raw.get("version", "1"))
              for raw in fixture["guards"]]
    failures = []
    for case in fixture["cases"]:
        result = GuardCompiler(token_budget=200).compile(
            guards,
            case["domains"], case["shapes"],
            task_id=case["task_id"], model=case.get("model"),
            harness=case.get("harness"), trigger=case.get("trigger"),
            stateworks=case.get("stateworks"))
        actual = result.get("active", [])
        if actual != case["expected_active"]:
            failures.append(f"{case['name']}: expected {case['expected_active']}, got {actual}")
    if failures:
        print("guard conformance failed")
        print("\n".join(f"- {failure}" for failure in failures))
        return 1
    print(f"guard conformance passed ({len(fixture['cases'])} cases)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
