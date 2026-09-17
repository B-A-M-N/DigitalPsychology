#!/usr/bin/env python3
"""Guard-effectiveness monitor with statistically valid decay findings."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Tuple

ROOT = Path(__file__).resolve().parents[1]


def _state_path() -> Path:
    base = os.environ.get("DIGITALPSYCHOLOGY_STATE_ROOT")
    if base:
        return Path(base) / "guard-monitor.json"
    xdg = os.environ.get("XDG_STATE_HOME")
    return (Path(xdg) if xdg else Path.home() / ".local" / "state") / "digitalpsychology" / "guard-monitor.json"


def _load_state(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"revision": 0, "flagged": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"revision": 0, "flagged": []}


def _state_revision(path: Path) -> int:
    state = _load_state(path)
    return int(state.get("revision", 0) or 0)


def _save_state(path: Path, state: Dict[str, Any], *, expected_revision: int | None = None) -> None:
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.lock")
    lock_path.touch(mode=0o600, exist_ok=True)
    with lock_path.open("r+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        current = _load_state(path)
        current_revision = int(current.get("revision", 0) or 0)
        if expected_revision is not None and current_revision != expected_revision:
            raise RuntimeError("guard monitor state changed concurrently; retry from a fresh snapshot")
        state = dict(state)
        state["revision"] = current_revision + 1
        fd, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=".monitor-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(state, handle, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _window_time(profile: Dict[str, Any]) -> float:
    raw = profile.get("window_end") or profile.get("generated_at") or ""
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError, OverflowError):
        return float("-inf")


def _profile_key(profile: Dict[str, Any]) -> Tuple[str, str, str, str, str, str, str, str]:
    context = profile.get("task_context") or {}
    guard_key = str(profile.get("guard_key") or profile.get("guard") or "")
    return (
        guard_key,
        str(context.get("model") or ""),
        str(context.get("harness") or ""),
        str(context.get("environment") or ""),
        str(context.get("task_family") or context.get("task_shape") or ""),
        str(profile.get("cohort") or ""),
        str(context.get("comparison_context_hash") or ""),
        str(context.get("window_group") or ""),
    )


def _window_key(profile: Dict[str, Any]) -> str:
    context = profile.get("task_context") or {}
    explicit = profile.get("window_id") or context.get("window_id")
    if explicit:
        return str(explicit)
    return str(profile.get("window_end") or profile.get("generated_at") or "")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile_path", nargs="?", type=Path,
                        default=ROOT / "profiles.json")
    parser.add_argument("--guard-pack", type=Path,
                        default=ROOT / "compiled-guard-pack.json")
    parser.add_argument("--min-sample", type=int, default=3)
    parser.add_argument("--decay-threshold", type=float, default=0.5)
    parser.add_argument("--restore-threshold", type=float, default=0.7)
    parser.add_argument("--state", type=Path, default=_state_path())
    args = parser.parse_args()

    if not args.profile_path.exists():
        print(f"No profile data at {args.profile_path}")
        return 1
    profiles = json.loads(args.profile_path.read_text(encoding="utf-8")).get("profiles", [])
    findings: List[str] = []
    by_guard: Dict[str, List[Dict[str, Any]]] = {}
    for profile in profiles:
        key = str(profile.get("guard_key") or profile.get("guard") or "")
        by_guard.setdefault(key, []).append(profile)

    state = _load_state(args.state)
    flagged_once = set(state.get("flagged", []))
    strata: Dict[Tuple[str, str, str, str, str, str, str, str], List[Dict[str, Any]]] = {}
    for profile in profiles:
        if profile.get("cohort") != "treatment" or profile.get("rate") is None:
            continue
        strata.setdefault(_profile_key(profile), []).append(profile)
    for stratum, series in strata.items():
        series = sorted(series, key=lambda item: (_window_key(item), _window_time(item)))
        latest_window = _window_key(series[-1])
        window = [p for p in series if _window_key(p) == latest_window]
        applicable = sum(int(p.get("applicable_episodes", 0) or 0) for p in window)
        if applicable < args.min_sample:
            continue
        passing = sum(int(p.get("passing_episodes", 0) or 0) for p in window)
        rate = passing / applicable if applicable else 0.0
        ci_low = min((p.get("ci_lower_bound") for p in window
                      if p.get("ci_lower_bound") is not None), default=None)
        ci_high = max((p.get("ci_upper_bound") for p in window
                       if p.get("ci_upper_bound") is not None), default=None)
        state_key = "|".join(str(item) for item in stratum)
        if rate < args.decay_threshold:
            flagged_once.add(state_key)
            if ci_high is not None and ci_high < args.restore_threshold:
                findings.append(
                    f"guard {stratum[0]} [{state_key}]: rate {rate:.3f} "
                    f"(ci {ci_low}-{ci_high}) below {args.decay_threshold} "
                    f"with {applicable} applicable episodes in the evaluated window")
        elif state_key in flagged_once:
            if rate >= args.restore_threshold:
                flagged_once.discard(state_key)
                findings.append(f"guard {stratum[0]} [{state_key}]: recovered at rate {rate:.3f}")
            else:
                findings.append(
                    f"guard {stratum[0]} [{state_key}]: below restore threshold "
                    f"{args.restore_threshold} (rate {rate:.3f})")

    pack = json.loads(args.guard_pack.read_text(encoding="utf-8")) if args.guard_pack.exists() else {}
    scopes = {}
    guard_refs = {}
    for guard in pack.get("guards", []):
        key = guard.get("key") or guard["id"]
        scopes[key] = guard.get("scope", {})
        guard_refs[guard["id"]] = key
        guard_refs[guard.get("family", guard["id"])] = key
    observed: Dict[str, Dict[str, set]] = {}
    for profile in profiles:
        context = profile.get("task_context") or {}
        model = context.get("model")
        harness = context.get("harness")
        if not model and not harness:
            continue
        key = str(profile.get("guard_key") or profile.get("guard") or "")
        bucket = observed.setdefault(key, {"models": set(), "harnesses": set()})
        if model:
            bucket["models"].add(model)
        if harness:
            bucket["harnesses"].add(harness)
    for guard, seen in observed.items():
        scope = scopes.get(guard_refs.get(guard, guard), {})
        allowed_models = set(scope.get("models") or [])
        allowed_harnesses = set(scope.get("harnesses") or [])
        if allowed_models and seen["models"] - allowed_models:
            findings.append(f"guard {guard}: observed models outside validated scope")
        if allowed_harnesses and seen["harnesses"] - allowed_harnesses:
            findings.append(f"guard {guard}: observed harnesses outside validated scope")

    for guard in pack.get("guards", []):
        for old_id in guard.get("supersedes", []):
            old_ref = guard_refs.get(old_id, old_id)
            new_ref = guard_refs.get(guard["id"], guard["id"])
            old = [p for p in by_guard.get(old_ref, by_guard.get(old_id, []))
                   if p.get("rate") is not None and p.get("cohort") == "treatment"]
            new = [p for p in by_guard.get(new_ref, by_guard.get(guard["id"], []))
                   if p.get("rate") is not None and p.get("cohort") == "treatment"]
            if not old or not new:
                continue
            latest_old = max(_window_time(p) for p in old)
            latest_new = max(_window_time(p) for p in new)
            old = [p for p in old if _window_time(p) == latest_old]
            new = [p for p in new if _window_time(p) == latest_new]
            old_total = sum(p.get("applicable_episodes", 0) for p in old)
            new_total = sum(p.get("applicable_episodes", 0) for p in new)
            if not old_total or not new_total:
                continue
            old_rate = sum(p["rate"] * p.get("applicable_episodes", 0) for p in old) / old_total
            new_rate = sum(p["rate"] * p.get("applicable_episodes", 0) for p in new) / new_total
            if new_rate < old_rate:
                findings.append(
                    f"replacement defect: {guard['id']} ({new_rate:.3f}) less effective "
                    f"than superseded {old_id} ({old_rate:.3f})")

    try:
        _save_state(args.state, {"flagged": sorted(flagged_once),
                                 "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")},
                    expected_revision=int(state.get("revision", 0) or 0))
    except RuntimeError as exc:
        print(json.dumps({"findings": [str(exc)], "state": state}, indent=2))
        return 1
    print(json.dumps({"findings": findings, "state": {"flagged": sorted(flagged_once)}}, indent=2))
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
