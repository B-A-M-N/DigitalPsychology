import hashlib
import hmac
import json
import os
import tempfile
from pathlib import Path

import pytest

from lib.feedback_loop import Event, EventError, SQLiteSink
from lib.mcp_server import DigitalPsychologyService
from lib.routing_profiles import (
    BehavioralRoutingProfile,
    build_experiment_plan,
    build_profile,
    validate_experiment_plan,
)


def _event(instance: str, summary: str = "ok") -> Event:
    return Event(
        event_id="reused-event-id",
        timestamp="2026-09-19T00:00:00Z",
        task_id="task",
        agent_id="agent",
        category="tool_result",
        event_type="test_result",
        namespace_id="test-namespace",
        application_id="test-application",
        application_instance_id=instance,
        provider_id="provider",
        model_id="model",
        model_revision="revision",
        model_capability_hash="capability",
        harness_id="harness",
        harness_version="harness-version",
        agent_instance_id="agent-instance",
        session_id="session",
        behavioral_subject="subject",
        payload={"summary": summary},
    )


def _subject() -> dict[str, str]:
    return {
        "namespace_id": "test-namespace",
        "application_id": "test-application",
        "application_version": "application-version",
        "application_instance_id": "instance",
        "provider_id": "provider",
        "model_id": "model",
        "model_revision": "revision",
        "model_capability_hash": "capability",
        "harness_id": "harness",
        "harness_version": "harness-version",
        "agent_instance_id": "agent-instance",
    }


def test_experiment_plan_is_validated_before_return() -> None:
    plan = build_experiment_plan(
        experiment_id="experiment",
        candidate_profile_hash="a" * 64,
        candidate_adjustment={"route_type": "stage", "route": "flow"},
        eligibility={"field": "task_family", "equals": "performance"},
        target_evaluator="route-success",
        control_policy_identity="prepared-control",
        treatment_policy_identity="prepared-treatment",
    )
    assert validate_experiment_plan(plan)["plan_hash"] == plan["plan_hash"]


def test_profile_round_trip_preserves_scope_and_structured_versions() -> None:
    profile = build_profile(
        profile_id="candidate",
        subject=_subject(),
        context={"task_family": "performance", "statework_versions": {"gitter": "1.1.0"}},
        observations=[],
        routing_adjustments=[{
            "route_type": "stage", "route": "flow", "disposition": "suppress",
            "condition": {}, "evidence_refs": ["event"],
        }],
        evidence={"event_ids": ["event"], "trajectory_ids": ["trajectory"],
                  "source_hash": "source"},
        criterion={"evaluator_version": "test@1.0.0", "minimum_samples": 1,
                   "minimum_effect": 0.1, "confidence_rule": "test"},
    )
    round_trip = BehavioralRoutingProfile.from_dict(profile).to_dict()
    assert round_trip["subject"] == profile["subject"]
    assert round_trip["scope_mode"] == "exact"
    assert round_trip["context"]["statework_versions"] == {"gitter": "1.1.0"}


def test_sqlite_event_identity_is_origin_scoped_and_replay_safe() -> None:
    with tempfile.TemporaryDirectory() as raw:
        sink = SQLiteSink(Path(raw) / "events.sqlite3")
        sink.emit(_event("instance-a"))
        sink.emit(_event("instance-a"))
        sink.emit(_event("instance-b"))
        with pytest.raises(EventError):
            sink.emit(_event("instance-a", "changed"))
        assert sink._conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 2
        sink.close()


def test_mcp_ingestion_keeps_cfw_optional_and_is_idempotent() -> None:
    with tempfile.TemporaryDirectory() as raw:
        service = DigitalPsychologyService(event_db=Path(raw) / "events.sqlite3")
        result = service.ingest_behavior_event(_event("instance-a").to_dict())
        replay = service.ingest_behavior_event(_event("instance-a").to_dict())
        assert result["accepted"] and replay["accepted"]
        advice = service.behavioral_advice(
            context={"application_id": "test-application"},
            eligible_routes=[{"route_type": "stage", "route": "flow"}],
            static_policy={"policy_hash": "static"},
        )
        assert advice["status"] == "ok"
        assert advice["adjustments"] == []
        service.close()


def test_host_attested_ingestion_rejects_forged_origin(tmp_path, monkeypatch) -> None:
    secret = b"test-host-secret"
    monkeypatch.setenv("DIGITALPSYCHOLOGY_INGESTION_SECRET", secret.decode())
    service = DigitalPsychologyService(event_db=tmp_path / "events.sqlite3")
    event = _event("instance-a").to_dict()
    forged = dict(event, host_attestation={"issuer": "host", "signature": "forged"})
    with pytest.raises(ValueError, match="attestation"):
        service.ingest_behavior_event(forged)
    body = {k: v for k, v in event.items() if k != "host_attestation"}
    signature = hmac.new(secret, json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode(), hashlib.sha256).hexdigest()
    accepted = dict(event, host_attestation={"issuer": "host", "signature": signature})
    assert service.ingest_behavior_event(accepted)["accepted"]
    service.close()

def test_route_outcomes_are_canonical_and_zero_canary_has_no_cohort() -> None:
    from lib.routing_profiles import assign_routing_cohort, normalize_route_outcome, RoutingProfileError
    assert normalize_route_outcome("pass") == "PASS"
    assert normalize_route_outcome("bad") == "FAIL"
    with pytest.raises(RoutingProfileError):
        normalize_route_outcome("unknown")
    plan = build_experiment_plan(
        experiment_id="zero", candidate_profile_hash="a" * 64,
        candidate_adjustment={"route_type": "stage", "route": "flow"},
        eligibility={"field": "task_family", "equals": "performance"},
        target_evaluator="route-success", control_policy_identity="prepared-control",
        treatment_policy_identity="prepared-treatment", canary_fraction=0.0)
    assert assign_routing_cohort(plan, "trial", {"task_family": "performance"}) is None

def test_mcp_cannot_run_unregistered_raw_promotion_path() -> None:
    from lib.mcp_server import create_server
    with tempfile.TemporaryDirectory() as raw:
        service = DigitalPsychologyService(event_db=Path(raw) / "events.sqlite3")
        assert not hasattr(service, "run_slow_loop")
        assert hasattr(service, "run_registered_slow_loop")
        try:
            create_server(service)
        except RuntimeError:
            pass


def test_conflicting_terminal_outcomes_fail_closed() -> None:
    from lib.feedback_loop import Event
    from lib.routing_profiles import RoutingProfileError, build_experiment_plan, build_profile, derive_profiles
    from datetime import datetime, timedelta, timezone
    candidate = build_profile(
        profile_id="conflict-test",
        subject={"namespace_id": "n", "application_id": "a", "application_version": "1",
                 "application_instance_id": "i", "provider_id": "p", "model_id": "m",
                 "model_revision": "r", "model_capability_hash": "c", "harness_id": "h",
                 "harness_version": "v", "agent_instance_id": "agent"},
        context={"task_family": "performance", "task_shape": "performance", "domain_tags": [],
                 "phase": None, "environment": "test", "toolset": "fixture",
                 "statework_versions": {}, "framework_version": "f", "guard_pack_hash": "g"},
        observations=[], routing_adjustments=[{"route_type": "stage", "route": "flow", "disposition": "suppress",
                                              "condition": {}, "evidence_refs": ["x"]}],
        evidence={"event_ids": ["x"], "trajectory_ids": [], "source_hash": "x",
                  "experiment_id": "e", "candidate_adjustment": {"route_type": "stage", "route": "flow",
                  "disposition": "suppress", "condition": {}, "evidence_refs": ["x"]}, "receipt_ref": None},
        criterion={"evaluator_version": "routing-outcome-v2", "minimum_samples": 1,
                   "minimum_effect": 0.1, "confidence_rule": "x"})
    plan = build_experiment_plan(experiment_id="e", candidate_profile_hash=candidate["profile_hash"],
        candidate_profile_id="conflict-test", candidate_adjustment=candidate["routing_adjustments"][0],
        eligibility={"field": "task_family", "equals": "performance"}, target_evaluator="ev",
        control_policy_identity="c", treatment_policy_identity="t", canary_fraction=0.5)
    def ev(eid, outcome):
        return Event(event_id=eid, timestamp=(datetime(2026,1,1,tzinfo=timezone.utc)+timedelta(seconds=int(eid[-1]))).isoformat(),
            task_id="task", agent_id="agent", agent_instance_id="agent", session_id="session", attempt_id="attempt-1",
            category="decision", event_type="route_outcome", namespace_id="n", application_id="a", application_instance_id="i",
            provider_id="p", model_id="m", model_revision="r", model_capability_hash="c", harness_id="h", harness_version="v",
            behavioral_subject="s", payload={"route": "flow", "route_type": "stage", "route_decision_id": "d",
            "evaluator_id": "ev", "evaluator_version": plan["target_evaluator_version"], "evaluator_hash": plan["target_evaluator_hash"],
            "outcome_source": "evaluator:ev", "outcome": outcome, "candidate_profile_hash": plan["candidate_profile_hash"],
            "candidate_adjustment_applied": False, "policy_identity": "c", "experiment_plan_hash": plan["plan_hash"],
            "comparison_context_hash": "same"})
    contexts = {"n|i|agent|session|task|attempt-1|s|None|None|None": {"namespace_id": "n", "application_id": "a", "application_version": "1",
        "application_instance_id": "i", "provider_id": "p", "model_id": "m", "model_revision": "r",
        "model_capability_hash": "c", "harness_id": "h", "harness_version": "v", "agent_instance_id": "agent",
        "task_family": "performance", "task_shape": "performance", "domain_tags": [], "phase": None,
        "environment": "test", "toolset": "fixture", "statework_versions": {}, "framework_version": "f",
        "guard_pack_hash": "g", "role": "", "interaction_id": "", "delegation_id": "",
        "routing_experiment_plan": plan, "routing_cohort": "control",
        "comparison_context_hash": "same"}}
    with pytest.raises(RoutingProfileError):
        derive_profiles([ev("e1", "FAIL"), ev("e2", "PASS")], contexts)


def test_sqlite_write_contention_has_a_bounded_busy_timeout(tmp_path):
    import sqlite3
    import time
    from lib.feedback_loop import SQLiteSink
    db = tmp_path / "contended.sqlite3"
    first = SQLiteSink(db, busy_timeout_ms=25)
    second = sqlite3.connect(str(db), timeout=0.01)
    second.execute("PRAGMA busy_timeout=10")
    second.execute("BEGIN IMMEDIATE")
    second.execute("CREATE TABLE IF NOT EXISTS hold_lock (id INTEGER PRIMARY KEY)")
    started = time.monotonic()
    try:
        try:
            first._conn.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as exc:
            assert "locked" in str(exc).lower() or "busy" in str(exc).lower()
        else:
            raise AssertionError("contended writer unexpectedly acquired the SQLite lock")
    finally:
        second.rollback(); second.close(); first.close()
    assert time.monotonic() - started < 1.0


def test_configured_producers_require_matching_key_and_authorization(tmp_path, monkeypatch):
    import hmac
    from lib.mcp_server import DigitalPsychologyService, _canonical
    event = _event("instance-a").to_dict()
    event.update({"actor_id": "agent", "category": "decision"})
    producer_key = "producer-a-key"
    monkeypatch.setenv(f"DIGITALPSYCHOLOGY_INGESTION_KEY_{producer_key}", "secret-a")
    producers = {
        f"{event['namespace_id']}|{event['application_instance_id']}|{event['agent_instance_id']}": {
            "key_id": producer_key, "issuer": "host:a", "application_id": event["application_id"],
            "allowed_actors": ["agent"], "allowed_categories": ["decision"],
        }
    }
    service = DigitalPsychologyService(
        event_db=tmp_path / "events.sqlite3", trusted_producers=producers)
    body = {k: v for k, v in event.items() if k != "host_attestation"}
    signature = hmac.new(b"secret-a", _canonical(body).encode(), hashlib.sha256).hexdigest()
    accepted = {**event, "host_attestation": {
        "key_id": producer_key, "issuer": "host:a", "signature": signature}}
    assert service.ingest_behavior_event(accepted)["accepted"]
    forged = {**accepted, "host_attestation": {
        "key_id": producer_key, "issuer": "host:b", "signature": signature}}
    with pytest.raises(ValueError, match="issuer"):
        service.ingest_behavior_event(forged)
    impersonated = dict(event, event_id="forged-origin", application_id="other-app")
    body = {k: v for k, v in impersonated.items() if k != "host_attestation"}
    impersonated["host_attestation"] = {
        "key_id": producer_key, "issuer": "host:a",
        "signature": hmac.new(b"secret-a", _canonical(body).encode(), hashlib.sha256).hexdigest()}
    with pytest.raises(ValueError, match="application"):
        service.ingest_behavior_event(impersonated)
    # Rotate only this producer's key.
    monkeypatch.setenv(f"DIGITALPSYCHOLOGY_INGESTION_KEY_{producer_key}", "secret-a-rotated")
    replay = {**event, "event_id": "after-rotation"}
    body = {k: v for k, v in replay.items() if k != "host_attestation"}
    replay["host_attestation"] = {
        "key_id": producer_key, "issuer": "host:a",
        "signature": hmac.new(b"secret-a-rotated", _canonical(body).encode(), hashlib.sha256).hexdigest()}
    assert service.ingest_behavior_event(replay)["accepted"]
    service.close()

def test_legacy_mcp_entrypoint_fails_closed_by_default() -> None:
    import os
    import subprocess
    import sys

    env = dict(os.environ)
    env.pop("DIGITALPSYCHOLOGY_ENABLE_LEGACY_MCP", None)
    result = subprocess.run(
        [sys.executable, "-c", "from lib.mcp_server import main; main()"],
        cwd=Path(__file__).resolve().parents[1], env=env,
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode != 0
    assert "disabled by default" in result.stderr


def test_authenticated_marker_is_written_in_same_transaction(tmp_path, monkeypatch):
    from lib.mcp_server import DigitalPsychologyService
    service = DigitalPsychologyService(event_db=tmp_path / "events.sqlite3")
    event = _event("instance-a").to_dict()
    service._sink.emit_authenticated(__import__("lib.feedback_loop", fromlist=["EventFactory"]).EventFactory.from_dict(event), authenticated=True)
    assert service._sink._conn.execute(
        "SELECT COUNT(*) FROM authenticated_events WHERE event_id = ?", (event["event_id"],)
    ).fetchone()[0] == 1
    service.close()


def test_origin_daily_quota_is_durable_and_resets_by_day(tmp_path):
    from datetime import datetime, timedelta, timezone
    from lib.feedback_loop import SQLiteSink
    db = tmp_path / "quota.sqlite3"
    sink = SQLiteSink(db, per_origin_daily_write_limit=1)
    first = _event("quota-a")
    sink.emit(first)
    second = _event("quota-a")
    second.event_id = "second"
    with pytest.raises(Exception, match="budget"):
        sink.emit(second)
    sink.close()
    restarted = SQLiteSink(db, per_origin_daily_write_limit=1)
    with pytest.raises(Exception, match="budget"):
        restarted.emit(second)
    restarted.close()
    conn = __import__("sqlite3").connect(db)
    day = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
    conn.execute("DELETE FROM origin_daily_quotas WHERE namespace_id=? AND application_instance_id=?",
                 ("test-namespace", "quota-a"))
    conn.commit(); conn.close()
    recovered = SQLiteSink(db, per_origin_daily_write_limit=1)
    recovered.emit(second); recovered.close()
