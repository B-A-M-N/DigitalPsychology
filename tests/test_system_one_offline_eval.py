import sqlite3
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from lib.protocol_artifact import PROTOCOL_VERSION, QUESTION_REGISTRY
from lib.system_one_contracts import (
    ActivityFact,
    CriterionState,
    QuestionSpec,
    SemanticState,
    SystemOneFrame,
    SystemOneIdentity,
    canonical_hash,
)
from lib.system_one_offline_eval import (
    _metrics,
    _frame_event_matches,
    _replay_frame,
    _union_find_split_manifest,
    _validate_split_manifest,
    run_offline_replay,
)


def _rows(count=5):
    rows = []
    for index in range(count):
        actual = "yes" if index % 2 else "no"
        probabilities = ({"no": 0.2, "yes": 0.8} if actual == "yes"
                         else {"no": 0.8, "yes": 0.2})
        rows.append({
            "task_instance_hash": f"task-{index}",
            "trajectory_id": f"trajectory-{index}",
            "fixture_set_hash": f"fixture-{index}",
            "decision_id": f"decision-{index}",
            "actual": actual,
            "selected": actual,
            "probabilities": probabilities,
            "host_baseline": "no",
        })
    return rows


def _legacy_frame_mapping():
    question_data = QUESTION_REGISTRY["progress_relation"]
    question = QuestionSpec(
        question_id="progress_relation", family="progress_relation",
        question_type="choice", instructions=question_data["instructions"],
        criteria=tuple(question_data["criteria"].items()))
    identity = SystemOneIdentity(
        namespace_id="tenant", application_id="app", application_instance_id="app-1",
        agent_id="agent", agent_instance_id="agent-1", session_id="session",
        task_id="task", attempt_id="attempt")
    activity = ActivityFact(
        sequence=1, activity_type="action", source="host_observed",
        action_class="verification", host_declared_relation="ambiguous",
        target_kind="workspace", proposed_purpose="verify the requested result",
        affected_criterion_ids=("criterion",))
    state = SemanticState(
        task={"objective": "Verify the requested artifact.", "work_mode": "review",
              "deliverable_kind": "document", "task_complexity": "standard",
              "phase": "review", "active_segment": "segment", "active_statework": None,
              "active_flow": None, "non_goals": [], "domain_tags": []},
        situation={"current_state": "REVIEW", "blockers": [],
                   "actions_since_goal_progress": 0, "actions_since_artifact_change": 0,
                   "verification_actions_since_artifact_change": 0, "recovery_epochs": 0,
                   "repeated_branch_count": 0},
        criteria=(CriterionState("criterion", "The artifact passes review.", "objective"),),
        activity=(activity,), workflows=(), convergence=(), candidate=activity)
    legacy_schema = json.loads((Path(__file__).parents[1] / "lib" / "schemas" /
                                "system-one-protocol-2.5.0.schema.json").read_text())
    raw = {
        "protocol_version": "2.5.0",
        "protocol_hash": canonical_hash(legacy_schema),
        "identity": identity.to_dict(),
        "request_id": "legacy-frame-request",
        "sequence": 1,
        "state_digest": "",
        "question_bundle_digest": canonical_hash([question.to_dict()]),
        "request_digest": "",
        "policy_hash": "c" * 64,
        "backend_id": "legacy-laya",
        "model_id": "model",
        "model_revision": "revision",
        "model_content_hash": "a" * 64,
        "provider_protocol_hash": "b" * 64,
        "question_registry_hash": canonical_hash(legacy_schema["x-question-registry"]),
        "state_projection_schema_hash": canonical_hash(
            legacy_schema["$defs"]["SemanticState"]),
        "calibration_bundle_hash": "",
        "state": state.to_dict(),
        "questions": [question.to_dict()],
    }
    raw["state"].pop("criteria_before")
    raw["state_digest"] = canonical_hash(raw["state"])
    raw["request_digest"] = canonical_hash({
        "identity": raw["identity"], "sequence": raw["sequence"],
        "state_digest": raw["state_digest"],
        "question_bundle_digest": raw["question_bundle_digest"],
        "policy_hash": raw["policy_hash"],
    })
    return raw


def test_split_manifest_groups_and_validates_independent_clusters():
    rows = _rows()
    manifest = _union_find_split_manifest(rows, "goal_drift")
    assert manifest["status"] == "available"
    assert set(manifest["assignments"].values()) == {
        "training", "calibration", "holdout"}
    checked = _validate_split_manifest(manifest, rows, "goal_drift")
    assert checked["manifest_digest"] == manifest["manifest_digest"]


def test_offline_replay_normalizes_authenticated_legacy_frame_without_prior_snapshot():
    raw = _legacy_frame_mapping()
    with pytest.raises(ValueError, match="projection registry"):
        SystemOneFrame.from_mapping(raw)

    replay = _replay_frame(raw)

    assert replay.protocol_version == PROTOCOL_VERSION
    assert replay.state.criteria_before is None
    assert replay.state.to_dict()["criteria_before"] is None
    identity = raw["identity"]
    event = SimpleNamespace(
        event_type="system_one_decision", category="decision",
        actor_id="digitalpsychology_runtime",
        authority_source="digitalpsychology_system_one", event_id="event-1",
        scope="scope-1", payload={
            "system_one_tick_id": "tick-1",
            "system_one_decision_id": raw["request_id"],
            "system_one_state_digest": raw["state_digest"],
            "system_one_question_bundle_hash": raw["question_bundle_digest"],
        }, **identity)
    assert _frame_event_matches(
        replay, event, "tick-1", ("event-1",), source_frame=raw)
    tampered = {**raw, "state_digest": "0" * 64}
    with pytest.raises(ValueError, match="content digest"):
        _replay_frame(tampered)


def test_split_manifest_rejects_trajectory_leakage_even_with_valid_digest():
    rows = _rows()
    rows[1]["trajectory_id"] = rows[0]["trajectory_id"]
    manifest = _union_find_split_manifest(rows, "goal_drift")
    assignments = dict(manifest["assignments"])
    assignments["task-1"] = "holdout" if assignments["task-0"] != "holdout" else "training"
    changed = {key: value for key, value in manifest.items()
               if key != "manifest_digest"}
    changed["assignments"] = assignments
    changed["manifest_digest"] = canonical_hash(changed)
    with pytest.raises(ValueError, match="leaks a trajectory or fixture"):
        _validate_split_manifest(changed, rows, "goal_drift")


def test_replay_metrics_include_confusion_calibration_and_host_baseline():
    rows = _rows(4)
    metrics = _metrics(rows, ("no", "yes"), majority_label="no",
                       split_name="holdout")
    assert metrics["top1_accuracy"] == 1.0
    assert metrics["confusion_matrix"]["yes"]["yes"] == 2
    assert metrics["ece_bin_count"] == 10
    assert metrics["host_baseline_count"] == 4
    assert metrics["training_majority_label"] == "no"


def test_replay_metric_ties_are_independent_of_probability_insertion_order():
    first = _rows(1)[0]
    first["probabilities"] = {"yes": 0.5, "no": 0.5}
    second = dict(first, probabilities={"no": 0.5, "yes": 0.5})
    forward = _metrics([first], ("no", "yes"), majority_label=None,
                       split_name="holdout")
    reverse = _metrics([second], ("no", "yes"), majority_label=None,
                       split_name="holdout")
    assert forward["confusion_matrix"] == reverse["confusion_matrix"]


def test_overconfident_wrong_prediction_receives_large_proper_score_penalties():
    row = _rows(1)[0]
    row["probabilities"] = {"no": 0.0001, "yes": 0.9999}
    row["selected"] = "yes"

    metrics = _metrics([row], ("no", "yes"), majority_label="no",
                       split_name="holdout")

    assert metrics["top1_accuracy"] == 0.0
    assert metrics["multiclass_brier_score"] > 1.9
    assert metrics["top_label_brier_score"] > 0.99
    assert metrics["log_loss"] > 5.0


def test_empty_authenticated_corpus_reports_no_replay_without_loading_laya(tmp_path):
    event_db = tmp_path / "events.sqlite3"
    with sqlite3.connect(event_db) as database:
        database.execute("CREATE TABLE events (row_id INTEGER PRIMARY KEY, namespace_id TEXT, application_instance_id TEXT, event_id TEXT, timestamp TEXT, payload TEXT)")
        database.execute("CREATE TABLE authenticated_events (namespace_id TEXT, application_instance_id TEXT, event_id TEXT)")
    runtime_db = tmp_path / "runtime.sqlite3"
    with sqlite3.connect(runtime_db) as database:
        database.execute("CREATE TABLE control_ticks (scope TEXT, tick_id TEXT, basis_json TEXT, result_json TEXT, status TEXT, created_at TEXT)")

    report = run_offline_replay(event_db, runtime_db, "progress_relation")
    assert report["provenance"] == "authenticated_real_trace"
    assert report["paired_independent_outcomes"] == 0
    assert report["replayed_inferences"] == 0
    assert report["model_api_calls"] == 0
    assert report["qualification"] == "not evaluated; no threshold or checkpoint changes"
    assert report["split_status"] == "descriptive_only"
