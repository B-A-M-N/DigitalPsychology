#!/usr/bin/env python3
"""Export the compiled deployable guard pack artifact that
CognitiveFrameWorks reads. DP owns the research implementation; the runtime
consumes only compiled-guard-pack.json with a stable schema.

Sources: registry/guards.json -> export ALL deployable guards (active +
canary, with rollout metadata). Budget/scoping/canary-assignment happens at
task composition time in the runtime, never here — a TUI guard can no longer
consume global budget and make an unrelated infrastructure guard disappear.

The exporter schema-validates before atomically publishing the artifact. The
semantic content hash excludes compiled_at: identical policy -> identical
semantic hash.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.feedback_loop import (  # noqa: E402
    GuardRegistry, ReceiptRegistry, guard_semantic_hash, guard_to_dict,
    default_registry_path, default_receipts_path, trusted_state_root,
)
from lib.receipts import is_production_grade_criterion  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
LEGACY_REGISTRY_PATH = ROOT / "registry" / "guards.json"
OUT_PATH = trusted_state_root() / "compiled-guard-pack.json"
SP = Path(__file__).resolve().parents[1]


def enforcement_capabilities() -> dict:
    """Load capabilities published by the target Cognitive Runtime."""
    configured = os.environ.get("COGNITIVE_FRAMEWORKS_CAPABILITIES")
    candidates = [Path(configured)] if configured else []
    candidates.extend([
        ROOT.parent / "CognitiveFrameWorks" / "contracts" / "enforcement-capabilities.json",
        ROOT / "enforcement-capabilities.json",
    ])
    for path in candidates:
        if path and path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data.get("handlers"), dict):
                raise ValueError(f"invalid runtime capability artifact: {path}")
            return data["handlers"]
    raise ValueError("target runtime enforcement-capabilities.json is unavailable")


def _stable_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def main() -> int:
    registry_path = default_registry_path()
    if not registry_path.exists():
        print(f"Missing trusted persistent registry: {registry_path}")
        print("Agent-editable registry/ guards are candidate data only and cannot authorize deployment.")
        return 1
    OUT_PATH.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    registry = GuardRegistry(path=registry_path)
    receipts = ReceiptRegistry(path=default_receipts_path())
    deployable = [g for g in registry.all() if g.status in {"active", "canary"}]
    lifecycle_problems = registry.audit_deployments(receipts)
    if lifecycle_problems:
        print("Deployable guard lifecycle is not receipt-complete:")
        for problem in lifecycle_problems:
            print(f"  - {problem}")
        return 1
    by_family = {}
    for guard in deployable:
        by_family.setdefault(guard.family or guard.id, []).append(guard)
    for family, members in by_family.items():
        if len(members) <= 1:
            continue
        keys = {member.key for member in members}
        superseded = set()
        for member in members:
            for ref in member.supersedes or []:
                if ref in keys:
                    superseded.add(ref)
                elif any(item.id == ref for item in members):
                    superseded.add(next(item.key for item in members if item.id == ref))
        survivors = [member for member in members if member.key not in superseded]
        if len(survivors) != 1:
            print(f"Guard family {family!r} has multiple deployable versions without explicit supersession")
            return 1
    if not deployable:
        # An empty pack is a valid, auditable policy state.  In particular, a
        # retired duplicate must be removable from the runtime without
        # leaving a stale compiled artifact behind.
        print("No deployable guards (active/canary); exporting an explicit empty pack")

    # FrameWorks evaluator mappings stay out of the guard schema; the
    # aggregator back-fills evaluators from the framework kernel at analysis
    # time. Export only schema-conformant fields.
    guards = [guard_to_dict(g) for g in deployable]
    capabilities = enforcement_capabilities()
    for source_guard, guard in zip(deployable, guards):
        if source_guard.status in {"active", "canary"} and not getattr(
                source_guard, "_enforcement_key_explicit", False):
            print(f"Deployable guard {source_guard.key} requires an explicit enforcement_key")
            return 1
        enforcement = guard.get("enforcement") or {}
        if enforcement.get("mode") != "prompt":
            handler = enforcement.get("handler")
            version = enforcement.get("handler_version")
            capability = capabilities.get(handler) or {}
            if capability.get("version") != version:
                print(f"Unsupported structural enforcement handler/version: {handler!r}@{version!r}")
                return 1
            if enforcement.get("mode") not in set(capability.get("modes") or ()):
                print(f"Handler {handler!r} does not support mode {enforcement.get('mode')!r}")
                return 1
            try:
                jsonschema = __import__("jsonschema")
                jsonschema.Draft202012Validator(
                    capability.get("parameter_schema") or {"type": "object"}
                ).validate(enforcement.get("parameters") or {})
            except Exception as exc:
                print(f"Invalid parameters for handler {handler!r}: {exc}")
                return 1
        if source_guard.status == "active":
            # Every active deployment, including acceptance artifacts that
            # are written to the production trusted path, must carry a
            # receipt whose versioned criterion meets the production floor.
            refs = [source_guard.validation_receipt_ref,
                    source_guard.activation_receipt_ref]
            active_receipts = [receipts.get(ref) for ref in refs if ref]
            if not active_receipts:
                print(f"Active guard {source_guard.key} has no trusted receipt chain")
                return 1
            if (any(item is None or item.guard_semantic_hash !=
                    guard_semantic_hash(source_guard)
                    for item in active_receipts)):
                print(f"Active guard {source_guard.key} has an invalid semantic receipt chain")
                return 1
            if any(not is_production_grade_criterion(dict(item.criterion))
                   for item in active_receipts):
                print(f"Active guard {source_guard.key} lacks production-grade promotion criteria")
                return 1
    pack_version = "1.1.0"
    compiled_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    semantic_body = _stable_json({"pack_version": pack_version, "guards": guards})
    semantic_hash = hashlib.sha256(semantic_body.encode("utf-8")).hexdigest()
    payload = {
        "pack_version": pack_version,
        "source_registry_revision": registry.revision,
        "semantic_hash": semantic_hash,
        "content_hash": semantic_hash,
        "compiled_at": compiled_at,
        "rollout": {
            "canary_task_determinism": "hash(guard-version + task-id) percentile < canary_fraction; explicit canary_task_ids override",
            "task_ids": sorted({tid for g in deployable for tid in (g.rollout or {}).get("canary_task_ids", [])}),
        },
        "guards": guards,
    }

    # validate against the local schema before publishing
    try:
        import jsonschema
        from jsonschema import Draft202012Validator
        pack_schema = json.loads((SP / "schemas" / "guard-pack.schema.json").read_text(encoding="utf-8"))
        guard_schema = json.loads((SP / "schemas" / "guard.schema.json").read_text(encoding="utf-8"))
        from referencing import Registry, Resource
        local = Registry().with_resource(
            "https://digitalpsychology.dev/schemas/guard.schema.json",
            Resource.from_contents(guard_schema)).with_resource(
                "https://digitalpsychology.dev/schemas/guard-pack.schema.json",
                Resource.from_contents(pack_schema))
        Draft202012Validator(pack_schema, registry=local).validate(payload)
        for g in guards:
            Draft202012Validator(guard_schema).validate(g)
    except Exception as exc:
        print(f"Schema validation failed before publish: {exc}")
        return 1

    # atomic, durable, restrictive publish from trusted control-plane state
    fd, tmp = tempfile.mkstemp(dir=str(OUT_PATH.parent), prefix=".guard-pack-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, indent=2) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, OUT_PATH)
        directory_fd = os.open(OUT_PATH.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

    print(f"Exported {len(guards)} deployable guard(s) -> {OUT_PATH.name}")
    print(f"  active:  {sum(1 for g in guards if g['status'] == 'active')}")
    print(f"  canary:  {sum(1 for g in guards if g['status'] == 'canary')}")
    print(f"  semantic_hash: {semantic_hash}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
