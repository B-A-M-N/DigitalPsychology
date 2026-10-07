from __future__ import annotations

import importlib.machinery
import json
from types import SimpleNamespace

import pytest

from lib import system_one_cli
from lib.system_one_contracts import (QuestionAnswer, QuestionProbability,
                                      SystemOneJudgment, canonical_hash)


def test_laya_doctor_checks_pinned_checkpoint_without_loading_weights(tmp_path, monkeypatch):
    checkpoint = tmp_path / "checkpoint"
    checkpoint.mkdir()
    digest = "a" * 64
    monkeypatch.setattr(system_one_cli.importlib.util, "find_spec",
                        lambda name: importlib.machinery.ModuleSpec(name, loader=None))
    monkeypatch.setattr(system_one_cli, "model_content_digest",
                        lambda path: digest)
    report = system_one_cli.laya_doctor_report({
        "DIGITALPSYCHOLOGY_LAYA_MODEL_PATH": str(checkpoint),
        "DIGITALPSYCHOLOGY_LAYA_MODEL_REVISION": "pinned-revision",
        "DIGITALPSYCHOLOGY_LAYA_MODEL_SHA256": digest,
        "DIGITALPSYCHOLOGY_LAYA_DEVICE": "cpu",
        "DIGITALPSYCHOLOGY_SYSTEM_ONE_MODE": "shadow",
        "DIGITALPSYCHOLOGY_SYSTEM_ONE_QUESTIONS": "progress_relation",
        "PRIVATE_ACCESS_TOKEN": "must-not-print",
    })
    assert report["shadow_readiness"] == "PASS"
    assert report["advisory_readiness"] == "FAIL"
    output = system_one_cli._format_doctor(report)
    assert "progress_relation calibration: MISSING" in output
    assert "PRIVATE_ACCESS_TOKEN" not in output
    assert "must-not-print" not in output


def test_laya_doctor_does_not_claim_advisory_ready_without_runtime(
        tmp_path, monkeypatch):
    from lib import system_one_calibration

    checkpoint = tmp_path / "checkpoint"
    checkpoint.mkdir()
    calibration_path = tmp_path / "calibration.json"
    calibration_path.write_text("{}", encoding="utf-8")
    digest = "a" * 64
    record = SimpleNamespace(
        backend_id="digitalpsychology-laya-native",
        model_id="laya-typed-decisions",
        model_revision="pinned-revision",
        model_content_hash=digest,
        provider_protocol_hash=system_one_cli.PROVIDER_PROTOCOL_HASH,
        qualification_passed=True,
        drift_status="clear",
    )
    bundle = SimpleNamespace(records={"progress_relation": record})
    monkeypatch.setattr(system_one_cli.importlib.util, "find_spec",
                        lambda _name: None)
    monkeypatch.setattr(system_one_cli, "model_content_digest",
                        lambda _path: digest)
    monkeypatch.setattr(
        system_one_calibration.CalibrationBundle, "from_mapping",
        classmethod(lambda _cls, _raw: bundle),
    )

    report = system_one_cli.laya_doctor_report({
        "DIGITALPSYCHOLOGY_LAYA_MODEL_PATH": str(checkpoint),
        "DIGITALPSYCHOLOGY_LAYA_MODEL_REVISION": "pinned-revision",
        "DIGITALPSYCHOLOGY_LAYA_MODEL_SHA256": digest,
        "DIGITALPSYCHOLOGY_LAYA_DEVICE": "cpu",
        "DIGITALPSYCHOLOGY_SYSTEM_ONE_MODE": "advisory",
        "DIGITALPSYCHOLOGY_SYSTEM_ONE_QUESTIONS": "progress_relation",
        "DIGITALPSYCHOLOGY_SYSTEM_ONE_CALIBRATION_PATH": str(calibration_path),
    })

    assert report["qualified_question_families"]["progress_relation"] == "ACTIVE"
    assert report["shadow_readiness"] == "FAIL"
    assert report["advisory_readiness"] == "FAIL"


def test_laya_qualification_cli_cannot_weaken_production_minimums(tmp_path):
    with pytest.raises(SystemExit) as error:
        system_one_cli.laya_qualify_main([
            "--question-family", "progress_relation",
            "--event-db", str(tmp_path / "does-not-exist.sqlite3"),
            "--output", str(tmp_path / "candidate.json"),
            "--minimum-outcomes", "99",
        ])
    assert error.value.code == 2
    assert not (tmp_path / "candidate.json").exists()


def test_laya_doctor_advisory_exit_gate_can_require_all_enabled_families(monkeypatch,
                                                                         capsys):
    report = {
        "mode": "advisory", "shadow_readiness": "PASS",
        "advisory_readiness": "PARTIAL", "qualified_question_families": {},
        "advisory_shadow_only_families": ["retry_relation"],
    }
    monkeypatch.setattr(system_one_cli, "laya_doctor_report", lambda: dict(report))

    assert system_one_cli.laya_doctor_main(["--json"]) == 0
    default_output = capsys.readouterr().out
    assert '"required_mode": "advisory"' in default_output
    assert system_one_cli.laya_doctor_main(["--require-all-enabled-families", "--json"]) == 1
    strict_output = capsys.readouterr().out
    assert '"required_readiness_pass": false' in strict_output


def test_laya_doctor_can_explicitly_require_shadow_readiness(monkeypatch, capsys):
    monkeypatch.setattr(system_one_cli, "laya_doctor_report", lambda: {
        "mode": "advisory", "shadow_readiness": "PASS",
        "advisory_readiness": "FAIL", "qualified_question_families": {},
        "advisory_shadow_only_families": [],
    })

    assert system_one_cli.laya_doctor_main(["--require", "advisory", "--json"]) == 1
    capsys.readouterr()
    assert system_one_cli.laya_doctor_main(["--require", "shadow", "--json"]) == 0
    output = capsys.readouterr().out
    assert '"required_mode": "shadow"' in output


def test_laya_smoke_runs_one_typed_shadow_judgment_and_closes_provider(monkeypatch):
    content_hash = "a" * 64
    provider_protocol_hash = system_one_cli.PROVIDER_PROTOCOL_HASH

    class FakeProvider:
        backend_id = "digitalpsychology-laya-native"
        model_id = "laya-typed-decisions"
        model_revision = "pinned-revision"
        model_content_hash = content_hash
        device = "cpu"
        warmed = False
        closed = False

        def warmup(self):
            self.warmed = True

        def warmup_and_wait(self):
            self.warmed = True
            return 1

        def close(self):
            self.closed = True

    provider = FakeProvider()
    monkeypatch.setattr(system_one_cli.LayaProvider, "from_environment",
                        classmethod(lambda _cls: provider))

    class FakeService:
        calibration_bundle_hash = ""

        def capability(self):
            return {
                "backend_id": provider.backend_id,
                "model_id": provider.model_id,
                "model_revision": provider.model_revision,
                "model_content_hash": provider.model_content_hash,
                "provider_protocol_hash": provider_protocol_hash,
            }

        def judge(self, frame, *, mode):
            assert mode == "shadow"
            spec = frame.questions[0]
            labels = tuple(label for label, _description in spec.criteria)
            answer = QuestionAnswer(
                question_id=spec.question_id, selected=labels[0],
                raw_probabilities=tuple(QuestionProbability(label, 1 / len(labels))
                                         for label in labels),
            )
            judgment = SystemOneJudgment(
                identity=frame.identity, request_id=frame.request_id,
                sequence=frame.sequence, state_digest=frame.state_digest,
                question_bundle_digest=frame.question_bundle_digest,
                request_digest=frame.request_digest, policy_hash=frame.policy_hash,
                backend_id=frame.backend_id, model_id=frame.model_id,
                model_revision=frame.model_revision,
                model_content_hash=frame.model_content_hash,
                provider_protocol_hash=frame.provider_protocol_hash,
                calibration_bundle_hash=frame.calibration_bundle_hash,
                answers=(answer,),
            )
            return SimpleNamespace(status="shadow", judgment=judgment)

        def close(self):
            provider.close()

    monkeypatch.setattr(system_one_cli, "SystemOneService",
                        lambda *_args, **_kwargs: FakeService())
    report = system_one_cli.laya_smoke_report("progress_relation")

    assert report["success"] is True
    assert report["question_family"] == "progress_relation"
    assert report["checkpoint_hash"] == content_hash
    assert report["efficacy_claim"] is False
    assert provider.warmed is True
    assert provider.closed is True


def test_laya_smoke_reports_provider_failure_without_efficacy_claim(monkeypatch):
    class BrokenProvider:
        @classmethod
        def from_environment(cls):
            raise RuntimeError("checkpoint unavailable")

    monkeypatch.setattr(system_one_cli, "LayaProvider", BrokenProvider)
    report = system_one_cli.laya_smoke_report()

    assert report["success"] is False
    assert report["efficacy_claim"] is False
    assert "checkpoint unavailable" in report["error"]


def test_calibration_corpus_manifest_binds_candidate_and_task_instances(tmp_path):
    path = tmp_path / "calibration-corpus.json"
    hashes = ("b" * 64, "a" * 64)
    manifest = system_one_cli._write_corpus_manifest(
        path, corpus_id="qualification-2026-10", question_family="progress_relation",
        candidate_digest="c" * 64, task_instance_hashes=hashes)

    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted == manifest
    assert persisted["task_instance_hashes"] == sorted(hashes)
    assert persisted["fixture_set_hash"] == canonical_hash(
        {"task_instance_hashes": sorted(hashes)})
    assert persisted["manifest_digest"] == canonical_hash({
        key: value for key, value in persisted.items() if key != "manifest_digest"})
    assert path.stat().st_mode & 0o777 == 0o600
