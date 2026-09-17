#!/usr/bin/env python3
"""Consume independent post-deployment trajectories for routing-profile expiry."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.routing_lifecycle import RoutingLifecycleStore  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", type=Path)
    parser.add_argument("observations", type=Path,
                        help="JSON array of host-bound trajectory observations")
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    profile = json.loads(args.profile.read_text(encoding="utf-8"))
    observations = json.loads(args.observations.read_text(encoding="utf-8"))
    if not isinstance(observations, list):
        raise SystemExit("observations must be a JSON array")
    updated = RoutingLifecycleStore(args.state).observe(profile, observations)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(updated, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": updated.get("status"),
                      "profile_hash": updated.get("profile_hash")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
