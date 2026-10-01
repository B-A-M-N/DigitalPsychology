import json
import tempfile
from pathlib import Path
from datetime import datetime, timezone
import pytest

from lib.feedback_loop import Event, EventError
from lib.routing_profiles import RoutingProfileError, derive_profiles
from lib.trajectories import build_trajectories


def _event(agent, *, event_id="same", app="app", namespace="ns", role="delegator", interaction=None, outcome="FAIL"):
    return Event(event_id=event_id, timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat(), task_id="task", agent_id=agent, agent_instance_id=agent, session_id="same-session", attempt_id="attempt-1", behavioral_subject="subject", namespace_id=namespace, application_id=app, application_instance_id=app, category="decision", event_type="route_outcome", role=role, interaction_id=interaction, payload={"route": "flow", "route_type": "stage", "route_decision_id": "d", "outcome": outcome, "outcome_source": "evaluator:e", "evaluator_id": "e", "candidate_profile_hash": "x", "candidate_adjustment_applied": False, "policy_identity": "c", "experiment_plan_hash": "p", "comparison_context_hash": "c"})


def test_overlapping_ids_are_distinct_trajectories():
    _episodes, tasks, _sessions, agents = build_trajectories([_event("agent-1"), _event("agent-2")])
    assert len(tasks) == 2
    assert len(agents) == 2
    assert len({task.trajectory_id for task in tasks}) == 2


def test_same_event_id_across_origins_is_not_merged():
    _episodes, tasks, _sessions, _agents = build_trajectories([_event("agent-1"), _event("agent-2")])
    assert all(len(task.events) == 1 for task in tasks)


def test_mismatched_sidecar_identity_fails_closed():
    event = _event("agent-1")
    plan = {"plan_hash": "p", "target_evaluator": "e", "target_evaluator_version": "1", "target_evaluator_hash": "h", "candidate_profile_hash": "x", "treatment_policy_identity": "t", "control_policy_identity": "c", "holdout_requirements": {"minimum_samples": 1, "evaluators": []}}
    context = {"namespace_id": "ns", "application_id": "app", "application_instance_id": "app", "agent_instance_id": "agent-2", "session_id": "same-session", "task_id": "task", "attempt_id": "attempt-1", "behavioral_subject": "subject", "role": "delegator", "interaction_id": "", "delegation_id": "", "routing_experiment_plan": plan, "routing_cohort": "control", "comparison_context_hash": "c"}
    key = "ns|app|agent-1|same-session|task|attempt-1|subject|delegator|None|None"
    with pytest.raises(RoutingProfileError, match="authenticated event identity"):
        derive_profiles([event], {key: context}, minimum_samples=1)


def test_shared_interaction_cannot_cross_cohorts():
    from lib.routing_profiles import build_profile, build_experiment_plan
    adjustment = {"route_type": "stage", "route": "flow", "disposition": "suppress",
                  "condition": {}, "evidence_refs": ["predeclared"]}
    identity = {
        "namespace_id": "ns", "application_id": "app", "application_version": "1",
        "application_instance_id": "app", "provider_id": "p", "model_id": "m",
        "model_revision": "1", "model_capability_hash": "c", "harness_id": "h",
        "harness_version": "1", "agent_instance_id": "agent-1",
    }
    context = {"task_family": "performance", "task_shape": "performance", "domain_tags": [],
        "phase": None, "environment": "test", "toolset": "fixture",
        "statework_versions": {}, "framework_version": "f", "guard_pack_hash": "g",
        "role": "delegator", "interaction_id": "shared", "delegation_id": "d"}
    subject = identity
    candidate = build_profile(
        profile_id="shared-interaction", subject=subject, context=context, observations=[],
        routing_adjustments=[adjustment], evidence={"event_ids": ["e"], "trajectory_ids": [],
        "source_hash": "s", "experiment_id": "shared", "candidate_adjustment": adjustment,
        "receipt_ref": None}, criterion={"evaluator_version": "routing-outcome-v2",
        "minimum_samples": 1, "minimum_effect": 0.1,
        "confidence_rule": "independent trajectory effect lower confidence bound is non-negative"})
    plan = build_experiment_plan(experiment_id="shared", candidate_profile_hash=candidate["profile_hash"],
        candidate_profile_id="shared", candidate_adjustment=adjustment,
        eligibility={"field": "task_family", "equals": "performance"}, target_evaluator="e",
        control_policy_identity="c", treatment_policy_identity="t", canary_fraction=0.5,
        holdout_requirements={"evaluators": ["e@1.0.0"], "minimum_samples": 1})
    def event(agent, cohort, applied):
        data = {**identity, **context, "task_id": "task", "session_id": "session", "attempt_id": "attempt-1",
                "agent_id": agent, "agent_instance_id": agent, "behavioral_subject": "subject",
                "routing_experiment_plan": plan, "routing_cohort": cohort,
                "candidate_profile_hash": plan["candidate_profile_hash"],
                "candidate_adjustment_applied": applied, "policy_hash": "p",
                "comparison_context_hash": "p"}
        return Event(event_id=f"e-{agent}-{cohort}", timestamp="2026-01-01T00:00:00Z",
            task_id="task", agent_id=agent, agent_instance_id=agent, session_id="session",
            attempt_id="attempt-1", behavioral_subject="subject", namespace_id="ns",
            application_id="app", application_instance_id="app", category="decision",
            event_type="route_outcome", role="delegator", interaction_id="shared", delegation_id="d",
            payload={"route": "flow", "route_type": "stage", "route_decision_id": "d",
            "outcome": "FAIL" if cohort == "control" else "PASS", "outcome_source": "evaluator:e",
            "evaluator_id": "e", "evaluator_version": "1", "evaluator_hash": plan["target_evaluator_hash"],
            "candidate_profile_hash": plan["candidate_profile_hash"],
            "candidate_adjustment_applied": applied, "policy_identity": "t" if applied else "c",
            "experiment_plan_hash": plan["plan_hash"], "comparison_context_hash": "p"}), data
    events=[]; contexts={}
    for index, (agent, cohort, applied) in enumerate((
        ("agent-1", "control", False), ("agent-2", "control", False),
        ("agent-3", "treatment", True), ("agent-4", "treatment", True),
    )):
        ev, data = event(agent, cohort, applied)
        ev.event_id = f"e-{index}"
        data["candidate_profile_hash"] = plan["candidate_profile_hash"]
        events.append(ev)
        contexts[f"ns|app|{ev.agent_instance_id}|session|task|attempt-1|subject|delegator|shared|d"] = data
    with pytest.raises(RoutingProfileError, match="shared interaction"):
        derive_profiles(events, contexts, minimum_samples=1)


def test_legacy_context_is_importable_but_ineligible_for_promotion():
    event = _event("agent-1", interaction="interaction-1")
    context = {"namespace_id": "ns", "application_instance_id": "app",
               "agent_instance_id": "agent-1", "session_id": "same-session",
               "task_id": "task", "attempt_id": "attempt-1",
               "behavioral_subject": "subject", "routing_cohort": "control"}
    assert derive_profiles([event], {"same-session:task:attempt-1": context}, minimum_samples=1) == []


def test_reused_interaction_id_is_origin_scoped():
    events = [_event("agent-1", app="app-a", namespace="ns-a", interaction="shared"),
              _event("agent-1", app="app-b", namespace="ns-a", interaction="shared")]
    _episodes, _tasks, _sessions, _agents = build_trajectories(events)
    from lib.trajectories import build_interaction_trajectories
    assert len(build_interaction_trajectories(_sessions)) == 2


def test_persisted_event_retrieval_requires_complete_origin():
    from datetime import datetime, timezone
    from lib.feedback_loop import EventFactory
    from lib.mcp_server import DigitalPsychologyService
    with tempfile.TemporaryDirectory() as raw:
        service = DigitalPsychologyService(event_db=Path(raw) / "events.sqlite3")
        event = EventFactory.from_dict({
            "schema_version": "2.5.0", "event_id": "same-event",
            "timestamp": datetime.now(timezone.utc).isoformat(), "namespace_id": "ns-a",
            "application_id": "app", "application_instance_id": "instance-a",
            "task_id": "task", "agent_id": "agent", "actor_id": "agent",
            "agent_instance_id": "agent", "session_id": "session", "attempt_id": "attempt", "segment_id": "segment-0",
            "behavioral_subject": "subject", "category": "decision", "event_type": "route_outcome",
        })
        service._sink.emit(event)
        service._sink._conn.execute(
            "INSERT INTO authenticated_events(namespace_id, application_instance_id, event_id) VALUES (?,?,?)",
            ("ns-a", "instance-a", "same-event"))
        service._sink._conn.commit()
        loaded = service._load_persisted_events(
            ["same-event"], namespace_id="ns-a", application_instance_id="instance-a")
        assert loaded[0].event_id == "same-event"
        for namespace, instance in (("", "instance-a"), ("ns-a", ""), ("ns-b", "instance-a")):
            try:
                service._load_persisted_events(
                    ["same-event"], namespace_id=namespace, application_instance_id=instance)
            except ValueError:
                pass
            else:
                raise AssertionError("incomplete or wrong event origin accepted")
        service.close()


def _deployable_profile(profile_id="lineage-test", disposition="prefer"):
    from lib.routing_profiles import build_profile
    profile = build_profile(
        profile_id=profile_id,
        subject={"namespace_id":"n","application_id":"a","application_version":"1",
                 "application_instance_id":"i","provider_id":"p","model_id":"m",
                 "model_revision":"r","model_capability_hash":"c","harness_id":"h",
                 "harness_version":"v","agent_instance_id":"agent"},
        context={"task_family":"performance"},
        observations=[],
        routing_adjustments=[{"route_type":"stage","route":"flow","disposition":disposition,
                              "condition":{},"evidence_refs":["e"]}],
        evidence={"event_ids":["e"],"trajectory_ids":[],"source_hash":"s",
                  "experiment_id":"exp","candidate_adjustment":{},
                  "receipt_ref":None},
        criterion={"evaluator_version":"v","minimum_samples":1,"minimum_effect":0.1,
                   "confidence_rule":"test"})
    from lib.routing_profiles import _canonical, profile_semantic_hash, promote_profile
    import hashlib
    receipt = {
        "receipt_id": f"receipt-{profile_id}", "kind": "routing_experiment",
        "decision": "pass", "profile_candidate_hash": profile_semantic_hash(profile),
        "source_evidence_hash": profile["evidence"]["source_hash"],
        "experiment_id": profile["evidence"]["experiment_id"],
    }
    receipt["receipt_hash"] = hashlib.sha256(_canonical(receipt).encode()).hexdigest()
    return promote_profile(profile, receipt, status="validated")


def test_lifecycle_lineage_survives_revision_and_restart(tmp_path):
    from lib.routing_lifecycle import RoutingLifecycleStore
    store = RoutingLifecycleStore(tmp_path / "state.json")
    profile = store.deploy(_deployable_profile("lineage-restart"))
    lineage = profile["deployment_lineage_id"]
    observed = store.observe(profile, [{"profile_hash": profile["profile_hash"],
                                         "trajectory_id":"t1","eligible":True}])
    assert observed["lifecycle_revision"] == profile["lifecycle_revision"] + 1
    assert observed["deployment_lineage_id"] == lineage
    restarted = RoutingLifecycleStore(tmp_path / "state.json")
    assert restarted.observe(observed, [])["deployment_lineage_id"] == lineage


def test_unknown_routing_lifecycle_status_is_not_active(tmp_path):
    from lib.routing_lifecycle import RoutingLifecycleStore

    store = RoutingLifecycleStore(tmp_path / "state.json")
    store.set_lifecycle_status(source_profile_hash="profile-x", status="future-status")
    assert not store.is_active(source_profile_hash="profile-x")
    store.set_lifecycle_status(source_profile_hash="profile-x", status="active")
    assert store.is_active(source_profile_hash="profile-x")
    store.set_lifecycle_status(source_profile_hash="profile-x", status="validated")
    assert store.is_active(source_profile_hash="profile-x")


def test_fallback_requires_explicit_known_good_profile(tmp_path):
    from lib.mcp_server import DigitalPsychologyService
    from lib.routing_lifecycle import RoutingLifecycleStore
    store = RoutingLifecycleStore(tmp_path / "state.json")
    first = store.deploy(_deployable_profile("known-good"))
    second = store.deploy(_deployable_profile("new-profile"))
    artifact = tmp_path / "active-routing-profile.json"
    payload = {
        "revision": second["lifecycle_revision"],
        "profile": {**second, "status": "stale", "lifecycle_status": "stale"},
        "previous_profile": first,
    }
    artifact.write_text(json.dumps(payload), encoding="utf-8")
    service = DigitalPsychologyService(event_db=tmp_path / "events.sqlite3", profile_path=artifact)
    try:
        profiles, _ = service._load_profiles()
        assert profiles == []
        payload["known_good_profile"] = first
        payload["known_good_revision"] = first["lifecycle_revision"]
        artifact.write_text(json.dumps(payload), encoding="utf-8")
        try:
            profiles, _ = service._load_profiles()
        except Exception as exc:
            raise AssertionError(f"explicit known-good fallback was rejected: {exc}")
        assert profiles[0]["profile_hash"] == first["profile_hash"]
    finally:
        service.close()


def test_provenance_and_comparison_context_are_distinct():
    from lib.routing_profiles import comparison_context, provenance_identity
    event = _event("agent", interaction="interaction-1")
    context = {
        "namespace_id": "ns", "application_id": "app", "application_instance_id": "app",
        "model_id": "model", "task_family": "performance", "role": "delegate",
        "interaction_id": "interaction-1", "delegation_id": "d1",
    }
    provenance = provenance_identity(event)
    comparable = comparison_context(context)
    assert provenance["interaction_id"] == "interaction-1"
    assert "interaction_id" not in comparable
    assert "delegation_id" not in comparable
    assert comparable["model_id"] == "model"
    assert comparable["task_family"] == "performance"


def test_all_inherited_treatment_failures_cannot_create_individual_profile():
    from lib.routing_profiles import RoutingProfileError, validate_treatment_failure_attribution
    with pytest.raises(RoutingProfileError, match="inherited from shared premises"):
        validate_treatment_failure_attribution([
            {"failure_attribution": "inherited", "premise_digest": "premise-1"},
            {"failure_attribution": "inherited", "premise_digest": "premise-1"},
        ])
    validate_treatment_failure_attribution([
        {"failure_attribution": "inherited", "premise_digest": "premise-1"},
        {"failure_attribution": "independent"},
    ])


def _registration_hash(record):
    from lib.mcp_server import _canonical
    import hashlib
    body = dict(record)
    body.pop("registration_hash", None)
    return hashlib.sha256(_canonical(body).encode()).hexdigest()


def test_experiment_registration_is_unique_hashed_and_revision_bound(tmp_path, monkeypatch):
    import os
    from lib.mcp_server import DigitalPsychologyService, _canonical
    import hashlib
    monkeypatch.setenv("DIGITALPSYCHOLOGY_STATE_ROOT", str(tmp_path))
    monkeypatch.setenv("DIGITALPSYCHOLOGY_PROMOTION_SECRET", "promotion-secret")
    base = {
        "experiment_id": "exp", "namespace_id": "ns",
        "application_instance_id": "instance", "registration_revision": "rev-1",
        "promotion_authorization_id": "auth-1", "event_ids": [], "task_context": {},
    }
    base["registration_hash"] = _registration_hash(base)
    (tmp_path / "experiments.json").write_text(json.dumps({"exp": base}), encoding="utf-8")
    service = DigitalPsychologyService(event_db=tmp_path / "events.sqlite3")
    monkeypatch.setattr(service, "_run_slow_loop", lambda events, contexts: {"status": "completed", "events": len(events), "contexts": len(contexts)})
    authorization = {
        "authorization_id": "auth-1", "namespace_id": "ns",
        "application_instance_id": "instance", "registration_revision": "rev-1",
        "registration_hash": base["registration_hash"],
    }
    # Sign only the service-defined authorization body.
    import hmac
    body = dict(authorization)
    authorization["signature"] = hmac.new(b"promotion-secret", _canonical(body).encode(), hashlib.sha256).hexdigest()
    try:
        result = service.run_registered_slow_loop("exp", authorization)
        assert result["status"] == "completed"
        for mutation in (
            lambda a: a.pop("namespace_id"),
            lambda a: a.update(registration_revision="rev-other"),
        ):
            broken = dict(authorization)
            body = {k: v for k, v in broken.items() if k != "signature"}
            mutation(body)
            body["signature"] = hmac.new(b"promotion-secret", _canonical(body).encode(), hashlib.sha256).hexdigest()
            try:
                service.run_registered_slow_loop("exp", body)
            except ValueError:
                pass
            else:
                raise AssertionError("invalid experiment registration/authorization accepted")
        # Two exact-origin records are ambiguous even when their IDs differ.
        duplicate = {**base, "registration_revision": "rev-2"}
        duplicate["registration_hash"] = _registration_hash(duplicate)
        (tmp_path / "experiments.json").write_text(json.dumps([base, duplicate]), encoding="utf-8")
        try:
            service.run_registered_slow_loop("exp", authorization)
        except ValueError as exc:
            assert "unique" in str(exc)
        else:
            raise AssertionError("duplicate exact-origin experiment accepted")
    finally:
        service.close()


def test_slow_loop_report_separates_lifecycle_and_evaluator_feedback(tmp_path, monkeypatch):
    from lib.slow_loop import BoundedLearningController

    passing = {
        "profile_id": "passing",
        "profile_hash": "p" * 64,
        "evidence": {
            "experiment_id": "exp-pass",
            "event_ids": ["event-1"],
            "trajectory_ids": ["trajectory-1"],
            "experiment_receipt": {
                "decision": "pass",
                "receipt_id": "receipt-1",
                "baseline_rate": 0.25,
                "intervention_rate": 0.75,
                "effect": 0.5,
                "baseline_count": 4,
                "intervention_count": 4,
                "holdout_count": 4,
            },
        },
    }
    rejected = {
        "profile_id": "rejected",
        "profile_hash": "r" * 64,
        "evidence": {
            "experiment_id": "exp-reject",
            "event_ids": ["event-2"],
            "trajectory_ids": ["trajectory-2"],
            "experiment_receipt": {"decision": "fail"},
        },
    }

    class Lifecycle:
        def deploy(self, profile, *, reason):
            return {**profile, "lifecycle_revision": 7}

    monkeypatch.setattr("lib.slow_loop.derive_profiles", lambda *args, **kwargs: [passing, rejected])
    monkeypatch.setattr("lib.slow_loop.promote_profile", lambda profile, receipt, status: {**profile, "status": status})
    report = BoundedLearningController(lifecycle=Lifecycle()).run([], {})
    assert [item["profile_id"] for item in report.deployment_exposures] == ["passing", "rejected"]
    assert [item["completed"] for item in report.observations_completed] == [True, True]
    assert report.intervention_effectiveness[0]["effect"] == 0.5
    assert report.rejection_reasons == [{"profile_id": "rejected", "reason": "candidate lacks a passing experiment receipt"}]
    assert {item["type"] for item in report.events} >= {
        "deployment_exposed", "observation_completed", "intervention_effectiveness",
        "deployment_published", "candidate_rejected",
    }
    # A passing receipt yields zero regressions: every candidate trajectory is
    # NOT a regression.
    assert report.regressions == []


def test_shared_profile_requires_authenticated_scope_and_explicit_adoption():
    import hashlib
    from lib.routing_profiles import (_canonical, BehavioralRoutingProfile,
                                      SharingDecisionAuthority, applicable_adjustments, build_profile,
                                      profile_semantic_hash, promote_profile)
    profile = build_profile(
        profile_id="shared", subject={"namespace_id":"ns","application_id":"app",
        "application_version":"1","application_instance_id":"producer","provider_id":"p",
        "model_id":"m","model_revision":"1","model_capability_hash":"c",
        "harness_id":"h","harness_version":"1","agent_instance_id":"producer"},
        context={"task_family":"performance","role":"delegate"}, observations=[],
        routing_adjustments=[{"route_type":"stage","route":"flow","disposition":"suppress",
                              "condition":{},"evidence_refs":["e"]}],
        evidence={"event_ids":["e"],"trajectory_ids":[],"source_hash":"s",
                  "experiment_id":"exp","candidate_adjustment":{},"receipt_ref":None},
        criterion={"evaluator_version":"v","minimum_samples":1,"minimum_effect":0.1,
                   "confidence_rule":"test"})
    receipt = {"receipt_id":"r","kind":"routing_experiment","decision":"pass",
               "profile_candidate_hash":profile_semantic_hash(profile),
               "source_evidence_hash":"s","experiment_id":"exp"}
    receipt["receipt_hash"] = hashlib.sha256(_canonical(receipt).encode()).hexdigest()
    private = promote_profile(profile, receipt, status="validated")
    consumer = {"namespace_id":"ns","application_id":"app","application_version":"1",
                "application_instance_id":"consumer","provider_id":"p","model_id":"m",
                "model_revision":"1","model_capability_hash":"c","harness_id":"h",
                "harness_version":"1","agent_instance_id":"consumer","task_family":"performance",
                "role":"delegate","adopted_profile_hashes":(private["profile_hash"],)}
    private_profile = BehavioralRoutingProfile.from_dict(private, require_deployable=True)
    assert applicable_adjustments(private_profile, consumer) == []
    authority = SharingDecisionAuthority.ephemeral(key_id="host-1")
    decision = authority.sign_decision({
        "target_scope":"application_shared","profile_candidate_hash":profile["profile_hash"],
        "authorized_application_id":"app","authorization_id":"auth"})
    for missing in (None, {"key_id":"host-1"}):
        try:
            promote_profile(profile, receipt, status="validated",
                            sharing_scope="application_shared", promotion_decision=decision,
                            sharing_authority=missing)
        except Exception as exc:
            assert "authority" in str(exc) or "authenticated" in str(exc)
        else:
            raise AssertionError("shared promotion accepted without host authority")
    shared = promote_profile(profile, receipt, status="validated",
                             sharing_scope="application_shared", promotion_decision=decision,
                             sharing_authority=authority)
    shared_context = {**consumer, "adopted_profile_hashes": ()}
    shared_profile = BehavioralRoutingProfile.from_dict(
        shared, require_deployable=True, sharing_authority=authority)
    assert applicable_adjustments(
        shared_profile, shared_context, sharing_authority=authority) == []
    shared["adoption"] = {"mode":"explicit","authorized":True}
    shared["profile_hash"] = profile_semantic_hash(shared)
    shared_context["adopted_profile_hashes"] = (shared["profile_hash"],)
    shared_profile = BehavioralRoutingProfile.from_dict(
        shared, require_deployable=True, sharing_authority=authority)
    assert applicable_adjustments(
        shared_profile, shared_context, sharing_authority=authority)[0]["route"] == "flow"
    forged = dict(decision); forged["authorization_id"] = "other"
    try:
        promote_profile(profile, receipt, status="validated",
                        sharing_scope="application_shared", promotion_decision=forged,
                        sharing_authority=authority)
    except Exception:
        pass
    else:
        raise AssertionError("forged sharing decision accepted")


def test_multi_profile_registry_preserves_independent_active_profiles(tmp_path):
    from lib.routing_lifecycle import RoutingLifecycleStore
    store=RoutingLifecycleStore(tmp_path/"state.json")
    first=store.deploy(_deployable_profile("first-registry"))
    second=store.deploy(_deployable_profile("second-registry"))
    entries={item["profile_hash"]:item for item in store.list_registry()}
    assert first["profile_hash"] in entries and second["profile_hash"] in entries
    artifact=json.loads((tmp_path/"active-routing-profile.json").read_text())
    hashes={item["profile_hash"] for item in artifact["profiles"]}
    assert {first["profile_hash"],second["profile_hash"]} <= hashes
    store.adopt_profile(second, adopter_agent_id="agent-b", reason="eligible task")
    refreshed=json.loads((tmp_path/"active-routing-profile.json").read_text())
    assert len(refreshed["profiles"]) == len(artifact["profiles"])


def test_only_holdout_failures_are_reported_as_regressions(monkeypatch):
    from lib.slow_loop import BoundedLearningController

    candidate = {
        "profile_id": "with-regression",
        "profile_hash": "r" * 64,
        "evidence": {
            "event_ids": ["e1"],
            "trajectory_ids": ["t-control", "t-treatment", "t-holdout-ok"],
            "experiment_receipt": {
                "decision": "fail", "receipt_id": "rcpt-1",
                "regressions": ["t-holdout-bad"],
            },
        },
    }

    class Lifecycle:
        def deploy(self, profile, *, reason):
            return {**profile, "lifecycle_revision": 1}

    monkeypatch.setattr("lib.slow_loop.derive_profiles", lambda *a, **k: [candidate])
    monkeypatch.setattr("lib.slow_loop.promote_profile", lambda profile, receipt, status: profile)
    report = BoundedLearningController(lifecycle=Lifecycle()).run([], {})
    assert [item["trajectory_id"] for item in report.regressions] == ["t-holdout-bad"]
    assert {item["type"] for item in report.events} >= {"holdout_regression_observed"}


def test_expired_profile_stops_being_advisable_after_restart(tmp_path):
    import importlib
    from lib.mcp_server import DigitalPsychologyService
    store = importlib.import_module("lib.routing_lifecycle")
    from lib.routing_profiles import (_canonical, build_profile, promote_profile,
                                      profile_semantic_hash)
    adjustment = {"route_type": "stage", "route": "flow", "disposition": "prefer",
                  "condition": {}, "evidence_refs": ["e"],
                  "expires_after_samples": 2}
    candidate = build_profile(
        profile_id="expiry-advisory",
        subject={"namespace_id": "n", "application_id": "a", "application_version": "1",
                 "application_instance_id": "i", "provider_id": "p", "model_id": "m",
                 "model_revision": "r", "model_capability_hash": "c", "harness_id": "h",
                 "harness_version": "v", "agent_instance_id": "agent"},
        context={"task_family": "performance"}, observations=[],
        routing_adjustments=[adjustment],
        evidence={"event_ids": ["e"], "trajectory_ids": [], "source_hash": "s",
                  "experiment_id": "exp", "candidate_adjustment": adjustment,
                  "receipt_ref": None},
        criterion={"evaluator_version": "v", "minimum_samples": 1,
                   "minimum_effect": 0.1, "confidence_rule": "test"})
    receipt = {"receipt_id": "receipt-expiry", "kind": "routing_experiment",
               "decision": "pass", "profile_candidate_hash": profile_semantic_hash(candidate),
               "source_evidence_hash": "s", "experiment_id": "exp"}
    receipt["receipt_hash"] = __import__("hashlib").sha256(
        _canonical(receipt).encode()).hexdigest()
    profile = promote_profile(candidate, receipt, status="validated")
    lifecycle = store.RoutingLifecycleStore(tmp_path / "state.json")
    profile = lifecycle.deploy(profile)
    for index in range(2):
        observed = lifecycle.observe(profile, [{"profile_hash": profile["profile_hash"],
                                                "trajectory_id": f"t{index}", "eligible": True}])
    assert observed["status"] == "stale"
    deployment = tmp_path / "active-routing-profile.json"
    pack = json.loads(deployment.read_text())
    assert all(item.get("status") != "active" for item in pack["profiles"])
    service = DigitalPsychologyService(event_db=tmp_path / "events.sqlite3",
                                      profile_path=deployment)
    try:
        profiles, _ = service._load_profiles()
        assert profiles == []
        advice = service.behavioral_advice(
            context={"application_id": "a"}, eligible_routes=[{"route_type": "stage", "route": "flow"}],
            static_policy={"policy_hash": "p"})
        assert advice["adjustments"] == []
    finally:
        service.close()


def test_circular_cross_agent_agreement_is_not_independent_evidence():
    from lib.routing_profiles import RoutingProfileError, validate_treatment_failure_attribution
    with pytest.raises(RoutingProfileError, match="circular"):
        validate_treatment_failure_attribution([
            {"agent_instance_id": "agent-a", "premise_digest": "premise-1"},
            {"agent_instance_id": "agent-b", "premise_digest": "premise-1"},
        ])
    # Independent premises are fine.
    validate_treatment_failure_attribution([
        {"agent_instance_id": "agent-a", "premise_digest": "premise-1"},
        {"agent_instance_id": "agent-b", "premise_digest": "premise-2"},
    ])
