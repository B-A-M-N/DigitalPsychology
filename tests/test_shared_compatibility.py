import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
CFW = ROOT.parent / "CognitiveFrameWorks"
sys.path.insert(0, str(CFW / "scripts"))
from lib.routing_profiles import BehavioralRoutingProfile
from runtime_contract.routing import applicable_adjustments as cfw_applicable
import lib.routing_profiles as rp

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
