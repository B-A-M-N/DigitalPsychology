"""Codex transport projections preserve shared lifecycle semantics."""
from __future__ import annotations

import asyncio

from cognitive_runtime_core.harness import (
    CallbackCognitiveHarnessLifecycle,
    CognitiveRuntimeHarnessSession,
    CompletionProposal,
    HARNESS_ADAPTER_PROTOCOL_VERSION,
    HarnessAdapterIdentity,
    HarnessBoundary,
    HarnessCapabilities,
    TaskContract,
)
from cognitive_runtime_core.integrations.codex.adapter import CodexHarnessAdapter


def test_codex_harness_adapter_emits_neutral_lifecycle_without_raw_arguments():
    async def run():
        events = []

        async def observe(event):
            events.append(event)
            return {"status": "ready"}

        capabilities = HarnessCapabilities(
            model_boundary=True, pre_action=True, post_action=True,
            action_blocking=True, validator_boundary=True,
            completion_interception=True)
        identity = HarnessAdapterIdentity(
            harness_id="codex-app-server", adapter_version="0.1.0",
            host_version="test-host", protocol_version=HARNESS_ADAPTER_PROTOCOL_VERSION,
            capabilities=capabilities,
            catalog_pin={"codex_feature_catalog": "sha256:fixture"}, verified=True)
        session = CognitiveRuntimeHarnessSession(
            identity, CallbackCognitiveHarnessLifecycle(observe))
        adapter = CodexHarnessAdapter(session)
        await adapter.start_task(TaskContract(
            task_id="task-1", objective="Implement feature",
            criterion_claims={"c1": "Feature works"}))
        await adapter.model_boundary(transport_event_id="turn-start-1",
                                     context="base context", tool_capable=True)
        proposed = await adapter.action_proposed(
            invocation_id="call-1", tool_name="write", operation="write_file",
            purpose="advances_primary_goal", arguments={"path": "main.py",
                                                            "content": "secret body"},
            affected_criterion_ids=("c1",), action_class="write")
        action_event = events[-1]
        await adapter.action_completed(
            invocation_id="call-1", action_index=1,
            criterion_status={"c1": "verified"}, progress_made=True,
            evidence_refs=("evidence-c1",),
            criterion_evidence_refs={"c1": ("evidence-c1",)})
        await adapter.validation_completed(
            validator_id="v1", criterion_ids=("c1",),
            evidence_refs=("evidence-c1",))
        await adapter.completion_proposed(CompletionProposal(
            proposal_id="completion-1", claim="Feature works",
            required_criterion_ids=("c1",), expected_validator_ids=("v1",),
            validator_outcomes=({
                "validator_id": "v1", "criterion_ids": ("c1",),
                "passed": True, "evidence_ref": "evidence-c1",
                "result_digest": "a" * 64, "invocation_id": "invoke-v1",
            },), criterion_evidence_refs={"c1": ("evidence-c1",)}))
        await adapter.task_end()
        return events, proposed, action_event

    events, proposed, action_event = asyncio.run(run())
    assert [event.boundary for event in events] == [
        HarnessBoundary.TASK_START,
        HarnessBoundary.MODEL_BOUNDARY,
        HarnessBoundary.ACTION_PROPOSED,
        HarnessBoundary.ACTION_COMPLETED,
        HarnessBoundary.VALIDATION_COMPLETED,
        HarnessBoundary.COMPLETION_PROPOSED,
        HarnessBoundary.TASK_END,
    ]
    assert proposed["status"] == "ready"
    assert events[1].control_data.context == "base context"
    assert "secret body" not in str(action_event.to_dict())
    assert action_event.control_data.parameter_digest
    assert "thread/start" not in str(action_event.to_dict())


def test_codex_identity_pins_feature_catalog_and_degrades_unverified_clients():
    class Client:
        host_policy_verified = True
        codex_feature_catalog = {"dynamic_tools": "v1", "shell_tool": "disabled"}

    verified = CodexHarnessAdapter.identity_for_client(
        Client(), host_version="codex-1.2.3")
    assert verified.verified is True
    assert verified.effective_capabilities.action_blocking is True
    assert verified.catalog_pin["codex_feature_catalog_sha256"]

    class UnverifiedClient:
        host_policy_verified = False
        codex_feature_catalog = {"dynamic_tools": "v1"}

    unverified = CodexHarnessAdapter.identity_for_client(
        UnverifiedClient(), host_version="codex-1.2.3")
    assert unverified.verified is False
    assert unverified.effective_capabilities.action_blocking is False
    assert "harness_identity_unverified" in unverified.degradation_claims
