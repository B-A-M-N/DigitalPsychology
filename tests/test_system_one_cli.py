from __future__ import annotations

import importlib.machinery
from types import SimpleNamespace

import pytest

from lib import system_one_cli


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
