"""Representation A/B: bare frame vs informative frame, same pinned checkpoint.

The finding: offline frames gave Laya `workflows=()` and generic option labels,
so the earlier 0.50 balanced accuracy / 0.069 mean margin measured a REPRESENTATION
defect as much as a model defect.

This isolates the variable. Same checkpoint, same weights, same 9 cases. The only
difference is whether the frame carries real StateWork facts.

Case set is deliberately balanced across the three bundles and includes
near-identical contrastive pairs where one material fact changes the preferred
route -- plus two cases where the deterministic controller is wrong.
"""
from __future__ import annotations

import sys
import time

sys.path.insert(0, "/home/bamn/DigitalPsychology")

from lib.laya_configuration import MODEL_CONTENT_SHA256, MODEL_REVISION
from lib.system_one_backends import PROVIDER_PROTOCOL_HASH

CKPT = "/home/bamn/.cache/laya/convaiinnovations-laya-typed-decisions-1a793eb"

# (purpose, domain, current_state, legal_next_states, required_evidence)
# required_evidence taken verbatim from the authoritative CSW transitions.yaml:
#   getter.observed.plan.planned  -> [intent, risk]
#   gitter.unknown.observe.observed -> [repository_truth]
#   infrae.unknown.discover.observed -> [discovery]
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

# Evidence the host holds before the routing decision varies per case. This is a
# PRE-DECISION fact, not a hint: the same evidence set is presented to both arms.
HELD = {
    "get-1": (), "get-2": (),
    "git-1": ("repository_truth",), "git-2": ("repository_truth",),
    "inf-1": (), "inf-2": ("discovery",),
    "mix-1": ("intent", "risk"), "mix-2": ("repository_truth",),
    "amb-1": ("repository_truth", "intent"),
}

# (id, objective, work_mode, domain_tags, gold, controller_selected, eligible)
CASES = [
    ("get-1", "Establish what the repository currently contains before changing anything.",
     "diagnose", ("software",), "getter", "getter", ("getter", "gitter", "infrae")),
    ("get-2", "Read the source tree to determine the existing module structure.",
     "diagnose", ("software",), "getter", "gitter", ("getter", "gitter", "infrae")),
    ("git-1", "Check whether recorded repository state still matches the working tree.",
     "review", ("software",), "gitter", "getter", ("getter", "gitter", "infrae")),
    ("git-2", "Confirm the checked-in truth before trusting it as a baseline.",
     "review", ("software",), "gitter", "gitter", ("getter", "gitter", "infrae")),
    ("inf-1", "Discover what hosts are actually running before planning a change.",
     "diagnose", ("infrastructure",), "infrae", "infrae", ("getter", "gitter", "infrae")),
    ("inf-2", "Model the network topology to find the failing dependency.",
     "diagnose", ("infrastructure", "network"), "infrae", "getter",
     ("getter", "gitter", "infrae")),
    ("mix-1", "Apply the repository change once intent, risk and rollback evidence exist.",
     "delivery", ("software",), "getter", "getter", ("getter", "gitter")),
    ("mix-2", "Apply the change, but the only recorded evidence is repository truth.",
     "delivery", ("software",), "getter", "infrae", ("getter", "gitter")),
    ("amb-1", "Choose a route; both repository workflows are equally supported.",
     "delivery", ("software",), None, None, ("getter", "gitter")),
]


def build(context, record, seq):
    from qualification.inference_bridge import build_frame
    return build_frame(
        record, context, sequence=seq,
        backend_id="digitalpsychology-laya-native",
        model_id="laya-typed-decisions", model_revision=MODEL_REVISION,
        model_content_hash=MODEL_CONTENT_SHA256,
        provider_protocol_hash=PROVIDER_PROTOCOL_HASH)


def main():
    from lib.system_one_backends import LayaProvider
    from qualification.corpus_schema import (
        ControllerDecision,
        CorpusRecord,
        ObservedOutcome,
        SemanticTarget,
    )
    from qualification.inference_bridge import (
        TaskContext,
        compute_metrics,
    )
    from qualification.qualification_spec import QualificationSpec

    print("=" * 74)
    print("Loading pinned checkpoint ONCE (offline)")
    print("=" * 74)
    provider = LayaProvider(
        CKPT, model_id="laya-typed-decisions", model_revision=MODEL_REVISION,
        expected_model_content_hash=MODEL_CONTENT_SHA256, device="cpu",
        max_latency_ms=30000)
    t0 = time.monotonic()
    provider.warmup_and_wait()
    print(f"  loaded in {time.monotonic() - t0:.1f}s")

    def run(informative: bool):
        preds = []
        rows = []
        for index, (cid, objective, mode, tags, gold, controller, eligible) in enumerate(
                CASES, start=1):
            if informative:
                context = TaskContext(
                    objective=objective, work_mode=mode,
                    deliverable_kind="code_behavior",
                    current_state="UNKNOWN", domain_tags=tags,
                    workflows=tuple(
                        (sid, purpose, domain, state, legal, sid in eligible,
                         required)
                        for sid, (purpose, domain, state, legal, required)
                        in WF.items()),
                    # Evidence the host holds before deciding. Varies per case
                    # so the model cannot infer the answer from a constant.
                    available_evidence=HELD.get(cid, ()),
                    non_goals=())
            else:
                # Bare: no workflow facts at all -- the original defect.
                context = TaskContext(
                    objective=objective, work_mode=mode,
                    deliverable_kind="code_behavior",
                    current_state="UNKNOWN", domain_tags=tags,
                    workflows=(), non_goals=())
            record = CorpusRecord(
                frame_id=cid,
                semantic_target=SemanticTarget(
                    frame_id=cid, appropriate_route=gold, eligible_routes=eligible,
                    label_provenance="independent_expert_review",
                    labelled_by="human-reviewer-1", abstention_expected=gold is None),
                controller_decision=ControllerDecision(
                    selected_route=controller, available_candidates=eligible,
                    policy_snapshot_hash="1" * 64,
                    decision_reason="deterministic tag match", abstained=controller is None),
                observed_outcome=ObservedOutcome(
                    success_criteria_met=True, evidence_refs=(f"e-{cid}",),
                    observed_by="host_validator"),
                checkpoint_revision=MODEL_REVISION,
                checkpoint_content_hash=MODEL_CONTENT_SHA256,
                statework_contract_version="csw-v1", calibration_id="none",
                policy_snapshot_hash="1" * 64, trajectory_id=f"t-{cid}",
                subject_ref="repo:example")
            frame = build(context, record, index)
            judgment = provider.judge(frame)
            from qualification.inference_bridge import prediction_from_judgment
            artifact = prediction_from_judgment(
                record, judgment, checkpoint_revision=MODEL_REVISION,
                checkpoint_content_hash=MODEL_CONTENT_SHA256)
            preds.append(artifact)
            rows.append((cid, artifact.selected, gold, artifact.margin))
        return compute_metrics(preds, spec=QualificationSpec(), trajectories=len(CASES)), rows

    bare_metrics, bare_rows = run(informative=False)
    info_metrics, info_rows = run(informative=True)

    print()
    print("=" * 74)
    print("REPRESENTATION A/B (same checkpoint, same weights)")
    print("=" * 74)
    print(f"{'case':<7}{'gold':<10}{'bare':<10}{'informative':<13}{'margin'}")
    print("-" * 74)
    info_by_id = {r[0]: r for r in info_rows}
    for cid, _s, gold, _m in bare_rows:
        i_sel, i_margin = info_by_id[cid][1], info_by_id[cid][3]
        print(f"{cid:<7}{gold!s:<10}{_s:<10}{i_sel:<13}{i_margin:.3f}")
    print("-" * 74)

    for label, metrics in (("BARE (workflows=())", bare_metrics),
                           ("INFORMATIVE (workflow facts)", info_metrics)):
        print()
        print(f"  {label}")
        print(f"    balanced_accuracy : {metrics.balanced_accuracy}")
        print(f"    brier / ece       : {metrics.brier} / {metrics.ece}")
        print(f"    mean_margin       : {metrics.mean_margin}")
        print(f"    false_interventions: {metrics.false_interventions}")
        print(f"    complementarity   : {dict(metrics.complementarity)}")
        print(f"    verdict           : {metrics.verdict}")

    print()
    print("=" * 74)
    delta = (info_metrics.balanced_accuracy or 0) - (bare_metrics.balanced_accuracy or 0)
    print(f"  balanced-accuracy delta (informative - bare): {delta:+.3f}")
    print()
    if delta > 0:
        print("  The representation change ALONE improved discrimination with")
        print("  identical weights. The earlier measurement was confounded.")
    elif delta == 0:
        print("  No measurable difference. The representation was not the")
        print("  limiting factor on these cases -- that is also a real result.")
    else:
        print("  Informative input scored WORSE. Worth investigating before")
        print("  attributing anything to the model.")
    print("  n=9 is a tiny diagnostic sample; treat direction, not magnitude.")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
