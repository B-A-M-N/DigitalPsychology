import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
CFW = ROOT.parent / "CognitiveFrameWorks"
sys.path.insert(0, str(CFW / "scripts"))
from lib.routing_profiles import BehavioralRoutingProfile
from runtime_contract.routing import applicable_adjustments as cfw_applicable
import lib.routing_profiles as rp
from lib.compatibility import (
    COMPATIBILITY, CONTROL_LIMITS, MAX_ACTIVE_STATEWORKS, MAX_GOAL_CRITERIA,
    advertised_control_capabilities, validate_control_capabilities,
)

def test_shared_compatibility_vectors_agree():
    vectors = json.loads((ROOT / "schemas" / "shared-compatibility-vectors.json").read_text())
    profile_doc = vectors["profile"]
    class Minimal:
        to_dict = lambda self: profile_doc
        sharing_scope = "cross_application"
        subject = profile_doc["subject"]
        context = profile_doc["context"]
        selector_semantics = profile_doc["selector_semantics"]
        profile_hash = profile_doc["profile_hash"]
        routing_adjustments = profile_doc["routing_adjustments"]
    profile = Minimal()
    original = rp.validate_profile; rp.validate_profile = lambda *args, **kwargs: None
    try:
        for case in vectors["cases"]:
            dp = bool(rp.applicable_adjustments(profile, case["context"]))
            cfw = bool(cfw_applicable(profile_doc, case["context"]))
            assert dp == cfw == case["expected"], (case["id"], dp, cfw)
    finally:
        rp.validate_profile = original


def test_control_capability_negotiation_fails_closed_on_version_or_limit_drift():
    capabilities = advertised_control_capabilities()
    validate_control_capabilities(capabilities)

    wrong_version = dict(capabilities)
    wrong_version["protocol_versions"] = {
        **capabilities["protocol_versions"], "csw_workflow_port": "1.3.0"}
    try:
        validate_control_capabilities(wrong_version)
    except ValueError as exc:
        assert "compatibility negotiation" in str(exc)
    else:
        raise AssertionError("incompatible workflow port version was accepted")

    wrong_limit = dict(capabilities, max_cfw_signals=64)
    try:
        validate_control_capabilities(wrong_limit)
    except ValueError as exc:
        assert "max_cfw_signals" in str(exc)
    else:
        raise AssertionError("incompatible signal limit was accepted")

    wrong_receipts = dict(capabilities, host_evaluator_receipt_schemas=[
        "host-evaluator-receipt/v2"])
    try:
        validate_control_capabilities(wrong_receipts)
    except ValueError as exc:
        assert "receipt capability" in str(exc)
    else:
        raise AssertionError("action-bound receipt capability drift was accepted")


def test_wire_schema_collection_limits_match_the_compatibility_registry():
    state_schema = json.loads(
        (ROOT / "lib" / "schemas" / "behavior-state-v1.schema.json").read_text())
    goal_schema = json.loads(
        (ROOT / "lib" / "schemas" / "goal-set-snapshot-v1.schema.json").read_text())
    assert (state_schema["properties"]["active_stateworks"]["maxItems"]
            == MAX_ACTIVE_STATEWORKS)
    assert goal_schema["properties"]["items"]["maxItems"] == MAX_GOAL_CRITERIA
    cfw_event_schema = json.loads(
        (CFW / "contracts" / "runtime-event.schema.json").read_text())
    assert COMPATIBILITY["cfw_runtime_event"] == cfw_event_schema[
        "properties"]["schema_version"]["const"]
    assert CONTROL_LIMITS["max_objective_criteria"] == MAX_GOAL_CRITERIA
    assert COMPATIBILITY["dp_control"]
