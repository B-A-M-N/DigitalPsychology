from __future__ import annotations

import json
import math
import sqlite3
import time
import tracemalloc

from lib.behavior_state import BehaviorState, CSWBehaviorSnapshot
from lib.control.fast_loop_input import FastLoopInput
from lib.fast_loop import BehavioralController
from lib.runtime.projection import project_behavior_context
from lib.runtime.store import BehaviorStateStore, RuntimeStateRecord


def test_2000_boundary_soak_bounds_receipts_latency_context_and_memory(tmp_path):
    database_path = tmp_path / "behavior.sqlite3"
    scope = "tenant/app/agent/session/task/attempt"
    state = BehaviorState()
    store = BehaviorStateStore(database_path)
    store.register_control_session(scope, {"test_fixture": "durable-soak"},
                                  initial_state=state)
    record = store.load(scope)
    controller = BehavioralController(intervention_expiry_actions=1)
    latencies = []
    storage_latencies = []
    interventions_applied = 0
    max_context_bytes = 0
    reconnects = 0
    tracemalloc.start()
    try:
        for boundary in range(2000):
            action_index = boundary + 1
            started = time.perf_counter()
            decision = controller.decide(FastLoopInput(
                scope=scope,
                state=record.state,
                csw_snapshot=CSWBehaviorSnapshot(revision=action_index),
                objective_criteria=("criterion-1",),
                equivalent_retry_count=2 if boundary % 2 == 0 else 0,
                action_completed=True,
                host_action_index=action_index,
            ))
            prior_ids = {item.intervention_id for item in record.state.active_interventions}
            new_interventions = tuple(
                item for item in decision.state.active_interventions
                if item.intervention_id not in prior_ids)
            receipts = {}
            outbox_events = []
            for intervention in new_interventions:
                directive_ids = tuple(sorted(
                    item.directive_id for item in decision.directives
                    if item.intervention_id == intervention.intervention_id))
                assert directive_ids
                receipts[intervention.intervention_id] = directive_ids
                outbox_events.append({
                    "event_id": f"application-{action_index}",
                    "scope": scope,
                    "kind": "behavior_intervention_applied",
                    "intervention_id": intervention.intervention_id,
                })
            interventions_applied += len(new_interventions)

            basis = {
                "expected_behavior_revision": record.state.revision,
                "expected_action_index": record.action_index,
                "commit_action_index": action_index,
                "host_action_index": action_index,
                "boundary_sequence": boundary,
                "boundary_kind": "post_action",
            }
            tick_id = f"soak-tick-{boundary}"
            storage_started = time.perf_counter()
            store.prepare_control_tick(
                scope, tick_id=tick_id,
                request_digest=f"soak-request-{boundary}", basis=basis,
                decision=decision.to_dict())
            store.save(
                scope, expected_revision=record.state.revision,
                expected_action_index=record.action_index,
                action_index=action_index, state=decision.state,
                application_receipts=receipts,
                outbox_events=outbox_events,
                control_tick_id=tick_id,
                control_tick_result={"behavior_revision": decision.state.revision},
                behavioral_action_trace={
                    "action_index": action_index,
                    "intervention_applied": bool(new_interventions),
                },
                behavioral_control_transition={
                    "control_session_id": scope,
                    "boundary_sequence": boundary,
                    "host_action_index": action_index,
                    "boundary_kind": "post_action",
                    "behavior_revision": decision.state.revision,
                },
            )
            for event in store.pending_outbox(scope):
                store.acknowledge_outbox_event(scope, event.event_id)
            storage_latencies.append(time.perf_counter() - storage_started)
            latency = time.perf_counter() - started
            latencies.append(latency)

            context = project_behavior_context(
                decision.state, CSWBehaviorSnapshot(revision=action_index),
                objective="Complete the bounded storage soak",
                goal_set=None, cfw_connected=False, csw_connected=False,
                outcome_evidence_connected=False)
            max_context_bytes = max(max_context_bytes,
                                    len(context.encode("utf-8")))
            record = RuntimeStateRecord(decision.state, action_index, receipts)

            if action_index in {401, 801, 1201, 1601}:
                store = BehaviorStateStore(database_path)
                record = store.load(scope)
                assert record.state.revision == action_index
                assert len(record.application_receipts) <= 16
                assert len(store.pending_outbox(scope)) == 0
                reconnects += 1
    finally:
        _current_memory_bytes, peak_memory_bytes = tracemalloc.get_traced_memory()
        tracemalloc.stop()

    with sqlite3.connect(database_path) as database:
        boundary_count = database.execute(
            "SELECT COUNT(*) FROM behavioral_control_transitions WHERE scope=?",
            (scope,)).fetchone()[0]
        tick_count, committed_count = database.execute(
            "SELECT COUNT(*), SUM(status='committed') FROM control_ticks WHERE scope=?",
            (scope,)).fetchone()
        action_rows = database.execute(
            "SELECT event_json FROM behavioral_action_traces WHERE scope=?",
            (scope,)).fetchall()
        receipt_count = database.execute(
            "SELECT COUNT(*) FROM intervention_receipts WHERE scope=?",
            (scope,)).fetchone()[0]
        outbox_count = database.execute(
            "SELECT COUNT(*) FROM runtime_event_outbox WHERE scope=?",
            (scope,)).fetchone()[0]

    latencies.sort()
    p99_latency = latencies[math.ceil(len(latencies) * .99) - 1]
    storage_latencies.sort()
    p99_storage_latency = storage_latencies[
        math.ceil(len(storage_latencies) * .99) - 1]
    database_bytes = sum(
        path.stat().st_size for path in tmp_path.glob("behavior.sqlite3*"))
    loaded = store.load(scope)

    assert reconnects == 4
    assert boundary_count == tick_count == committed_count == 2000
    assert len(action_rows) == 2000
    assert sum(bool(json.loads(row[0]).get("intervention_applied"))
               for row in action_rows) == interventions_applied
    assert interventions_applied >= 1000
    assert receipt_count <= 16
    assert len(loaded.application_receipts) <= 16
    assert outbox_count <= 32
    assert p99_latency < 1.5
    assert latencies[-1] < 3.0
    assert p99_storage_latency < 1.0
    assert storage_latencies[-1] < 2.0
    assert max_context_bytes < 16_384
    assert peak_memory_bytes < 128 * 1024 * 1024
    assert database_bytes < 64 * 1024 * 1024


def test_outbox_delivery_failure_and_success_survive_store_restart(tmp_path):
    database_path = tmp_path / "behavior.sqlite3"
    scope = "tenant/app/agent/session/task/attempt"
    store = BehaviorStateStore(database_path)
    store.save(
        scope, expected_revision=0, expected_action_index=-1, action_index=0,
        state=BehaviorState(revision=1),
        outbox_events=({"event_id": "event-1", "scope": scope,
                        "event_type": "system_one_decision"},))

    store.record_outbox_dispatch_attempt(scope, "event-1")
    store.record_outbox_dispatch_failure(scope, "event-1", "EventSinkError")
    restarted = BehaviorStateStore(database_path)
    failed = restarted.outbox_delivery_status(scope, authenticated_event_ids=())
    assert failed["committed_count"] == 1
    assert failed["enqueued_count"] == 1
    assert failed["delivered_count"] == 0
    assert failed["pending_count"] == 1
    assert failed["authenticated_count"] == 0
    assert failed["authenticated_status_available"] is True
    assert failed["last_dispatch_failure"]["error_class"] == "EventSinkError"
    assert failed["last_attempted_event_id"] == "event-1"
    assert failed["last_attempt_at"]
    assert failed["by_event_type"]["system_one_decision"]["pending"] == 1

    restarted.record_outbox_dispatch_attempt(scope, "event-1")
    restarted.acknowledge_outbox_event(scope, "event-1")
    delivered = BehaviorStateStore(database_path).outbox_delivery_status(
        scope, authenticated_event_ids=("event-1",))
    assert delivered["delivered_count"] == 1
    assert delivered["pending_count"] == 0
    assert delivered["authenticated_count"] == 1
    assert delivered["last_dispatch_failure"]["error_class"] == "EventSinkError"
    assert delivered["last_attempted_event_id"] == "event-1"
    assert delivered["last_successful_delivery_at"]
