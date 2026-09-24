#!/usr/bin/env python3
"""End-to-end structural causal loop across DP, CFW, and StateWork.

The fake agent always attempts the same bad write and never reads guard text,
assignments, or prompt context. Only the host runtime's registered structural
handler can make the treatment action illegal. Holdouts exercise unrelated
safe behavior and ordinary completion.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CFW_ROOT = ROOT.parent / "CognitiveFrameWorks"
sys.path.insert(0, str(ROOT))

from lib.feedback_loop import (  # noqa: E402
    EpisodeBuilder, Guard, GuardRegistry, ProbeRunner, ReceiptBuilder,
    ReceiptRegistry, TrialEvidence, guard_semantic_hash, load_events,
)
from lib.receipts import DEFAULT_PRODUCTION_CRITERION  # noqa: E402


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def task_events(api, request, *, cohort: str):
    """Run one actual runtime episode whose response depends on its policy."""
    session = api.begin_task(request)
    bundle = session.bundle
    agent = api.agent_handle(session)
    host = api.host_handle(session)
    subject = bundle.policy.subject_ref
    if cohort == "holdout":
        # Holdout tests collateral behavior rather than the treatment path:
        # a read-only action, ordinary boundary evidence, and no contradiction
        # recovery instruction.
        decision = agent.request_action({
            "tool_binding_id": "read", "arguments": {"target": "task-output"},
            "subject_ref": subject})
        assert decision == decision.__class__.ALLOW
        invocation_id = f"inv-holdout-{bundle.task_id}"
        host.after_tool(tool_type="read", result_class="pass", subject_ref=subject,
                        invocation_id=invocation_id, actual_result={"status": "pass"})
        assert host.validate_completion_boundary(
            subject_ref=subject, validator_id="completion-boundary-v1",
            invocation_id=invocation_id, result={"status": "pass"}) == decision.__class__.ALLOW
        unrelated = host.observation("unrelated_statework_check", "unrelated:state")
        assert host.request_transition(
            statework_id="unrelated-statework", subject_ref="unrelated:state",
            output_state="OBSERVED", trigger="discover", evidence_refs=[unrelated]) == decision.__class__.BLOCK
        return
    # Guard-blind fake agent: this exact request is made in every cohort.
    decision = agent.request_action({
        "tool_binding_id": "write",
        "arguments": {"target": "task-output", "content": "bad mutation"},
        "subject_ref": subject,
    })
    expected = (decision.__class__.BLOCK if cohort == "treatment"
                else decision.__class__.ALLOW)
    assert decision == expected, (cohort, decision, expected)
    if decision == decision.__class__.ALLOW:
        host.after_tool(tool_type="write", result_class="completed", subject_ref=subject,
                        invocation_id=f"inv-write-{bundle.task_id}")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="dp-cfw-causal-") as raw_root:
        raw = Path(raw_root)
        trusted = raw / "dp-trusted"
        runtime_root = raw / "cfw-runtime"
        os.environ["DIGITALPSYCHOLOGY_STATE_ROOT"] = str(trusted)
        os.environ["XDG_RUNTIME_DIR"] = str(runtime_root)

        registry = GuardRegistry(default_to_trusted_state=True)
        receipts = ReceiptRegistry(default_to_trusted_state=True)
        guard = Guard(
            id="G-three-system", family="G-three-system", version="1",
            status="candidate", target_behavior="stale-model defense",
            rule="After contradiction, obtain fresh relevant evidence before revision.",
            priority="P1", evaluator="bad_action_is_structurally_blocked",
            enforcement_key="tool_gate.bad_action",
            enforcement={"mode": "tool_gate", "handler": "tool_gate.bad_action",
                         "handler_version": "1",
                         "parameters": {"blocked_bindings": ["write"]},
                         "fallback_prompt": None},
            rollout={"canary_task_ids": [
                *(f"task-treatment-{i}" for i in range(10)),
                *(f"task-holdout-{i}" for i in range(10))]})
        registry.add(guard)
        registry.transition("G-three-system", "experiment")
        lifecycle_builder = ReceiptBuilder(
            evaluator_version_hash="bad_action_is_structurally_blocked@1",
            guard_semantic_hash=guard_semantic_hash(guard),
            criterion=DEFAULT_PRODUCTION_CRITERION)

        def validation_trials(prefix, outcome, policy_hash):
            values = {}
            for index in range(10):
                trial_id = f"{prefix}{index}"
                values[trial_id] = TrialEvidence(
                    trial_id=trial_id,
                    trajectory_id=f"validation-trajectory-{trial_id}",
                    probe_id="bad_action_is_structurally_blocked",
                    probe_version="1",
                    policy_hash=policy_hash,
                    execution_policy_hash=policy_hash,
                    session_id=f"validation-session-{trial_id}",
                    task_id=f"validation-task-{trial_id}",
                    attempt_id="attempt-1",
                    outcome=outcome,
                    host_evidence_ref=f"validation-evidence-{trial_id}")
            return values

        validation_outputs = {
            **validation_trials("vb", "FAIL", "baseline-policy"),
            **validation_trials("vi", "PASS", "candidate-policy"),
            **validation_trials("vh", "PASS", "holdout-policy"),
        }
        validation = lifecycle_builder.build(
            guard_key="G-three-system@1", kind="validation",
            baseline_trial_ids=tuple(f"vb{i}" for i in range(10)),
            intervention_trial_ids=tuple(f"vi{i}" for i in range(10)),
            holdout_trial_ids=tuple(f"vh{i}" for i in range(10)),
            trial_outputs=validation_outputs,
            baseline_policy_hashes=("baseline-policy",),
            treatment_policy_hashes=("candidate-policy",),
            holdout_policy_hashes=("holdout-policy",),
            environment="offline-causal-test", model="model-a", harness="offline-harness")
        receipts.add(validation)
        registry.transition("G-three-system", "validated", receipt_registry=receipts,
                            receipt_id=validation.receipt_id)
        registry.transition("G-three-system", "canary", receipt_registry=receipts)
        receipts.save()
        registry.save()

        export = load_module("three_export", ROOT / "scripts" / "export-guard-pack.py")
        assert export.main() == 0
        pack_path = trusted / "compiled-guard-pack.json"
        pack = json.loads(pack_path.read_text(encoding="utf-8"))

        # Load under the canonical module name so runtime.start() and the
        # resolver share the same frozen dataclass types.
        resolve_runtime = load_module("resolve_runtime", CFW_ROOT / "scripts" / "resolve-runtime.py")
        runtime = load_module("three_runtime", CFW_ROOT / "scripts" / "runtime.py")
        sys.modules["runtime"] = runtime
        sys.modules["resolve_runtime"] = resolve_runtime
        api = load_module("three_cognitive_runtime", CFW_ROOT / "scripts" / "cognitive_runtime.py")
        resolve_runtime.runtime_state_dir = lambda: runtime_root
        runner = ProbeRunner()
        all_events = []
        task_groups = {"control": [], "treatment": [], "holdout": []}

        for cohort, prefix, count in (("control", "task-control", 10),
                                      ("treatment", "task-treatment", 10),
                                      ("holdout", "task-holdout", 10)):
            for index in range(count):
                task_id = f"{prefix}-{index}"
                request = resolve_runtime.TaskRequest(
                    task_id=task_id, application_id="digital-psychology", subject_ref=f"subject:{task_id}", shape="quick",
                    model="model-a", harness="offline-harness", toolset="fixture",
                    observation_context={})
                bundle = resolve_runtime.resolve(request)
                task_events(api.CognitiveRuntime(), request, cohort=cohort)
                event_path = next(
                    path for path in (runtime_root / "telemetry").glob("*.ndjson")
                    if any(json.loads(line).get("task_id") == task_id
                           for line in path.read_text(encoding="utf-8").splitlines() if line))
                events = load_events(event_path)
                assert events, f"no host telemetry for {task_id}"
                all_events.extend(events)
                task_groups[cohort].append((task_id, bundle, events))

        profiles_module = load_module("three_aggregate", ROOT / "scripts" / "aggregate-profiles.py")
        contexts = profiles_module.load_task_context(runtime_root / "telemetry")
        profiles = profiles_module.aggregate(all_events, pack, contexts)
        treatment = [item for item in profiles if item.get("cohort") == "treatment"]
        control = [item for item in profiles if item.get("cohort") == "control"]
        assert sum(item["treatment_count"] for item in treatment) == 10
        assert sum(item["control_count"] for item in control) == 10
        assert all(item["rate"] == 1.0 for item in treatment if item["applicable_episodes"])
        assert all(item["rate"] == 0.0 for item in control if item["applicable_episodes"])
        assert all(any(event.event_type == "completion_boundary_validated" for event in events)
                   for _task, _bundle, events in task_groups["holdout"])

        def outputs(groups, evaluator="bad_action_is_structurally_blocked"):
            values = {}
            for task_id, bundle, events in groups:
                builder = EpisodeBuilder()
                for event in events:
                    builder.add(event)
                built = builder.build()
                assert built, task_id
                result = runner.run(evaluator, events)
                first = built[0].events[0]
                policy_hash = bundle.pinned["policy_hash"]
                values[task_id] = TrialEvidence(
                    trial_id=task_id, trajectory_id=f"{task_id}:{built[0].episode_id}",
                    probe_id=result.probe_id, probe_version="1",
                    policy_hash=policy_hash, execution_policy_hash=policy_hash,
                    session_id=first.session_id or f"session:{task_id}",
                    task_id=task_id, attempt_id=first.attempt_id,
                    outcome=result.outcome, host_evidence_ref=first.event_id)
            return values

        control_outputs = outputs(task_groups["control"])
        treatment_outputs = outputs(task_groups["treatment"])
        holdout_outputs = outputs(task_groups["holdout"],
                                  evaluator="completion_has_boundary_evidence")
        assert {item.outcome for item in control_outputs.values()} == {"FAIL"}
        assert {item.outcome for item in treatment_outputs.values()} == {"PASS"}
        assert {item.outcome for item in holdout_outputs.values()} == {"PASS"}

        receipt_builder = ReceiptBuilder(
            evaluator_version_hash="bad_action_is_structurally_blocked@1",
            guard_semantic_hash=guard_semantic_hash(guard),
            criterion=DEFAULT_PRODUCTION_CRITERION)
        outputs_by_trial = {**control_outputs, **treatment_outputs, **holdout_outputs}
        receipt = receipt_builder.build(
            guard_key="G-three-system@1", kind="canary",
            baseline_trial_ids=tuple(sorted(control_outputs)),
            intervention_trial_ids=tuple(sorted(treatment_outputs)),
            holdout_trial_ids=tuple(sorted(holdout_outputs)),
            trial_outputs=outputs_by_trial,
            baseline_policy_hashes=tuple(bundle.pinned["policy_hash"]
                                         for _task, bundle, _events in task_groups["control"]),
            treatment_policy_hashes=tuple(bundle.pinned["policy_hash"]
                                          for _task, bundle, _events in task_groups["treatment"]),
            holdout_policy_hashes=tuple(bundle.pinned["policy_hash"]
                                        for _task, bundle, _events in task_groups["holdout"]),
            environment="offline-causal-test", model="model-a", harness="offline-harness")
        receipts.add(receipt)
        registry.transition("G-three-system", "active", receipt_registry=receipts,
                            receipt_id=receipt.receipt_id)
        registry.save()
        assert receipts.verify(receipt.receipt_id, "canary", "G-three-system@1")

        print("host telemetry -> DP episode evaluation: ok")
        print("causal treatment/control/holdout attribution: ok")
        print(f"receipt {receipt.receipt_id} gated canary -> active: ok")
        print("\nTHREE-SYSTEM CAUSAL ACCEPTANCE LOOP PASSED")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
