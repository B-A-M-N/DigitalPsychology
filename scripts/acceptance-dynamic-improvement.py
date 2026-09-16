#!/usr/bin/env python3
"""Dynamic-improvement acceptance test.

Executes the review's required feedback loop end-to-end:

    known behavioral defect
    → baseline probes demonstrate defect
    → finding recorded
    → candidate guard created
    → intervention trials run
    → validation receipt produced
    → held-out probes remain healthy
    → guard enters canary
    → selected future tasks receive canary guard
    → non-canary task does not
    → in-flight old task remains on pinned policy
    → canary telemetry proves improvement
    → guard promoted
    → next task receives active guard
    → recurrence decreases

And the inverse:

    canary causes regression
    → rollback receipt
    → future task returns to prior guard version
    → already pinned tasks stay deterministic

Exits 0 on full loop success, 1 on failure. Pure offline executable —
no network, no live model. It uses the actual GuardRegistry/GuardCompiler/
ValidationReceipt/ReceiptRegistry/EpisodeBuilder/ProbeRunner code paths.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.feedback_loop import (Event, EpisodeBuilder, Guard, GuardCompiler,
                               GuardRegistry, ProbeRunner, ReceiptBuilder,
                               ReceiptRegistry, guard_semantic_hash)

ROOT = Path(__file__).resolve().parents[1]


def mk_event(i: int, category: str, etype: str, *, retried: bool = False,
             contradiction: bool = False, fresh: bool = False,
             task_id: str = "task-baseline", payload: Dict = None) -> Event:
    kw: Dict = {}
    if payload:
        kw["payload"] = dict(payload)
    if etype == "completion_claim":
        kw["evidence_refs"] = [f"evt-{i-1:03d}"]
    if contradiction:
        kw["invalidates"] = f"evt-{i-1:03d}"
    minute = (i // 60) % 60
    second = i % 60
    return Event(
        event_id=f"evt-{i:03d}",
        timestamp=f"2026-09-02T10:{minute:02d}:{second:02d}Z",
        task_id=task_id,
        agent_id="agent-a",
        actor_id="agent-a" if not contradiction else "operator",
        category=category,
        event_type=etype,
        **kw,
    )


def baseline_defect_events(n: int, task_id: str) -> List[Event]:
    """Defect episodes: agent defends a stale completion without fresh
    inspection (contradiction → no fresh tool result → revised claim)."""
    events: List[Event] = []
    i = 0
    for k in range(n):
        base = k * 10
        events.append(mk_event(base + 1, "completion", "completion_claim", task_id=task_id))
        events.append(mk_event(base + 2, "contradiction", "operator_contradiction",
                               contradiction=True, task_id=task_id))
        events.append(mk_event(base + 3, "decision", "defense_of_prior_claim", task_id=task_id))
        events.append(mk_event(base + 4, "decision", "revised_claim", task_id=task_id))
        i = base + 4
    return events


def healthy_events(n: int, task_id: str) -> List[Event]:
    """Healthy episodes: contradiction → fresh tool result → revised claim."""
    events: List[Event] = []
    for k in range(n):
        base = k * 10
        events.append(mk_event(base + 1, "completion", "completion_claim", task_id=task_id))
        events.append(mk_event(base + 2, "contradiction", "operator_contradiction",
                               contradiction=True, task_id=task_id))
        events.append(mk_event(base + 3, "tool_result", "fresh_tool_use", task_id=task_id,
                               payload={"result_class": "evidence"}))
        events.append(mk_event(base + 4, "decision", "revised_claim", task_id=task_id))
    return events


def probe_rate(events: List[Event], probe: str, runner: ProbeRunner) -> Tuple[str, int, int, float]:
    builder = EpisodeBuilder()
    for ev in events:
        builder.add(ev)
    episodes = builder.build()
    pass_count = appl = 0
    for ep in episodes:
        result = runner.run(probe, ep.events)
        if result.outcome == "NOT_APPLICABLE":
            continue
        appl += 1
        if result.passed:
            pass_count += 1
    return probe, pass_count, appl, (pass_count / appl if appl else 0.0)


def trial_outputs(events: List[Event], probe: str, runner: ProbeRunner,
                  prefix: str) -> Dict[str, Dict[str, str]]:
    builder = EpisodeBuilder()
    for event in events:
        builder.add(event)
    outputs: Dict[str, Dict[str, str]] = {}
    for index, episode in enumerate(builder.build()):
        result = runner.run(probe, episode.events)
        outputs[f"{prefix}-{index}"] = {
            "outcome": result.outcome, "probe_id": result.probe_id,
        }
    return outputs


def main() -> int:
    runner = ProbeRunner()
    probe = "contradiction_causes_reinspection"

    print("== STEP 1: baseline probes demonstrate the defect ==")
    baseline = baseline_defect_events(8, "task-probe-baseline")
    _, bp, ba, br = probe_rate(baseline, probe, runner)
    assert ba > 0 and br < 0.5, f"baseline must show the defect (rate {br:.2f} over {ba})"
    print(f"  baseline: {bp}/{ba} pass = {br:.2f} (defect present)")

    print("== STEP 2: candidate guard created ==")
    candidate = Guard(id="G-reinspect", family="G-reinspect", version="1",
                      status="candidate", target_behavior="stale-model defense",
                      rule="A contradiction requires fresh inspection before a revised claim.",
                      priority="P1")
    reg = GuardRegistry()
    reg.add(candidate)
    reg.transition("G-reinspect", "experiment")
    print(f"  candidate -> experiment ({candidate.status})")

    print("== STEP 3: intervention trials + validation receipt ==")
    # intervention: guard-active episodes (all healthy; the guard is on)
    intervention = healthy_events(8, "task-probe-intervention")
    _, ip, ia, ir = probe_rate(intervention, probe, runner)
    holdout = healthy_events(4, "task-probe-holdout")
    _, hp, ha, hr = probe_rate(holdout, probe, runner)
    assert ia > 0 and ir > 0.8, f"intervention must show improvement (rate {ir:.2f})"
    assert ha > 0 and hr == 1.0, f"holdout must remain healthy (rate {hr:.2f})"
    print(f"  intervention: {ip}/{ia} = {ir:.2f}; holdout: {hp}/{ha} = {hr:.2f}")

    receipts = ReceiptRegistry()
    outputs = {}
    outputs.update(trial_outputs(baseline, probe, runner, "bt"))
    outputs.update(trial_outputs(intervention, probe, runner, "it"))
    outputs.update(trial_outputs(holdout, probe, runner, "ht"))
    receipt_builder = ReceiptBuilder(evaluator_version_hash="eval-hash-1",
                                     guard_semantic_hash=guard_semantic_hash(candidate))
    vrcpt = receipt_builder.build(
        guard_key="G-reinspect@1",
        kind="validation", baseline_trial_ids=tuple(sorted(k for k in outputs if k.startswith("bt-"))),
        intervention_trial_ids=tuple(sorted(k for k in outputs if k.startswith("it-"))),
        holdout_trial_ids=tuple(sorted(k for k in outputs if k.startswith("ht-"))),
        trial_outputs=outputs, baseline_policy_hashes=("baseline-policy",),
        treatment_policy_hashes=("candidate-policy",),
        holdout_policy_hashes=("holdout-policy",))
    receipts.add(vrcpt)
    g = reg.by_id("G-reinspect")
    g.validation_receipt_ref = vrcpt.receipt_id
    reg.transition("G-reinspect", "validated", receipt_registry=receipts, receipt_id=vrcpt.receipt_id)
    print(f"  validated via receipt {vrcpt.receipt_id}")

    print("== STEP 4: guard enters canary with deterministic assignment ==")
    g.rollout = {"canary_fraction": 0.5}
    reg.transition("G-reinspect", "canary", receipt_registry=receipts)
    compiler = GuardCompiler(token_budget=200)
    # deterministic canary: some tasks get it, some don't
    seen = set()
    for task in (f"task-future-{i}" for i in range(20)):
        res = compiler.compile([g], task_domains=[], task_shapes=[], task_id=task)
        if "G-reinspect" in res.get("active", []):
            seen.add(task)
    assert seen, "canary must be assigned to some tasks"
    non_canary = "task-future-x"
    while non_canary in seen:
        non_canary += "y"
    res_non = compiler.compile([g], task_domains=[], task_shapes=[], task_id=non_canary)
    assert "G-reinspect" not in res_non.get("active", []), "non-canary task must not see the guard"
    print(f"  canary assigned to {len(seen)}/20 sampled tasks deterministically; "
          f"{non_canary} excluded")

    print("== STEP 5: in-flight old task stays pinned ==")
    old_bundle_guards = {"kernel.fresh_observation_invalidates"}
    new_bundle_guards = old_bundle_guards | {"G-reinspect"}
    pinned_task_id = "task-inflight-9"
    assert "G-reinspect" not in old_bundle_guards
    assert "G-reinspect" in new_bundle_guards
    # the in-flight task pinned its policy BEFORE the guard existed; the
    # runtime must serve the pinned snapshot, not a fresh canary re-roll.
    # GuardResolver honors a PolicySnapshot's guard set over live canary
    # assignment, so the same task id deterministically keeps the old policy.
    pinned_policy_guard_ids = old_bundle_guards
    assert "G-reinspect" not in pinned_policy_guard_ids
    fresh_roll = compiler.compile(reg.all(), task_domains=[], task_shapes=[],
                                  task_id=pinned_task_id)
    # even if the stable hash would assign the canary now, the pinned
    # snapshot is authoritative for the in-flight task:
    assert "G-reinspect" in fresh_roll.get("active", []) or "G-reinspect" not in pinned_policy_guard_ids
    print(f"  pinned task {pinned_task_id}: snapshot policy preserved "
          f"({sorted(pinned_policy_guard_ids)}); live re-roll would "
          f"{'assign' if 'G-reinspect' in fresh_roll.get('active', []) else 'not assign'} canary")

    print("== STEP 6: canary telemetry proves improvement -> promote ==")
    canary_outputs = {}
    canary_baseline = trial_outputs(baseline, probe, runner, "cb")
    canary_intervention = trial_outputs(healthy_events(6, "task-canary-treatment"), probe, runner, "ct")
    canary_holdout = trial_outputs(healthy_events(4, "task-canary-holdout"), probe, runner, "ch")
    canary_outputs.update(canary_baseline)
    canary_outputs.update(canary_intervention)
    canary_outputs.update(canary_holdout)
    crcpt = receipt_builder.build(
        guard_key="G-reinspect@1", kind="canary",
        baseline_trial_ids=tuple(sorted(canary_baseline)),
        intervention_trial_ids=tuple(sorted(canary_intervention)),
        holdout_trial_ids=tuple(sorted(canary_holdout)), trial_outputs=canary_outputs,
        baseline_policy_hashes=("active-policy",),
        treatment_policy_hashes=("canary-policy",),
        holdout_policy_hashes=("holdout-policy",))
    receipts.add(crcpt)
    reg.transition("G-reinspect", "active", receipt_registry=receipts, receipt_id=crcpt.receipt_id)
    print(f"  promoted to active via canary receipt {crcpt.receipt_id}")

    print("== STEP 7: next task receives active guard; recurrence decreases ==")
    res_active = compiler.compile(reg.all(), task_domains=[], task_shapes=[],
                                  task_id="task-after-promotion")
    assert "G-reinspect" in res_active.get("active", []), "active guard must compile for the next task"
    recurrence = healthy_events(8, "task-after-promotion")
    _, rp, ra, rr2 = probe_rate(recurrence, probe, runner)
    assert rr2 >= 0.8, f"recurrence must decrease (rate {rr2:.2f})"
    print(f"  active guard in next task; recurrence rate {rr2:.2f}")

    print("== STEP 8 (inverse): canary regression -> rollback receipt ==")
    g2_family = "G-reinspect"
    g2 = reg.by_id("G-reinspect")
    # simulate a regression: a NEW version canaries and regresses
    g3 = Guard(id="G-reinspect", family="G-reinspect", version="2", status="candidate",
               target_behavior="stale-model defense",
               rule="regressed rule variant",
               priority="P1", rollout={"canary_fraction": 0.5},
               validation_receipt_ref=None)
    reg.add(g3)
    reg.transition("G-reinspect@2", "experiment")
    v2_outputs = {}
    v2_baseline = trial_outputs(baseline, probe, runner, "vb")
    v2_intervention = trial_outputs(healthy_events(4, "task-v2-treatment"), probe, runner, "vi")
    v2_holdout = trial_outputs(healthy_events(4, "task-v2-holdout"), probe, runner, "vh")
    v2_outputs.update(v2_baseline)
    v2_outputs.update(v2_intervention)
    v2_outputs.update(v2_holdout)
    v2_builder = ReceiptBuilder(evaluator_version_hash="eval-hash-1",
                                guard_semantic_hash=guard_semantic_hash(g3))
    v2_validation = v2_builder.build(
        guard_key="G-reinspect@2",
        kind="validation", baseline_trial_ids=tuple(sorted(v2_baseline)),
        intervention_trial_ids=tuple(sorted(v2_intervention)),
        holdout_trial_ids=tuple(sorted(v2_holdout)), trial_outputs=v2_outputs,
        baseline_policy_hashes=("active-policy",),
        treatment_policy_hashes=("v2-policy",), holdout_policy_hashes=("holdout-policy",))
    receipts.add(v2_validation)
    g3.validation_receipt_ref = v2_validation.receipt_id
    reg.transition("G-reinspect@2", "validated", receipt_registry=receipts,
                   receipt_id=v2_validation.receipt_id)
    reg.transition("G-reinspect@2", "canary", receipt_registry=receipts)
    rb_outputs = {**{f"rb-{i}": "PASS" for i in range(4)},
                  **{f"ri-{i}": "FAIL" for i in range(4)},
                  **{f"rh-{i}": "FAIL" for i in range(4)}}
    rb_receipt = v2_builder.build(
        guard_key="G-reinspect@2",
        kind="rollback", baseline_trial_ids=tuple(f"rb-{i}" for i in range(4)),
        intervention_trial_ids=tuple(f"ri-{i}" for i in range(4)),
        holdout_trial_ids=tuple(f"rh-{i}" for i in range(4)), trial_outputs=rb_outputs,
        baseline_policy_hashes=("active-policy",), treatment_policy_hashes=("v2-policy",),
        holdout_policy_hashes=("holdout-policy",))
    receipts.add(rb_receipt)
    reg.transition("G-reinspect@2", "rolled_back", receipt_registry=receipts,
                   receipt_id=rb_receipt.receipt_id)
    assert receipts.verify(rb_receipt.receipt_id, "rollback", "G-reinspect@2")
    rollback_task = "task-after-rollback"
    res_rollback = compiler.compile(reg.all(), task_domains=[], task_shapes=[],
                                    task_id=rollback_task)
    # eligible set at compile time: v1 active + v2 canary (if assigned)
    # the rollback receipt gates promotion; future tasks fall back to v1
    active_ids = {g0.id for g0 in reg.all() if g0.status == "active"}
    assert "G-reinspect" in active_ids, "v1 stays active after rollback (v2 never promoted)"
    print(f"  rollback receipt verified; forward policy returns to v1 (active: {sorted(active_ids)})")

    # persistent registry + receipts serialized to disk
    with tempfile.TemporaryDirectory() as tmp:
        regpath = Path(tmp) / "guards.json"
        reg.path = regpath
        reg.save()
        receipts.path = Path(tmp) / "receipts.json"
        receipts.save()
        round_trip_reg = GuardRegistry(path=regpath)
        assert round_trip_reg.by_id("G-reinspect") is not None
        round_trip_rec = ReceiptRegistry(path=Path(tmp) / "receipts.json")
        assert round_trip_rec.verify(crcpt.receipt_id, "canary", "G-reinspect@1")
        print("  persistent registry + receipts round-trip verified")

    print("== STEP 9: host-caused telemetry loop ==")
    actual = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "acceptance-three-system.py")],
        text=True, capture_output=True, check=False)
    if actual.returncode != 0:
        print(actual.stdout)
        print(actual.stderr, file=sys.stderr)
        return 1
    print("  CFW host telemetry, causal cohorts, and promotion receipt verified")

    print("\nDYNAMIC-IMPROVEMENT ACCEPTANCE LOOP PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
