#!/usr/bin/env python3
"""Cross-system dynamic-improvement acceptance test.

Runs the real layer boundary in one offline executable:

    DigitalPsychology trusted registry + receipts
    -> trusted compiled guard pack export
    -> CognitiveFrameWorks trusted guard-pack loader
    -> CFW resolve/start + runtime telemetry
    -> DP receipt lifecycle promotion

No live model or network is used. The editable DP registry is never treated
as deployment authority.
"""
from __future__ import annotations

import importlib.util
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CFW_ROOT = ROOT.parent / "CognitiveFrameWorks"
sys.path.insert(0, str(ROOT))
from lib.feedback_loop import (Guard, GuardRegistry, ReceiptBuilder,  # noqa: E402
                               ReceiptRegistry, TrialEvidence, guard_semantic_hash)
from lib.receipts import DEFAULT_PRODUCTION_CRITERION  # noqa: E402


def load_module(name: str, path: Path):
    if name == "cross_runtime" and "resolve_runtime" in sys.modules:
        del sys.modules["resolve_runtime"]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    if name == "cross_runtime":
        sys.modules["resolve_runtime"] = sys.modules["cross_resolve_runtime"]
    spec.loader.exec_module(module)
    if name == "cross_runtime":
        assert module.resolve_runtime is sys.modules["cross_resolve_runtime"]
    return module


def trial_map(values, policy):
    return {
        trial_id: TrialEvidence(
            trial_id=trial_id, trajectory_id=f"trajectory-{trial_id}",
            probe_id="registered-probe", probe_version="1",
            policy_hash=policy, execution_policy_hash=policy,
            session_id=f"session-{trial_id}", task_id=f"task-{trial_id}",
            attempt_id="attempt-1", outcome=outcome,
            host_evidence_ref=f"evt-{trial_id}")
        for trial_id, outcome in values.items()
    }


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="dp-cfw-loop-") as raw_root:
        state_root = Path(raw_root) / "trusted-state"
        os.environ["DIGITALPSYCHOLOGY_STATE_ROOT"] = str(state_root)

        registry = GuardRegistry(default_to_trusted_state=True)
        receipts = ReceiptRegistry(default_to_trusted_state=True)
        guard = Guard(
            id="G-cross-system", family="G-cross-system", version="1",
            status="candidate", target_behavior="stale-model defense",
            rule="A contradiction requires fresh relevant inspection before revision.",
            priority="P1")
        registry.add(guard)
        registry.transition("G-cross-system", "experiment")
        receipt_builder = ReceiptBuilder(evaluator_version_hash="eval-v1",
                                         guard_semantic_hash=guard_semantic_hash(guard),
                                         criterion=DEFAULT_PRODUCTION_CRITERION)
        validation_outputs = {
            **trial_map({f"b{i}": "FAIL" for i in range(10)}, "baseline-policy"),
            **trial_map({f"i{i}": "PASS" for i in range(10)}, "candidate-policy"),
            **trial_map({f"h{i}": "PASS" for i in range(10)}, "holdout-policy"),
        }
        validation = receipt_builder.build(
            guard_key="G-cross-system@1",
            kind="validation", baseline_trial_ids=tuple(f"b{i}" for i in range(10)),
            intervention_trial_ids=tuple(f"i{i}" for i in range(10)),
            holdout_trial_ids=tuple(f"h{i}" for i in range(10)),
            trial_outputs=validation_outputs,
            baseline_policy_hashes=("baseline-policy",),
            treatment_policy_hashes=("candidate-policy",),
            holdout_policy_hashes=("holdout-policy",),
            environment="offline-test", model="no-model", harness="cross-system")
        receipts.add(validation)
        guard.validation_receipt_ref = validation.receipt_id
        registry.transition("G-cross-system", "validated",
                            receipt_registry=receipts,
                            receipt_id=validation.receipt_id)
        guard.rollout = {"canary_fraction": 0.0, "canary_task_ids": ["canary-task"]}
        registry.transition("G-cross-system", "canary", receipt_registry=receipts)
        receipts.save()
        registry.save()

        export = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "export-guard-pack.py")],
            text=True, capture_output=True, check=False)
        assert export.returncode == 0, export.stdout + export.stderr
        trusted_pack_path = state_root / "compiled-guard-pack.json"
        pack = json.loads(trusted_pack_path.read_text(encoding="utf-8"))
        assert pack["content_hash"] == pack["semantic_hash"]
        assert any(g["key"] == "G-cross-system@1" for g in pack["guards"])
        assert os.stat(trusted_pack_path).st_mode & 0o777 == 0o600

        resolve_runtime = load_module("cross_resolve_runtime", CFW_ROOT / "scripts" / "resolve-runtime.py")
        runtime = load_module("cross_runtime", CFW_ROOT / "scripts" / "runtime.py")
        sys.modules["runtime"] = runtime
        sys.modules["resolve_runtime"] = resolve_runtime
        api = load_module("cross_cognitive_runtime", CFW_ROOT / "scripts" / "cognitive_runtime.py")
        assert resolve_runtime.dp_trusted_guard_pack_path() == trusted_pack_path
        runtime_root = Path(raw_root) / "cfw-runtime"
        resolve_runtime.runtime_state_dir = lambda: runtime_root

        canary_request = resolve_runtime.TaskRequest(
            task_id="canary-task", application_id="digital-psychology", subject_ref="claim", shape="implement",
            domain_tags=("debug",), model="no-model", harness="cross-system")
        canary_bundle = resolve_runtime.resolve(canary_request)
        canary_keys = {g["key"] for g in canary_bundle.guard_pack["guards"]}
        assert "G-cross-system@1" in canary_keys, canary_keys
        noncanary_bundle = resolve_runtime.resolve(
            resolve_runtime.TaskRequest(
                task_id="noncanary-task", application_id="digital-psychology", subject_ref="claim", shape="implement",
                domain_tags=("debug",), model="no-model", harness="cross-system"))
        assert "G-cross-system@1" not in {
            g["key"] for g in noncanary_bundle.guard_pack["guards"]}
        assert canary_bundle.pinned["policy_hash"] != noncanary_bundle.pinned["policy_hash"]

        canary_session = api.CognitiveRuntime().begin_task(canary_request)
        agent = api.CognitiveRuntime().agent_handle(canary_session)
        host = api.CognitiveRuntime().host_handle(canary_session)
        evidence_id = host.after_tool(
            tool_type="test", result_class="evidence", subject_ref="claim",
            invocation_id="inv-cross", actual_result={"status": "evidence"})
        host.operator_observation(subject_ref="claim", invalidates=evidence_id)
        assert host.request_completion(
            subject_ref="claim", evidence_refs=[evidence_id]) == runtime.GateDecision.REQUIRE_REINSPECTION
        telemetry_path = next(
            path for path in (runtime_root / "telemetry").glob("*.ndjson")
            if any(json.loads(line).get("task_id") == "canary-task"
                   for line in path.read_text(encoding="utf-8").splitlines() if line))
        events = [json.loads(line) for line in telemetry_path.read_text(encoding="utf-8").splitlines()]
        assert {event["category"] for event in events} >= {
            "tool_result", "contradiction", "blocker"}

        canary_outputs = {
            **trial_map({f"cb{i}": "FAIL" for i in range(10)}, "active-policy"),
            **trial_map({f"c{i}": "PASS" for i in range(10)}, "canary-policy"),
            **trial_map({f"ch{i}": "PASS" for i in range(10)}, "holdout-policy"),
        }
        canary = receipt_builder.build(
            guard_key="G-cross-system@1",
            kind="canary", baseline_trial_ids=tuple(f"cb{i}" for i in range(10)),
            intervention_trial_ids=tuple(f"c{i}" for i in range(10)),
            holdout_trial_ids=tuple(f"ch{i}" for i in range(10)),
            trial_outputs=canary_outputs,
            baseline_policy_hashes=("active-policy",),
            treatment_policy_hashes=("canary-policy",),
            holdout_policy_hashes=("holdout-policy",),
            environment="offline-test", model="no-model", harness="cross-system")
        receipts.add(canary)
        registry.transition("G-cross-system", "active",
                            receipt_registry=receipts,
                            receipt_id=canary.receipt_id)
        receipts.save()
        registry.save()
        export_active = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "export-guard-pack.py")],
            text=True, capture_output=True, check=False)
        assert export_active.returncode == 0, export_active.stdout + export_active.stderr

        active_bundle = resolve_runtime.resolve(
            "after-promotion", "implement", ["debug"])
        assert "G-cross-system@1" in {
            g["key"] for g in active_bundle.guard_pack["guards"]}

        print("trusted export -> CFW consume: ok")
        print("canary task received exact G-cross-system@1: ok")
        print("non-canary task excluded: ok")
        print("immutable task policies differed: ok")
        print("real host telemetry emitted and readable: ok")
        print("receipt-gated promotion -> next task policy: ok")
        print("\nCROSS-SYSTEM ACCEPTANCE LOOP PASSED")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
