#!/usr/bin/env python3
"""Run the mandatory real CFW + CSW + private DP MCP qualification test."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


DP = Path(__file__).resolve().parents[1]
CFW = Path(os.environ.get("COGNITIVE_FRAMEWORKS_ROOT", "/home/bamn/CognitiveFrameWorks"))
CSW = Path(os.environ.get("COGNITIVE_STATEWORK_ROOT", "/home/bamn/CognitiveStateWork"))
TARGET = CFW / "tests" / "test_current_cross_repo_wire_contracts.py"


def main() -> int:
    missing = [str(path) for path in (DP, CFW, CSW, TARGET) if not path.exists()]
    if missing:
        print("RUNTIME_TRIAD=BLOCKED missing=" + ", ".join(missing), file=sys.stderr)
        return 2
    env = dict(os.environ)
    env["COGNITIVE_STATEWORK_ROOT"] = str(CSW)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(TARGET),
         "-k", "runtime_triad_private_mcp_qualification_uses_all_three_authorities"],
        cwd=CFW, env=env, check=False)
    if result.returncode == 0:
        print("RUNTIME_TRIAD=PASS")
    else:
        print(f"RUNTIME_TRIAD=FAIL exit={result.returncode}")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
