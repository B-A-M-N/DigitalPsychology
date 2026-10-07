from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from cognitive_runtime_core.completion import (
    CompletionAuthorityReceipt,
    ValidatorCompletionEvidence,
    aggregate_completion,
    aggregate_completion_decision,
)
from cognitive_runtime_core.harness import (
    CompletionProposal,
    HarnessBoundary,
    HarnessBoundaryEvent,
)
from cognitive_runtime_core.integrations.cfw import DigitalPsychologyHarnessLifecycle


def _passing_validation():
    return aggregate_completion(
        ("criterion",), ("validator",),
        (ValidatorCompletionEvidence(
            validator_id="validator",
            criterion_ids=("criterion",),
            passed=True,
            evidence_ref="evidence://validator-result",
            result_digest="a" * 64,
            invocation_id="invocation-1",
        ),),
    )


def test_completion_decision_requires_each_host_selected_authority():
    validation = _passing_validation()
    receipts = (
        CompletionAuthorityReceipt(
            "component-authority", True, revision="component-r4",
            evidence_refs=("evidence://component-gate",)),
        CompletionAuthorityReceipt(
            "workflow-authority", True, revision="workflow-r8",
            evidence_refs=("evidence://workflow-state",)),
    )

    decision = aggregate_completion_decision(
        ("component-authority", "workflow-authority"), receipts,
        validation=validation)

    assert decision.allowed is True
    assert decision.reason_codes == ()
    assert decision.evidence_refs == (
        "evidence://component-gate",
        "evidence://workflow-state",
        "evidence://validator-result",
    )
    assert [item.authority_id for item in decision.authority_receipts] == [
        "component-authority", "workflow-authority"]


def test_completion_decision_fails_closed_on_missing_and_denied_authorities():
    decision = aggregate_completion_decision(
        ("component-authority", "workflow-authority"),
        (CompletionAuthorityReceipt(
            "component-authority", False, revision="component-r4",
            reason_codes=("component.evidence_stale",)),),
        validation=_passing_validation(),
    )

    assert decision.allowed is False
    assert decision.reason_codes == (
        "authority_receipt_missing:workflow-authority",
        "component.evidence_stale",
    )


def test_completion_decision_fails_closed_when_validator_evidence_is_incomplete():
    validation = aggregate_completion(("criterion",), ("validator",), ())
    receipt = CompletionAuthorityReceipt("host-authority", True)

    decision = aggregate_completion_decision(
        ("host-authority",), (receipt,), validation=validation)

    assert decision.allowed is False
    assert "validator_evidence:validator_outcome_missing" in decision.reason_codes
    assert "validator_missing:validator" in decision.reason_codes
    assert "criterion_evidence_missing:criterion" in decision.reason_codes


def test_completion_decision_rejects_duplicate_authority_receipts():
    receipt = CompletionAuthorityReceipt("host-authority", True)

    with pytest.raises(ValueError, match="contains duplicates"):
        aggregate_completion_decision(
            ("host-authority",), (receipt, receipt),
            validation=_passing_validation())


def test_completion_decision_with_no_required_authorities_is_denied():
    decision = aggregate_completion_decision(
        (), (), validation=_passing_validation())

    assert decision.allowed is False
    assert decision.reason_codes == ("authority_set_empty",)


def test_dp_lifecycle_requires_selected_adapter_before_starting_the_bridge():
    bridge_factory_calls = []
    lifecycle = DigitalPsychologyHarnessLifecycle(
        bridge_factory=lambda **_kwargs: bridge_factory_calls.append("started"),
        candidate_factory=lambda **kwargs: kwargs,
        observation_factory=lambda **kwargs: kwargs)

    async def run():
        with pytest.raises(RuntimeError, match="select a component adapter"):
            await lifecycle.open_task(
                request=SimpleNamespace(task_id="task"), runtime=None,
                task_handle=SimpleNamespace(agent_instance_id="agent"),
                host_handle=None, session_id="session", session_nonce="nonce",
                resume=False, runtime_mode="combined_enforcing")

    asyncio.run(run())
    assert bridge_factory_calls == []


def test_dp_lifecycle_projects_a_typed_completion_authority_receipt():
    class _Bridge:
        behavior_state = type("BehaviorState", (), {"revision": 7})()

        async def observe_boundary(self, **_kwargs):
            return {"status": "ready", "decision": "allow"}

    async def run():
        lifecycle = DigitalPsychologyHarnessLifecycle(
            _Bridge(), require_shared_csw=False,
            candidate_factory=lambda **kwargs: kwargs,
            observation_factory=lambda **_kwargs: {})
        lifecycle._opened = True
        proposal = CompletionProposal(
            proposal_id="completion-1", claim="finish the task",
            required_criterion_ids=("criterion",),
            expected_validator_ids=("validator",),
            validator_outcomes=(ValidatorCompletionEvidence(
                validator_id="validator", criterion_ids=("criterion",),
                passed=True, evidence_ref="evidence://validator",
                result_digest="a" * 64, invocation_id="invocation-1"),),
            criterion_evidence_refs={"criterion": ("evidence://validator",)})
        result = await lifecycle.observe_boundary(HarnessBoundaryEvent(
            boundary=HarnessBoundary.COMPLETION_PROPOSED,
            event_id="completion-event", sequence=1, control_data=proposal))
        return result

    result = asyncio.run(run())
    assert result["authority_receipts"] == [{
        "authority_id": "digital-psychology", "allowed": True,
        "revision": "7", "evidence_refs": [], "reason_codes": [],
    }]
