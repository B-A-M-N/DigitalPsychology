#!/usr/bin/env python3
"""Run the operational SQLite retention purge regression."""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
result = subprocess.run([sys.executable, "-m", "pytest", "-q", str(ROOT / "tests" / "test_retention.py")], cwd=ROOT)
raise SystemExit(result.returncode)
