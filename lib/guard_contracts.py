"""Pure guard-contract hashing shared by research and promotion code."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


def guard_semantic_hash(guard: Any) -> str:
    if isinstance(guard, Mapping):
        data = dict(guard)
    else:
        data = {key: getattr(guard, key, None) for key in (
            "family", "version", "target_behavior", "rule", "enforcement",
            "scope", "trigger", "evaluator")}
    semantic = {
        key: data.get(key)
        for key in ("family", "version", "target_behavior", "rule", "enforcement",
                    "scope", "trigger", "evaluator")
    }
    encoded = json.dumps(semantic, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
