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

from lib.laya_configuration import (DEFAULT_DESTINATION, MODEL_CONTENT_SHA256,
                                    MODEL_REPOSITORY, MODEL_REVISION,
                                    WEIGHTS_SHA256)
from lib.system_one_backends import model_content_digest


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
