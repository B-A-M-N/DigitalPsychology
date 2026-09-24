#!/usr/bin/env python3
"""Self-test for the DP feedback-loop plane. Proves the loop is executable,
not just specified: schema-validated events, actor/subject episodes,
real probe evaluators, guarded lifecycle, persistent registry, immutable
snapshot, event sink, secret-safe retention."""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.feedback_loop import (Event, EventError, EventFactory, EpisodeBuilder,
                               Guard, GuardCompiler, GuardRegistry, GuardResolver, NDJSONSink,
                               SQLiteSink,
                               PolicySnapshot, ProbeResult, ProbeRunner,
                               Retention, TrialEvidence, load_events)


def make_event(i, cat, etype, **kw):
    return Event(
        event_id=f"evt-{i}",
        timestamp=kw.pop("timestamp", f"2026-09-01T10:{i:02d}:00Z"),
        task_id=kw.pop("task_id", "task-9"),
        agent_id=kw.pop("agent_id", "agent-a"),
        actor_id=kw.pop("actor_id", "agent-a"),
        category=cat,
        event_type=etype,
        **kw,
    )


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
    # 1. Schema-governed parse: missing schema_version and bad timestamps reject
    try:
        EventFactory.from_dict({"event_id": "x", "timestamp": "2026-09-01T10:00:00Z", "task_id": "t1",
                                "agent_id": "a", "category": "completion", "event_type": "completion_claim"})
        raise SystemExit("missing schema_version should have failed")
    except EventError:
        print("ok: missing schema_version rejected")
    try:
        EventFactory.from_dict({"schema_version": "2.0.0", "event_id": "x", "timestamp": "not-a-date",
                                "task_id": "t1", "agent_id": "a", "category": "observation",
                                "event_type": "o"})
        raise SystemExit("bad timestamp should have failed")
    except EventError:
        print("ok: invalid timestamp rejected")
    try:
        EventFactory.from_dict({"schema_version": "2.0.0", "event_id": "x", "timestamp": "2026-09-01T10:00:00Z",
                                "task_id": "t1", "agent_id": "a", "category": "completion",
                                "event_type": "completion_claim", "evidence_refs": []})
        raise SystemExit("empty evidence_refs should have failed")
    except EventError:
        print("ok: empty evidence_refs rejected")

    # 2. Episode builder: operator contradiction groups with the behavioral
    #    subject via actor/subject split; stale_model_defense across segments
    builder = EpisodeBuilder()
    for ev in [
        make_event(1, "completion", "completion_claim", evidence_refs=["evt-0"]),
        make_event(2, "contradiction", "operator_contradiction", actor_id="operator", invalidates="evt-1"),
        make_event(3, "decision", "defense_of_prior_claim"),
        make_event(4, "tool_result", "fresh_tool_use", payload={"tool_type": "test"}),
        make_event(5, "decision", "revised_claim"),
    ]:
        builder.add(ev)
    episodes = builder.build()
    assert all(ep.pattern == "stale_model_defense" for ep in episodes), [e.pattern for e in episodes]
    print("ok: episode pattern =", episodes[0].pattern)
    agent_events = [e for ep in episodes for e in ep.events]
    assert any(e.event_type == "operator_contradiction" and e.actor_id == "operator"
               for e in agent_events)
    print("    actor/subject split links operator contradiction to agent")

    # 3. Guard lifecycle: candidate -> experiment -> validated requires a
    #    verifiable ValidationReceipt (booleans are never evidence)
    from lib.feedback_loop import ReceiptBuilder, ReceiptRegistry, guard_semantic_hash
    from lib.receipts import DEFAULT_PRODUCTION_CRITERION
    receipts = ReceiptRegistry()
    reg = GuardRegistry()
    try:
        reg.add(Guard(id="G-teleport", status="active", target_behavior="x", rule="r"))
        raise SystemExit("active guard insertion must require lifecycle evidence")
    except ValueError:
        print("ok: GuardRegistry rejects status teleport on add")
    base = Guard(id="G-completion", family="G-completion", version="1",
                 status="candidate", target_behavior="premature closure",
                 rule="completion requires boundary evidence", priority="P1")
    reg.add(base)
    try:
        reg.transition("G-completion", "validated")
        raise SystemExit("candidate->validated should reject")
    except ValueError:
        print("ok: candidate->validated rejected")
    reg.transition("G-completion", "experiment")
    try:
        reg.transition("G-completion", "validated")
        raise SystemExit("experiment->validated without receipt should reject")
    except ValueError:
        print("ok: experiment->validated without receipt rejected")
    # a fake boolean attempt must not authorize validation
    g = reg.by_id("G-completion")
    g.validation["successful_probe_record"] = True
    try:
        reg.transition("G-completion", "validated")
        raise SystemExit("boolean successful_probe_record must not authorize validation")
    except ValueError:
        print("ok: boolean probe record cannot authorize validation")

    builder = ReceiptBuilder(evaluator_version_hash="eval-hash-1",
                             guard_semantic_hash=guard_semantic_hash(base),
                             criterion=DEFAULT_PRODUCTION_CRITERION)
    outputs = {}
    outputs.update(trial_map({f"b{i}": "FAIL" for i in range(10)}, "baseline-policy"))
    outputs.update(trial_map({f"i{i}": "PASS" for i in range(10)}, "candidate-policy"))
    outputs.update(trial_map({f"h{i}": "PASS" for i in range(10)}, "holdout-policy"))
    vrcpt = builder.build(
        guard_key="G-completion@1", kind="validation",
        baseline_trial_ids=tuple(f"b{i}" for i in range(10)),
        intervention_trial_ids=tuple(f"i{i}" for i in range(10)),
        holdout_trial_ids=tuple(f"h{i}" for i in range(10)), trial_outputs=outputs,
        baseline_policy_hashes=("baseline-policy",),
        treatment_policy_hashes=("candidate-policy",),
        holdout_policy_hashes=("holdout-policy",))
    receipts.add(vrcpt)
    provenance_outputs = dict(outputs)
    provenance_outputs["i1"] = TrialEvidence(
        trial_id="i1", trajectory_id="trajectory-i1-wrong", probe_id="registered-probe",
        probe_version="1", policy_hash="baseline-policy",
        execution_policy_hash="baseline-policy", session_id="session-i1-wrong",
        task_id="task-i1-wrong", attempt_id="attempt-1", outcome="PASS",
        host_evidence_ref="evt-i1-wrong")
    try:
        builder.build(
            guard_key="G-completion@1", kind="validation",
            baseline_trial_ids=tuple(f"b{i}" for i in range(10)),
            intervention_trial_ids=tuple(f"i{i}" for i in range(10)),
            holdout_trial_ids=tuple(f"h{i}" for i in range(10)), trial_outputs=provenance_outputs,
            baseline_policy_hashes=("baseline-policy",),
            treatment_policy_hashes=("candidate-policy",),
            holdout_policy_hashes=("holdout-policy",))
        raise SystemExit("cross-cohort trial policy provenance must be rejected")
    except ValueError:
        print("ok: receipt builder binds every trial to its declared cohort policy")
    try:
        builder.build(
            receipt_id="caller-chosen", guard_key="G-completion@1", kind="validation",
            baseline_trial_ids=tuple(f"b{i}" for i in range(10)),
            intervention_trial_ids=tuple(f"i{i}" for i in range(10)),
            holdout_trial_ids=tuple(f"h{i}" for i in range(10)), trial_outputs=outputs,
            baseline_policy_hashes=("baseline-policy",),
            treatment_policy_hashes=("candidate-policy",),
            holdout_policy_hashes=("holdout-policy",))
        raise SystemExit("caller-chosen receipt IDs must be rejected")
    except ValueError:
        print("ok: receipt IDs are content-addressed")
    g.validation_receipt_ref = vrcpt.receipt_id
    reg.transition("G-completion", "validated", receipt_registry=receipts,
                   receipt_id=vrcpt.receipt_id)
    g.rollout = {"canary_task_ids": ["task-9"]}
    reg.transition("G-completion", "canary", receipt_registry=receipts)
    # canary -> active requires a verifiable canary receipt
    try:
        reg.transition("G-completion", "active")
        raise SystemExit("canary->active without canary receipt should reject")
    except ValueError:
        print("ok: canary->active without canary receipt rejected")
    canary_outputs = {}
    canary_outputs.update(trial_map({f"b{i}": "FAIL" for i in range(10)}, "baseline-policy"))
    canary_outputs.update(trial_map({f"c{i}": "PASS" for i in range(10)}, "canary-policy"))
    canary_outputs.update(trial_map({f"ch{i}": "PASS" for i in range(10)}, "holdout-policy"))
    crcpt = builder.build(
        guard_key="G-completion@1", kind="canary",
        baseline_trial_ids=tuple(f"b{i}" for i in range(10)),
        intervention_trial_ids=tuple(f"c{i}" for i in range(10)),
        holdout_trial_ids=tuple(f"ch{i}" for i in range(10)), trial_outputs=canary_outputs,
        baseline_policy_hashes=("baseline-policy",),
        treatment_policy_hashes=("canary-policy",),
        holdout_policy_hashes=("holdout-policy",))
    receipts.add(crcpt)
    reg.transition("G-completion", "active", receipt_registry=receipts, receipt_id=crcpt.receipt_id)
    print("ok: full lifecycle enforced via receipts")

    # A receipt for an earlier guard meaning cannot promote a mutated
    # candidate even when its key and receipt id are otherwise valid.
    mutated = Guard(id="G-mutated", family="G-mutated", version="1", status="canary",
                    target_behavior="same target", rule="original rule", priority="P1")
    mutated_builder = ReceiptBuilder(evaluator_version_hash="eval-hash-1",
                                     guard_semantic_hash=guard_semantic_hash(mutated),
                                     criterion=DEFAULT_PRODUCTION_CRITERION)
    mutated_receipt = mutated_builder.build(
        guard_key="G-mutated@1", kind="canary",
        baseline_trial_ids=tuple(f"mb{i}" for i in range(10)),
        intervention_trial_ids=tuple(f"mi{i}" for i in range(10)),
        holdout_trial_ids=tuple(f"mh{i}" for i in range(10)),
        trial_outputs={
            **trial_map({f"mb{i}": "FAIL" for i in range(10)}, "b"),
            **trial_map({f"mi{i}": "PASS" for i in range(10)}, "i"),
            **trial_map({f"mh{i}": "PASS" for i in range(10)}, "h"),
        },
        baseline_policy_hashes=("b",), treatment_policy_hashes=("i",),
        holdout_policy_hashes=("h",))
    mutated.rule = "mutated after receipt"
    mutated_registry = GuardRegistry()
    mutated_registry._insert(mutated)
    mutated_receipts = ReceiptRegistry()
    mutated_receipts.add(mutated_receipt)
    try:
        mutated_registry.transition("G-mutated", "active",
                                    receipt_registry=mutated_receipts,
                                    receipt_id=mutated_receipt.receipt_id)
        raise SystemExit("mutated guard must reject old receipt")
    except ValueError:
        print("ok: guard semantic mutation rejects old receipt")

    # Trusted registries must rehydrate from their default paths after a
    # restart; otherwise an apparently persisted promotion is lost.
    previous_state_root = os.environ.get("DIGITALPSYCHOLOGY_STATE_ROOT")
    with tempfile.TemporaryDirectory(prefix="dp-restart-") as restart_root:
        os.environ["DIGITALPSYCHOLOGY_STATE_ROOT"] = restart_root
        trusted_reg = GuardRegistry(default_to_trusted_state=True)
        trusted_receipts = ReceiptRegistry(default_to_trusted_state=True)
        trusted_guard = Guard(
            id="G-restart", family="G-restart", version="1", status="validated",
            target_behavior="restart proof", rule="persist trusted state",
            priority="P1", validation_receipt_ref=vrcpt.receipt_id)
        trusted_reg._insert(trusted_guard)
        trusted_receipts.add(vrcpt)
        trusted_reg.save()
        trusted_receipts.save()
        restarted_reg = GuardRegistry(default_to_trusted_state=True)
        restarted_receipts = ReceiptRegistry(default_to_trusted_state=True)
        assert restarted_reg.by_id("G-restart") is not None
        assert restarted_receipts.verify(vrcpt.receipt_id, "validation", "G-completion@1")
    if previous_state_root is None:
        os.environ.pop("DIGITALPSYCHOLOGY_STATE_ROOT", None)
    else:
        os.environ["DIGITALPSYCHOLOGY_STATE_ROOT"] = previous_state_root
    print("ok: trusted registry and receipts survive default-path restart")

    # 4. Compiler: candidate/validated/stale/rolled_back never compile
    reg.add(Guard(id="G-cand", status="candidate", target_behavior="x", rule="r", priority="P1"))
    reg._insert(Guard(id="G-stale", status="stale", target_behavior="y", rule="r", priority="P1"))
    compiler = GuardCompiler(token_budget=100, always_on_ids=["G-completion"])
    result = compiler.compile(reg.all(), task_domains=[], task_shapes=[])
    assert "G-completion" in result["active"]
    assert "G-cand" not in result["active"] and "G-stale" not in result["active"]
    print("ok: ineligible guards cannot compile:", result["ineligible"])

    # 5. Conflicts block compilation
    reg._insert(Guard(id="G-a", status="active", target_behavior="a", rule="r",
                      priority="P1", conflicts_with=["G-b"]))
    reg._insert(Guard(id="G-b", status="active", target_behavior="b", rule="r",
                      priority="P1", conflicts_with=["G-a"]))
    conflicted = compiler.compile(reg.all(), task_domains=[], task_shapes=[])
    assert conflicted.get("error") == "unresolved conflicts"
    print("ok: conflicting guards block compilation")

    # 6. Explicit supersedes (no string heuristic)
    reg2 = GuardRegistry()
    reg2._insert(Guard(id="G-new", status="active", target_behavior="premature closure", rule="new",
                       priority="P1", supersedes=["G-old"]))
    reg2._insert(Guard(id="G-old", status="active", target_behavior="premature closure", rule="old",
                       priority="P1"))
    res2 = compiler.compile(reg2.all(), task_domains=[], task_shapes=[])
    assert "G-new" in res2["active"] and "G-old" not in res2["active"]
    print("ok: explicit supersedes replaces")

    # 7. Fixtures load through the same canonical loader
    events = load_events(Path(__file__).resolve().parents[1] / "fixtures" / "events.ndjson")
    assert len(events) == 4
    print("ok: fixtures load via schema-validated loader:", [e.event_type for e in events])

    # 8. ProbeRunner: empty event set cannot pass a completion probe
    runner = ProbeRunner()
    pr = runner.run("completion_has_boundary_evidence", [])
    assert pr.passed is False, "empty event set must not pass a completion probe"
    print("ok: empty event set cannot pass completion probe")

    # 9. PolicySnapshot is immutable
    snap = PolicySnapshot(task_id="task-9", framework_version="1.4.0", framework_hash="abc",
                          guard_pack_version="G-pack@2", guard_pack_hash="h")
    try:
        snap.framework_version = "9.9.9"
        raise SystemExit("snapshot should be immutable")
    except Exception:
        print("ok: policy snapshot immutable")

    # 10. Event sink
    with tempfile.TemporaryDirectory() as tmp:
        sink = NDJSONSink(Path(tmp) / "events.ndjson")
        for ev in events:
            sink.emit(ev)
        lines = (Path(tmp) / "events.ndjson").read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 4
        print("ok: NDJSON event sink emits", len(lines), "events")
        sqlite_sink = SQLiteSink(Path(tmp) / "events.sqlite3")
        sqlite_sink.emit(events[0])
        row = sqlite_sink._conn.execute(
            "SELECT agent_instance_id, session_id, attempt_id, segment_id, behavioral_subject "
            "FROM events WHERE event_id = ?", (events[0].event_id,)).fetchone()
        sqlite_sink.close()
        assert row == (events[0].agent_instance_id, events[0].session_id,
                       events[0].attempt_id, events[0].segment_id,
                       events[0].behavioral_subject)
        print("ok: SQLite event store preserves session identity hierarchy")

    # 11. Retention: handoffs classified full fidelity
    retention = Retention()
    handoff = make_event(10, "handoff", "repository_truth_packet", payload={"packet_type": "repository_truth_packet"})
    routine = make_event(11, "tool_result", "test_suite_result", payload={"result_class": "pass"})
    assert retention.classify(handoff) == "full"
    assert retention.classify(routine) == "sampled"
    print("ok: retention classifies handoff as full, routine as sampled")

    # 12. Canary assignment deterministic: only assigned tasks see canary guards
    reg_c = GuardRegistry()
    reg_c._insert(Guard(id="G-canary", status="canary", target_behavior="t", rule="r",
                        priority="P1", rollout={"canary_task_ids": ["task-canary"]}))
    reg_c._insert(Guard(id="G-active", status="active", target_behavior="a", rule="r", priority="P1"))
    cc = GuardCompiler(token_budget=100, canary_task_ids={"task-canary"})
    res_c = cc.compile(reg_c.all(), task_domains=[], task_shapes=[], task_id="task-canary")
    res_c2 = cc.compile(reg_c.all(), task_domains=[], task_shapes=[], task_id="task-other")
    assert "G-canary" in res_c["active"] and "G-canary" not in res_c2["active"]
    assert res_c == cc.compile(reg_c.all(), task_domains=[], task_shapes=[], task_id="task-canary")
    print("ok: canary assignment deterministic and task-scoped")

    # 12b. GuardResolver passes model/harness/trigger through to the
    #      compiler (the signature must not promise more than it implements)
    reg_r = GuardRegistry()
    reg_r._insert(Guard(id="G-m", family="G-m", version="1", status="active",
                        target_behavior="x", rule="r", scope={"models": ["sonnet-4"]}))
    reg_r._insert(Guard(id="G-any", family="G-any", version="1", status="active",
                        target_behavior="y", rule="r"))
    comp_r = GuardCompiler(token_budget=200)
    res_r = GuardResolver(comp_r, reg_r).resolve(
        "task-r", domains=[], shapes=[], model="sonnet-4", harness="codex-cli")
    assert "G-m" in res_r.get("active", []), "matching model must compile"
    res_r2 = GuardResolver(comp_r, reg_r).resolve(
        "task-r", domains=[], shapes=[], model="other-model", harness="codex-cli")
    assert "G-m" not in res_r2.get("active", []), "non-matching model must not compile (fail-closed)"
    assert "G-any" in res_r2.get("active", []), "unrestricted guard compiles"
    print("ok: GuardResolver threads model/harness constraints fail-closed")

    # 13. Real fixture feeds episode builder (operator contradiction links)
    fixture_events = load_events(Path(__file__).resolve().parents[1] / "fixtures" / "events.ndjson")
    builder_f = EpisodeBuilder()
    for ev in fixture_events:
        builder_f.add(ev)
    eps_f = builder_f.build()
    f_agent_events = [e for ep in eps_f for e in ep.events]
    assert any(e.event_type == "operator_contradiction" and e.actor_id == "operator"
               and e.agent_id == "agent-a" for e in f_agent_events)
    print("ok: real fixture links operator contradiction to agent-a")

    print("\nfeedback-loop self-test passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
