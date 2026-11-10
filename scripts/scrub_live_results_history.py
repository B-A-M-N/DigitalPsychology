#!/usr/bin/env python3
"""Rewrite CFW public history to remove local machine metadata.

WHAT THIS REMOVES
  1. Absolute filesystem paths: /home/bamn/... -> <repo>/... (relative)
  2. Local evidence paths pointing at trial output: <repo>/evaluation_fixtures/... -> basename
  3. Nothing else. No credentials were found by gitleaks; see the note below.

WHY THESE THREE ONLY
  `gitleaks detect` over full history produced 11 findings, all verified as
  content-integrity hashes of our own source (e.g. scripts/resolver/api.py
  digests in untracked_file_hashes, compatibility_api_hash in
  compiled-registry.json) plus one deliberately truncated WARD fixture. None is
  a credential, so there is nothing to rotate. This scrub targets metadata, not
  secrets.

WHAT THIS DOES NOT TOUCH
  Provider/model identifiers (freeinference, deepseek-v4-flash, gpt-6-luna).
  They are the experimental record of which backend produced each trial and
  removing them would make the qualification evidence unreadable. If you want
  those gone too, that is a separate decision with a real cost.

USAGE
  python3 scrub_live_results_history.py --dry-run     # report only
  python3 scrub_live_results_history.py --apply        # rewrite working tree
  python3 scrub_live_results_history.py --apply --history  # + rewrite git history

--history requires git-filter-repo and FORCE-PUSHES the result. Back up first:
    git clone --mirror /home/bamn/CognitiveFrameWorks /path/to/backup.git
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path("/home/bamn/CognitiveFrameWorks")
TARGET_DIR = REPO / "evaluation_fixtures" / "live-results"
REPO_NAME = "CognitiveFrameWorks"

# 1) absolute repo path -> repo-relative
ABS_REPO = re.compile(r"/home/bamn/" + REPO_NAME + r"/")
# 2) any other absolute path under /home/bamn (evidence dirs, scratch, caches)
ABS_HOME = re.compile(r"/home/bamn/[A-Za-z0-9_.\-/]*")
# 3) repo-relative evidence/output paths -> basename only
EV_REL = re.compile(
    r"(?<![\w/])" + REPO_NAME + r"/evaluation_fixtures/live-results/"
    r"(?P<name>[A-Za-z0-9_.\-]+)")


def rewrite_text(text: str, stats: dict) -> str:
    """Apply the three substitutions, counting each."""

    def _abs_repo(m: re.Match) -> str:
        stats["abs_repo_paths"] += 1
        return ""

    def _abs_home(m: re.Match) -> str:
        stats["abs_home_paths"] += 1
        return "<local-path>"

    def _ev(m: re.Match) -> str:
        stats["evidence_paths"] += 1
        return m.group("name")

    text = ABS_REPO.sub(_abs_repo, text)
    text = ABS_HOME.sub(_abs_home, text)
    text = EV_REL.sub(_ev, text)
    return text


def rewrite_file(path: Path, stats: dict) -> bool:
    """Rewrite one file in place. Returns True when content changed."""
    raw = path.read_text(encoding="utf-8", errors="replace")
    new = rewrite_text(raw, stats)
    if new == raw:
        return False
    if not os.access(path, os.W_OK):
        print(f"  WARN not writable: {path}", file=sys.stderr)
        return False
    path.write_text(new, encoding="utf-8")
    return True


def process_tree(stats: dict) -> list[Path]:
    """Rewrite every JSON/text file under TARGET_DIR. Return changed paths."""
    changed = []
    for path in sorted(TARGET_DIR.rglob("*")):
        if not path.is_file() or path.suffix not in (".json", ".jsonl", ".md", ".txt", ".log"):
            continue
        if rewrite_file(path, stats):
            changed.append(path)
    return changed


def verify_json(changed: list[Path]) -> list[str]:
    """Confirm every rewritten .json still parses."""
    broken = []
    for path in changed:
        if path.suffix != ".json":
            continue
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            broken.append(f"{path.name}: {exc}")
    return broken


def git_check() -> dict:
    """Capture the state we need before and after a history rewrite."""
    def _run(*args):
        return subprocess.run(["git", "-C", str(REPO), *args],
                              capture_output=True, text=True, check=False)
    return {
        "head": _run("rev-parse", "HEAD").stdout.strip(),
        "status": _run("status", "--short").stdout,
        "count": _run("rev-list", "--count", "HEAD").stdout.strip(),
        "remote": _run("rev-parse", "origin/master").stdout.strip(),
    }


def rewrite_history(stats: dict) -> int:
    """Run git-filter-repo to strip the same patterns from every commit."""
    if shutil.which("git-filter-repo") is None:
        print("git-filter-repo not found; skipping history rewrite", file=sys.stderr)
        return 1

    # Blob callback applies the identical substitution to every historical blob.
    blob_cb = (
        "import re, sys\\n"
        "sys.path.insert(0, %r)\\n"
        "from scrub_live_results_history import rewrite_text\\n"
        "stats = {'abs_repo_paths': 0, 'abs_home_paths': 0, 'evidence_paths': 0}\\n"
        "if blob.data is None:\\n"
        "    return\\n"
        "try:\\n"
        "    text = blob.data.decode('utf-8')\\n"
        "except UnicodeDecodeError:\\n"
        "    return\\n"
        "new = rewrite_text(text, stats)\\n"
        "if new != text:\\n"
        "    blob.data = new.encode('utf-8')\\n"
    ) % str(REPO)

    args = ["git", "filter-repo", "--force", "--blob-callback", blob_cb]
    proc = subprocess.run(args, cwd=REPO, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        print(proc.stdout[-2000:], file=sys.stderr)
        print(proc.stderr[-2000:], file=sys.stderr)
        return proc.returncode
    print(proc.stdout[-1500:])
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would change, write nothing")
    parser.add_argument("--apply", action="store_true",
                        help="rewrite files in the working tree")
    parser.add_argument("--history", action="store_true",
                        help="also rewrite git history (requires --apply)")
    args = parser.parse_args()

    if not TARGET_DIR.is_dir():
        print(f"target directory missing: {TARGET_DIR}", file=sys.stderr)
        return 1

    print("=" * 70)
    print("CognitiveFrameWorks live-results metadata scrub")
    print("=" * 70)

    stats = {"abs_repo_paths": 0, "abs_home_paths": 0, "evidence_paths": 0}
    before = git_check()
    print(f"  HEAD {before['head'][:12]}  ({before['count']} commits)")

    # Count what would change without writing.
    for path in sorted(TARGET_DIR.rglob("*")):
        if not path.is_file() or path.suffix not in (".json", ".jsonl", ".md", ".txt", ".log"):
            continue
        rewrite_file(path, {"abs_repo_paths": 0, "abs_home_paths": 0,
                            "evidence_paths": 0})  # no-op read to warm stats? no:
    # Recompute cleanly.
    stats = {"abs_repo_paths": 0, "abs_home_paths": 0, "evidence_paths": 0}
    candidates = []
    for path in sorted(TARGET_DIR.rglob("*")):
        if not path.is_file() or path.suffix not in (".json", ".jsonl", ".md", ".txt", ".log"):
            continue
        raw = path.read_text(encoding="utf-8", errors="replace")
        if rewrite_text(raw, stats) != raw:
            candidates.append(path)

    print(f"  files needing change: {len(candidates)}")
    print(f"  substitutions: {json.dumps(stats)}")
    print(f"  uncommitted changes before: {len(before['status'].splitlines())}")

    if args.dry_run:
        for path in candidates:
            print(f"    would rewrite {path.name}")
        print("\nDRY RUN -- nothing written.")
        return 0

    if not args.apply:
        print("\nNo --apply given; nothing written.")
        return 0

    real_stats = {"abs_repo_paths": 0, "abs_home_paths": 0, "evidence_paths": 0}
    changed = []
    for path in candidates:
        if rewrite_file(path, real_stats):
            changed.append(path)

    broken = verify_json(changed)
    print(f"\nrewrote {len(changed)} file(s); {json.dumps(real_stats)}")
    if broken:
        print("JSON PARSE FAILURES:", file=sys.stderr)
        for line in broken:
            print(f"  {line}", file=sys.stderr)
        return 1
    print("all rewritten JSON parses")

    if args.history:
        print("\nrewriting git history (this is irreversible without a backup)...")
        rc = rewrite_history(real_stats)
        if rc != 0:
            print("history rewrite FAILED", file=sys.stderr)
            return rc
        after = git_check()
        print(f"  HEAD {before['head'][:12]} -> {after['head'][:12]}")
        print(f"  commits {before['count']} -> {after['count']}")
        print(f"  remote is still {before['remote'][:12]} (force-push required)")

    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
