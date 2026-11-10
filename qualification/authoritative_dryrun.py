"""The AUTHORITATIVE export check: upstream `laya.train.dry_run`.

`qualification/laya_contract_precheck.py` reimplements the documented skip rules so
the data can be checked without laya installed. That is a convenience, not proof --
a shared wrong assumption between exporter and precheck is exactly what produced the
internal-shape defect. This module runs the real `dry_run()` and is the authority.

`dry_run` needs only the tokenizer and `rl_agent_config.json`, not the model
weights, so it runs on a CPU-only box.

Tests skip when laya >= 0.4.6 is absent from the active interpreter, rather than
failing, so the pinned `.venv-laya` (0.3.22, no train module) stays green.
"""
from __future__ import annotations

import json
import sys
from collections.abc import Sequence

# The snapshot of what the trainable corpus is, so the authority can be pointed at
# a freshly exported set.
EXPORT_DIR = "/tmp/dp-audit/laya-train"
SPLITS = ("train", "calibration", "holdout")
CKPT = "/home/bamn/.cache/laya/convaiinnovations-laya-typed-decisions-1a793eb"


def _laya_available() -> bool:
    try:
        import laya  # noqa: F401
        from laya.train import dry_run  # noqa: F401
    except (ImportError, AttributeError):
        return False
    return True


def run_dry_run(path: str, ckpt: str = CKPT) -> dict:
    """Run upstream dry_run in-process and return its summary dict."""
    from laya.train import dry_run
    return dry_run(path, ckpt)


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    directory = argv[0] if argv else EXPORT_DIR
    if not _laya_available():
        print("laya >= 0.4.6 is not importable in this interpreter; "
              "nothing to verify.")
        return 0

    ok = True
    for split in SPLITS:
        path = f"{directory}/{split}.jsonl"
        summary = run_dry_run(path)
        skipped = summary.get("skipped") or {}
        print(json.dumps({
            "split": split,
            "rows_read": summary["rows_read"],
            "valid_items": summary["valid_items"],
            "skipped": skipped,
        }))
        if skipped:
            ok = False
        if summary["rows_read"] != summary["valid_items"]:
            ok = False
    print(json.dumps({"authority": "upstream dry_run",
                      "verdict": "accept" if ok else "reject"}))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
