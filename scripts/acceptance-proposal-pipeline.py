#!/usr/bin/env python3
"""Proposal-pipeline acceptance: DP judgment -> CSW inbox -> host attestation -> transition.

Closes the reported gap that drain_judgment() and ProposalInbox existed
only in tests with no production call path between them. This script
exercises the REAL production functions against a REAL CSW inbox and a
REAL WorkflowAuthority in one process:

1. A real SystemOneJudgment (validated contract type) carrying a decisive
   test_strategy_preference answer.
2. DP production drain_judgment() submitting into a real ProposalInbox.
3. Review snapshot surfacing the proposal.
4. Host attestation citing the proposal as input (host issuer only).
5. The attested evidence satisfying a real CSW transition guard.
6. Negative: the bare proposal id satisfies nothing (TransitionError).

A proposal alone never advances state; only host attestation does.
"""

from __future__ import annotations

import datetime
import hashlib
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSW_ROOT = ROOT.parent / "CognitiveStateWork"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(CSW_ROOT / "scripts"))

from lib.control.proposal_watcher import (  # noqa: E402
    drain_judgment, review_snapshot)
from lib.system_one_contracts import (  # noqa: E402
    QuestionAnswer,
    QuestionProbability,
    SystemOneIdentity,
    SystemOneJudgment,
)
from proposal_inbox import ProposalInbox, build_host_attestation  # noqa: E402
from evidence import EvidenceStore  # noqa: E402
from selection_store import WorkflowSelectionStore  # noqa: E402
from state_store import SubjectStateStore  # noqa: E402
from workflow_authority import WorkflowAuthority  # noqa: E402
from workflow_engines import WorkflowIdentity  # noqa: E402
from errors import TransitionError  # noqa: E402

DIGEST = "d" * 64


def _identity() -> SystemOneIdentity:
    return SystemOneIdentity(
        namespace_id="acceptance", application_id="assistant",
        application_instance_id="assistant-1", agent_id="agent-1",
        agent_instance_id="agent-1a", session_id="session-1",
        task_id="task-1", attempt_id="attempt-1", interaction_id="ix-1")


def _judgment() -> SystemOneJudgment:
    answers = (
        QuestionAnswer(
            question_id="test_strategy_preference", selected="testworks",
            raw_probabilities=(
                QuestionProbability("testworks", 0.8),
                QuestionProbability("godkiller", 0.2)),
            calibrated_probabilities=(
                QuestionProbability("testworks", 0.85),
                QuestionProbability("godkiller", 0.15)),
            calibration_hash="c" * 64),
    )
    return SystemOneJudgment(
        identity=_identity(), request_id="acceptance-req-1", sequence=1,
        state_digest=DIGEST, question_bundle_digest=DIGEST,
        request_digest=DIGEST, policy_hash="e" * 64,
        backend_id="digitalpsychology-jev-openrouter",
        model_id="typesafe/jev-1.14", model_revision="jev-1.14",
        model_content_hash="a" * 64, provider_protocol_hash="b" * 64,
        calibration_bundle_hash="", answers=answers)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="acceptance-proposal-"))
    inbox = ProposalInbox(tmp / "inbox.db")

    # 1-2. Real judgment through the production drain into the real inbox.
    judgment = _judgment()
    receipts = drain_judgment(judgment, submit=inbox.submit,
                              subject_ref="acceptance:subject")
    assert len(receipts) == 1 and receipts[0]["ok"], receipts
    pid = receipts[0]["proposal_id"]
    print(f"STEP 1-2: drain submitted {receipts[0]['kind']} as {pid}: ok")

    # 3. Review surfaces it.
    rows = review_snapshot(inbox.list_open())
    assert len(rows) == 1 and rows[0]["proposal_id"] == pid
    assert rows[0]["selected"] == "testworks"
    print("STEP 3: review snapshot surfaces the proposal: ok")

    # 4-5. Host attests its own evidence citing the proposal; CSW advances.
    evidence = EvidenceStore()
    authority = WorkflowAuthority(
        root=CSW_ROOT, state_store=SubjectStateStore(tmp / "subjects.json"),
        evidence_store=evidence,
        selection_store=WorkflowSelectionStore(tmp / "selections.json"))
    contract = authority.workflow_contract_identity("testworks")
    identity = WorkflowIdentity(
        namespace_id="acceptance", application_id="assistant",
        application_instance_id="assistant-1",
        workflow_id="testworks-acceptance-proposal",
        subject_ref="acceptance:subject", statework_id="testworks",
        contract_version=contract["contract_version"])
    snapshot = authority.inspect_workflow(
        identity, contract_hash=contract["contract_hash"])
    spec = authority._compiled_registry["evidence_registry"]["kinds"][
        "testworks.inventory"]
    record = build_host_attestation(
        inbox.get(pid), ref="acceptance:proposal-link:1",
        kind="testworks.inventory", subject_ref=identity.subject_ref,
        observed_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        issuer=spec["allowed_issuers"][0], validator=spec["validator"],
        schema=spec["schema"],
        host_observation={"checked": "smoke plus contract on current revision"})
    ref = evidence.create(record)
    decision = authority.apply_step(
        identity, contract_hash=contract["contract_hash"],
        step_id="testworks.unassessed.inventory.inventoried",
        evidence_refs=[ref], expected_revision=snapshot.revision)
    assert decision.allowed is True, decision.reason
    print("STEP 4-5: host attestation citing proposal advances transition: ok")

    # 6. Negative: the bare proposal id satisfies nothing.
    snapshot = authority.inspect_workflow(
        identity, contract_hash=contract["contract_hash"])
    try:
        authority.apply_step(
            identity, contract_hash=contract["contract_hash"],
            step_id="testworks.inventoried.strategize.strategized",
            evidence_refs=[pid], expected_revision=snapshot.revision)
    except TransitionError:
        print("STEP 6: bare proposal id rejected by evidence store: ok")
    else:
        print("STEP 6 FAILED: proposal id accepted as evidence")
        return 1
    assert authority.inspect_workflow(
        identity, contract_hash=contract["contract_hash"]).revision == snapshot.revision

    print("\nPROPOSAL-PIPELINE ACCEPTANCE PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
