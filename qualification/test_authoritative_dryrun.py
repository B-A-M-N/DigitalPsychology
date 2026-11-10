"""Pytest wrapper for the AUTHORITATIVE upstream dry-run.

Runs the real `laya.train.dry_run` in a subprocess against the exported splits.
Skips (rather than fails) when laya >= 0.4.6 is not importable, so the pinned
`.venv-laya` (0.3.22) stays green; the authority is exercised by running the
suite in the isolated venv.

The subprocess form is deliberate: it isolates the interpreter so the test cannot
import the wrong laya, and it makes the authority reproducible as a one-line
command rather than a remembered sequence of imports.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
AUTHORITY = REPO / "qualification" / "authoritative_dryrun.py"
EXPORT_DIR = "/tmp/dp-audit/laya-train"


def _laya_python() -> str | None:
    """An interpreter that can import laya >= 0.4.6, or None.

    Prefers an explicit override, then the isolated venv created for the
    training work, then the current interpreter.
    """
    import os

    # Compare (major, minor) first, then patch only when the release is exactly
    # 0.4 -- comparing a truncated tuple against a 3-tuple silently fails.
    probe = (
        "import importlib.metadata as md\n"
        "import laya.train\n"
        "assert hasattr(laya.train, 'dry_run'), 'no train module'\n"
        "v = tuple(int(p) for p in md.version('laya').split('.'))\n"
        "ok = v[:2] > (0, 4) or (v[:2] == (0, 4) and (len(v) < 3 or v[2] >= 6))\n"
        "assert ok, f'need laya 0.4.6+, have {v}'\n"
    )
    candidates = [os.environ.get("LAYA_AUTHORITY_PYTHON", ""),
                  "/tmp/dp-audit/venv-laya047/bin/python", sys.executable]
    for candidate in candidates:
        if not candidate or not Path(candidate).exists():
            continue
        try:
            proc = subprocess.run([candidate, "-c", probe],
                                  capture_output=True, timeout=180, check=False)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if proc.returncode == 0:
            return candidate
    return None


def _last_json_line(text: str) -> str:
    """The last line that parses as JSON, ignoring any progress output.

    Upstream writes its own progress lines to stdout before our payload, so the
    last JSON object is the answer and anything before it is noise.
    """
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            json.loads(line)
        except json.JSONDecodeError:
            continue
        return line
    raise AssertionError(f"no JSON in output:\n{text[-800:]}")


def _exported(directory: Path) -> bool:
    return all((directory / f"{split}.jsonl").exists()
               for split in ("train", "calibration", "holdout"))


def test_upstream_dry_run_accepts_every_exported_row():
    """The authority: real `laya.train.dry_run` must skip nothing.

    `skipped: {}` on every split means the exported rows are in the schema the
    real training loader reads. The local precheck is a convenience; this is the
    check that can actually be wrong about upstream.
    """
    python = _laya_python()
    if python is None:
        import pytest
        pytest.skip("laya >= 0.4.6 not importable in any available interpreter")

    if not _exported(Path(EXPORT_DIR)):
        # Export first so the test is self-contained.
        proc = subprocess.run(
            [sys.executable, "-m", "qualification.training_export"],
            cwd=REPO, capture_output=True, timeout=600, check=False)
        assert proc.returncode == 0, proc.stderr.decode()[-800:]

    proc = subprocess.run(
        [python, str(AUTHORITY), EXPORT_DIR],
        cwd=REPO, capture_output=True, timeout=900, check=False)
    out = proc.stdout.decode()
    assert proc.returncode == 0, (
        f"upstream dry_run rejected the export:\n{out}\n{proc.stderr.decode()[-800:]}")

    # Every split must report zero skipped, and rows must equal valid items.
    reports = [json.loads(line) for line in out.splitlines()
               if line.strip().startswith("{")]
    by_split = {r["split"]: r for r in reports if "split" in r}
    assert set(by_split) == {"train", "calibration", "holdout"}, (
        f"missing splits: {sorted(by_split)}")
    for split, report in by_split.items():
        assert report["skipped"] == {}, (
            f"{split}: upstream skipped rows: {report['skipped']}")
        assert report["rows_read"] == report["valid_items"], (
            f"{split}: {report['rows_read']} rows read but only "
            f"{report['valid_items']} valid")
    verdict = [r for r in reports if "verdict" in r]
    assert verdict and verdict[-1]["verdict"] == "accept", reports[-1]


def test_upstream_dry_run_rejects_the_internal_shape():
    """The defect this authority exists to catch, proven against upstream.

    A row written in the internal `{t, crit, ins}` shape must be REJECTED by
    upstream. Upstream's actual behaviour is not a skip count but a raise:
    `items_from_rows` silently drops the row, leaving zero items, and `dry_run`
    then fails with `ValueError: n_items must be a positive integer, got 0`.

    Asserting the raise (rather than a `skipped` dict) matters because the skip
    dict is never produced on this path -- an assertion shaped like the earlier
    one passes vacuously for the wrong reason.
    """
    python = _laya_python()
    if python is None:
        import pytest
        pytest.skip("laya >= 0.4.6 not importable in any available interpreter")

    import tempfile

    from qualification.contrastive_fixtures import generate_cases
    from qualification.training_export import case_to_row

    decidable = [c for c in generate_cases() if c.gold is not None]
    assert decidable
    good = case_to_row(decidable[0])
    # Rewrite the question in the internal shape upstream rejects.
    question = good["questions"]["route_preference"]
    bad = json.loads(json.dumps(good))
    bad["questions"]["route_preference"] = {
        "t": "choice",
        "crit": question["criteria"],
        "ins": question["instructions"],
    }

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "bad.jsonl"
        path.write_text(json.dumps(bad) + "\n", encoding="utf-8")
        script = (
            "from laya.train import dry_run\n"
            f"dry_run({str(path)!r},\n"
            "    '/home/bamn/.cache/laya/"
            "convaiinnovations-laya-typed-decisions-1a793eb')"
        )
        proc = subprocess.run(
            [python, "-c", script],
            cwd=REPO, capture_output=True, timeout=600, check=False)
        combined = proc.stdout.decode() + proc.stderr.decode()
        assert proc.returncode != 0, (
            "upstream ACCEPTED an internal-shape row; the exporter may have "
            f"drifted back into the rejected shape.\n{combined}")
        assert "n_items must be a positive integer" in combined, (
            f"upstream failed for an unexpected reason:\n{combined[-600:]}")


def test_upstream_dry_run_accepts_the_public_shape():
    """The positive control: the same row in the public shape is accepted.

    Without this, the rejection above could pass for a reason unrelated to the
    schema -- e.g. any single-row file failing for some other cause.
    """
    python = _laya_python()
    if python is None:
        import pytest
        pytest.skip("laya >= 0.4.6 not importable in any available interpreter")

    import tempfile

    from qualification.contrastive_fixtures import generate_cases
    from qualification.training_export import case_to_row

    decidable = [c for c in generate_cases() if c.gold is not None]
    good = case_to_row(decidable[0])

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "good.jsonl"
        path.write_text(json.dumps(good) + "\n", encoding="utf-8")
        script = (
            "import json\n"
            "from laya.train import dry_run\n"
            f"r = dry_run({str(path)!r},\n"
            "    '/home/bamn/.cache/laya/"
            "convaiinnovations-laya-typed-decisions-1a793eb')\n"
            "print(json.dumps(r.get('skipped', {})))"
        )
        proc = subprocess.run(
            [python, "-c", script],
            cwd=REPO, capture_output=True, timeout=600, check=False)
        out = _last_json_line(proc.stdout.decode())
        assert proc.returncode == 0, proc.stderr.decode()[-800:]
        assert json.loads(out) == {}, f"upstream skipped a valid row: {out}"
