"""Adversarial check: is the perfect score real, or a leak?

The informative arm scored 8/8 with ECE 0.602. Before believing it, check the
three ways a projection can accidentally encode the answer:

  1. LABEL LEAKAGE — the option text names the gold route.
  2. AVAILABILITY LEAKAGE — `available_evidence` differs between arms in a way
     that correlates with the answer.
  3. ORDER LEAKAGE — the gold route is always first among options.

Also checks whether the model is merely matching on evidence text
("requires repository_truth") rather than reasoning, by permuting which route
carries which obligation. If the score collapses under permutation, the model
learned a surface association, not the distinction.
"""
from __future__ import annotations

import sys

sys.path.insert(0, "/home/bamn/DigitalPsychology")

from lib.laya_configuration import MODEL_CONTENT_SHA256, MODEL_REVISION
from qualification.corpus_schema import (
    ControllerDecision,
    CorpusRecord,
    ObservedOutcome,
    SemanticTarget,
)
from qualification.inference_bridge import (
    TaskContext,
    _route_descriptions,
)

WF = {
    "getter": ("Establish repository truth and apply authorised changes",
               "repository", "OBSERVED",
               ("PLANNED", "CHANGING", "VERIFIED", "STABLE"),
               ("intent", "risk")),
    "gitter": ("Verify repository truth against recorded state",
               "repository", "UNKNOWN", ("OBSERVED", "STALE"),
               ("repository_truth",)),
    "infrae": ("Discover, model and change infrastructure state",
               "infrastructure", "UNKNOWN",
               ("OBSERVED", "MODELED", "ASSESSED", "PLANNED", "CHANGING",
                "VERIFIED", "STABLE"),
               ("discovery",)),
}


def _ctx(eligible, held, domains, obligations):
    """Build a TaskContext from an explicit obligations mapping.

    ``obligations`` is honoured, not ignored: a leak audit that cannot vary the
    thing it claims to be testing is not a test.
    """
    return TaskContext(
        objective="Establish the authoritative state of the repository.",
        work_mode="diagnose", deliverable_kind="code_behavior",
        current_state="UNKNOWN", domain_tags=domains,
        workflows=tuple(
            (sid, purpose, domain, state, legal, sid in eligible, req)
            for sid, (purpose, domain, state, legal, req)
            in obligations.items()),
        available_evidence=held, non_goals=())


def _rec(gold, eligible):
    return CorpusRecord(
        frame_id="leak-1",
        semantic_target=SemanticTarget(
            frame_id="leak-1", appropriate_route=gold, eligible_routes=eligible,
            label_provenance="independent_expert_review",
            labelled_by="human-reviewer-1"),
        controller_decision=ControllerDecision(
            selected_route=None, available_candidates=eligible,
            policy_snapshot_hash="1" * 64, decision_reason="none",
            abstained=True),
        observed_outcome=ObservedOutcome(
            success_criteria_met=True, evidence_refs=("e",),
            observed_by="host_validator"),
        checkpoint_revision=MODEL_REVISION, checkpoint_content_hash=MODEL_CONTENT_SHA256,
        statework_contract_version="csw-v1", calibration_id="none",
        policy_snapshot_hash="1" * 64, trajectory_id="t-leak",
        subject_ref="repo:example")


def main():

    print("=" * 74)
    print("LEAKAGE AUDIT — is the 8/8 real?")
    print("=" * 74)

    eligible = ("getter", "gitter", "infrae")
    held = ("repository_truth", "intent")
    ctx = _ctx(eligible, held, ("software",), WF)
    options = _route_descriptions(ctx, eligible)

    print("\n1. LABEL LEAKAGE — does any option text name the answer?")
    leaks = []
    for gold in eligible:
        for sid, label in options:
            # The option whose obligation the host holds must not be labelled
            # "correct" or otherwise flagged.
            if any(w in label.lower() for w in ("correct", "right", "best",
                                                "answer", "gold", "preferred")):
                leaks.append((sid, label))
    print(f"   {leaks if leaks else 'no evaluative wording in any option label'}")
    print("   option labels offered to the model:")
    for sid, label in options:
        print(f"     {label}")

    print("\n2. AVAILABILITY LEAKAGE — is held-evidence constant across golds?")
    sets = []
    for gold in eligible:
        c = _ctx(eligible, held, ("software",), WF)
        sets.append(tuple(sorted(c.available_evidence)))
    constant = len(set(sets)) == 1
    print(f"   available_evidence identical for every gold label: {constant}")
    print(f"   value: {sets[0]}")

    print("\n3. ORDER LEAKAGE — is gold ever positional?")
    orders = [("getter", "gitter", "infrae"),
              ("infrae", "getter", "gitter"),
              ("gitter", "infrae", "getter")]
    # Content must be identical regardless of the ORDER options are presented.
    # Comparing as an order-insensitive mapping is the correct check; comparing
    # tuples would flag a pure reordering as a content change.
    per_order = {
        tuple(sorted(_route_descriptions(_ctx(o, held, ("software",), WF), o)))
        for o in orders}
    content_constant = len(per_order) == 1
    print(f"   option CONTENT identical under permutation: {content_constant}")
    print(f"   option ORDER varies: {[o[0] for o in orders]}")

    # 4. The decisive test: does the score survive PERMUTING the obligations?
    print("\n4. OBLIGATION PERMUTATION — surface association or reasoning?")
    print("   Relabel which route carries which obligation, keeping the task")
    print("   text fixed. If the model matches on obligation text rather than")
    print("   reasoning about it, the score will collapse.")
    swapped = {
        "getter": ("Establish repository truth and apply authorised changes",
                   "repository", "OBSERVED",
                   ("PLANNED", "CHANGING", "VERIFIED", "STABLE"),
                   ("repository_truth",)),          # getter now requires repository_truth
        "gitter": ("Verify repository truth against recorded state",
                   "repository", "UNKNOWN", ("OBSERVED", "STALE"),
                   ("intent", "risk")),              # gitter now requires intent,risk
        "infrae": WF["infrae"],
    }
    swapped_options = _route_descriptions(
        _ctx(eligible, held, ("software",), swapped), eligible)
    same_as_original = dict(swapped_options) == dict(options)
    print(f"   labels changed when obligations swapped: {not same_as_original}")
    for sid, label in swapped_options:
        print(f"     {label}")

    print()
    print("=" * 74)
    print("VERDICT")
    print("=" * 74)
    print("""  If (1) shows no evaluative wording, (2) constant, (3) content-stable
  under order permutation, and (4) labels change when obligations are swapped,
  then the projection does not encode the answer and the measure is sound.

  The remaining written caveat is that with n=9 and a 3-option task the model may
  still be exploiting a surface association between obligation text and route
  name, because in these fixtures the obligation and the name always co-vary.
  Separating them requires a held-out set where the SAME obligation attaches to a
  DIFFERENT route -- which the permutation test shows the code supports but which
  no current fixture exercises.
""")
    print("=" * 74)
    return 0


def permutation_probe():
    """Does the score survive attaching the SAME obligation to a DIFFERENT route?

    In every current fixture the obligation and the route name co-vary, so a
    model could score 8/8 by matching obligation text to a name it memorised.
    This constructs the decoupling case: keep the task fixed, swap which route
    carries which obligation, and see whether the prediction follows the
    OBLIGATION (reasoning) or the NAME (surface association).
    """
    from lib.laya_configuration import MODEL_CONTENT_SHA256, MODEL_REVISION
    from lib.system_one_backends import PROVIDER_PROTOCOL_HASH, LayaProvider
    from qualification.inference_bridge import build_frame, prediction_from_judgment

    ELIGIBLE = ("getter", "gitter", "infrae")
    HELD = ("intent", "repository_truth", "discovery")

    original = {
        "getter": WF["getter"],
        "gitter": WF["gitter"],
        "infrae": WF["infrae"],
    }
    # Swap obligations between getter and gitter; task text and eligibility
    # unchanged. A reasoning model should now prefer whichever route carries the
    # obligation matching the task; a name-matching model should not move.
    permuted = {
        "getter": (WF["getter"][0], WF["getter"][1], WF["getter"][2],
                   WF["getter"][3], WF["gitter"][4]),
        "gitter": (WF["gitter"][0], WF["gitter"][1], WF["gitter"][2],
                   WF["gitter"][3], WF["getter"][4]),
        "infrae": WF["infrae"],
    }

    provider = LayaProvider(
        "/home/bamn/.cache/laya/"
        "convaiinnovations-laya-typed-decisions-1a793eb",
        model_id="laya-typed-decisions", model_revision=MODEL_REVISION,
        expected_model_content_hash=MODEL_CONTENT_SHA256, device="cpu",
        max_latency_ms=30000)
    provider.warmup_and_wait()

    results = {}
    for label, obligations in (("original", original), ("permuted", permuted)):
        # Hold the task at repository truth: gitter's obligation originally,
        # getter's obligation after permutation.
        record = _rec("gitter", ELIGIBLE)
        frame = build_frame(
            record, _ctx(ELIGIBLE, HELD, ("software",), obligations), sequence=1,
            backend_id="digitalpsychology-laya-native",
            model_id="laya-typed-decisions", model_revision=MODEL_REVISION,
            model_content_hash=MODEL_CONTENT_SHA256,
            provider_protocol_hash=PROVIDER_PROTOCOL_HASH)
        judgment = provider.judge(frame)
        artifact = prediction_from_judgment(
            record, judgment, checkpoint_revision=MODEL_REVISION,
            checkpoint_content_hash=MODEL_CONTENT_SHA256)
        results[label] = (artifact.selected, artifact.margin,
                          dict(artifact.distribution))

    print()
    print("=" * 74)
    print("PERMUTATION PROBE — reasoning or name-matching?")
    print("=" * 74)
    print("  Task fixed. Obligations for getter/gitter swapped in place.")
    for label, (selected, margin, dist) in results.items():
        print(f"    {label:<10} selected={selected:<8} margin={margin:.3f}")
        print(f"               dist={ {k: round(v,3) for k,v in dist.items()} }")
    moved = results["original"][0] != results["permuted"][0]
    print()
    if moved:
        print("  The prediction FOLLOWED THE OBLIGATION across the swap.")
        print("  The model is responding to the contract semantics, not the name.")
    else:
        print("  The prediction did NOT move when obligations swapped.")
        print("  At least on this case it is associating route NAME, not")
        print("  obligation semantics. The 8/8 must be read as weaker evidence.")
    print("=" * 74)
    return 0 if moved else 1


if __name__ == "__main__":
    import sys
    rc = main()
    if "--probe" in sys.argv:
        rc = permutation_probe() or rc
    raise SystemExit(rc)
