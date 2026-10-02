#!/usr/bin/env python3
"""Download and verify the pinned optional Laya System-1 checkpoint."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import sys

from huggingface_hub import snapshot_download

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lib.system_one_backends import model_content_digest


MODEL_REPOSITORY = "convaiinnovations/laya-typed-decisions"
MODEL_REVISION = "1a793eb568e6718f15941d08f85432581df534e3"
MODEL_CONTENT_SHA256 = "cda55a3e793f4c4268a25066ceb1568aa302fd210d96656e3032756fd722b642"
WEIGHTS_SHA256 = "4fa56de72383a9d3efa9cfa78955733c81b9fc8067a587ca4beb82c78107a24e"
DEFAULT_DESTINATION = (
    Path.home() / ".cache/laya/convaiinnovations-laya-typed-decisions-1a793eb")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, default=DEFAULT_DESTINATION)
    args = parser.parse_args()
    destination = args.destination.expanduser().resolve()
    path = Path(snapshot_download(
        repo_id=MODEL_REPOSITORY,
        revision=MODEL_REVISION,
        local_dir=destination,
    )).resolve(strict=True)
    content_hash = model_content_digest(path)
    weights_hash = hashlib.sha256((path / "model.safetensors").read_bytes()).hexdigest()
    if content_hash != MODEL_CONTENT_SHA256 or weights_hash != WEIGHTS_SHA256:
        raise SystemExit(
            "downloaded Laya snapshot does not match its pinned content checksums")
    print(f"Laya model verified at {path}")
    print(f"revision={MODEL_REVISION}")
    print(f"content_sha256={content_hash}")
    print(f"weights_sha256={weights_hash}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
