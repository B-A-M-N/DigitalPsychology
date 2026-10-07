from __future__ import annotations

import asyncio
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.runtime.harness import (ActionProposal, CompletionProposal,
                                 CallbackCognitiveHarnessLifecycle,
                                 CognitiveHarnessLifecycle,
                                 CognitiveRuntimeHarnessSession,
                                 HarnessAdapterIdentity, HarnessBoundary,
                                 HarnessBoundaryEvent, HarnessCapabilities,
                                 RuntimeObservationProjection, TaskContract,
                                 dispatch_harness_events)


def _sample_capabilities() -> HarnessCapabilities:
    return HarnessCapabilities(
        model_boundary=True,
        planning_boundary=False,
        pre_action=True,
        post_action=True,
        action_blocking=True,
        context_projection=True,
        validator_boundary=True,
        completion_interception=True,
        multi_agent_identity=True,
    )


def test_capabilities_reject_non_boolean_and_inconsistent_boundaries():
    with pytest.raises(ValueError, match="must be boolean"):
        HarnessCapabilities(model_boundary=1)

    with pytest.raises(ValueError, match="requires a pre-action"):
        HarnessCapabilities(action_blocking=True)


def test_capability_hash_and_serialization_are_stable():
    first = _sample_capabilities()
    second = HarnessCapabilities.from_mapping({
        key: value for key, value in reversed(tuple(first.to_dict().items()))
    })

    assert first.to_json() == second.to_json()
    assert first.capability_hash == second.capability_hash
    assert len(first.capability_hash) == 64


def test_identity_pins_catalog_and_derives_boundary_degradation_claims():
    identity = HarnessAdapterIdentity(
        harness_id="sample-harness",
        adapter_version="2.3.1",
        host_version="codex-cli 0.160.0",
        protocol_version="1.1.0",
        capabilities=_sample_capabilities(),
        catalog_pin={
            "feature_catalog_sha256": "a" * 64,
            "feature_catalog_version": "2026-06-reviewed",
        },
        verified=True,
    )

    serialized = identity.to_dict()
    restored = HarnessAdapterIdentity.from_mapping(serialized)
    assert restored.identity_hash == identity.identity_hash
    assert restored.catalog_pin["feature_catalog_sha256"] == "a" * 64
    assert "planning_boundary_unavailable" in identity.degradation_claims
    assert "internal_inference_visibility_unavailable" in identity.degradation_claims
    assert identity.effective_capabilities.pre_action is True

    serialized["capability_hash"] = "0" * 64
    with pytest.raises(ValueError, match="capability hash"):
        HarnessAdapterIdentity.from_mapping(serialized)


def test_unverified_identity_suppresses_positive_capability_claims():
    identity = HarnessAdapterIdentity(
        harness_id="athena",
        adapter_version="1.0.0",
        host_version="2026.10",
        protocol_version="1.1.0",
        capabilities=_sample_capabilities(),
    )

    assert identity.verified is False
    assert identity.effective_capabilities.to_dict() == HarnessCapabilities().to_dict()
    assert "harness_identity_unverified" in identity.degradation_claims
    assert "pre_action_unavailable" in identity.degradation_claims


def test_unidentified_identity_is_explicit_and_fails_closed():
    identity = HarnessAdapterIdentity.unidentified()

    assert identity.harness_id == "unidentified"
    assert identity.adapter_version == "unknown"
    assert identity.host_version == "unknown"
    assert identity.protocol_version == "unknown"
    assert identity.capabilities.to_dict() == HarnessCapabilities().to_dict()
    assert identity.verified is False


def test_identity_rejects_unbounded_catalog_pins_and_version_text():
    with pytest.raises(ValueError, match="catalog pin"):
        HarnessAdapterIdentity(
            harness_id="sample-harness", adapter_version="1.0.0", host_version="1.0.0",
            protocol_version="1.1.0", capabilities=_sample_capabilities(),
            catalog_pin={"bad key": "value"},
        )

    with pytest.raises(ValueError, match="host_version"):
        HarnessAdapterIdentity(
            harness_id="sample-harness", adapter_version="1.0.0", host_version=" host version ",
            protocol_version="1.1.0", capabilities=_sample_capabilities(),
        )

    with pytest.raises(ValueError, match="protocol version is incompatible"):
        HarnessAdapterIdentity(
            harness_id="sample-harness", adapter_version="1.0.0", host_version="1.0.0",
            protocol_version="2.0.0", capabilities=_sample_capabilities(),
        )


def test_raw_adapter_declaration_is_canonicalized_and_hashed_by_dp():
    raw = {
        "harness_id": "athena",
        "adapter_version": "1.2.0",
        "host_version": "2026.10",
        "protocol_version": "1.1.0",
        "capabilities": _sample_capabilities().to_dict(),
        "catalog_pin": {"runtime_contract_version": "3.4"},
        "verified": True,
    }

    identity = HarnessAdapterIdentity.from_mapping(raw)
    canonical = identity.to_dict()
    assert canonical["capability_hash"] == identity.capabilities.capability_hash
    assert canonical["effective_capabilities"] == raw["capabilities"]
    assert canonical["effective_capability_hash"] == identity.capability_hash
    assert canonical["catalog_pin"] == raw["catalog_pin"]


def test_harness_boundaries_use_canonical_labels_and_bounded_evidence_refs():
    event = HarnessBoundaryEvent(
        boundary=HarnessBoundary.ACTION_COMPLETED,
        event_id="evt-12",
        sequence=12,
        action_id="action-12",
        criterion_ids=["tests_pass"],
        evidence_refs=["evidence://sha256/" + "a" * 64],
    )

    assert event.to_dict() == {
        "boundary": "action_completed",
        "event_id": "evt-12",
        "sequence": 12,
        "action_id": "action-12",
        "criterion_ids": ["tests_pass"],
        "evidence_refs": ["evidence://sha256/" + "a" * 64],
    }
    with pytest.raises(ValueError, match="canonical enum"):
        HarnessBoundaryEvent("item/tool/call", "evt-13", 13)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="unique bounded text"):
        HarnessBoundaryEvent(
            HarnessBoundary.VALIDATION_COMPLETED, "evt-14", 14,
            validator_id="validator-14", validation_passed=True,
            criterion_ids=["tests_pass", "tests_pass"],
        )
    with pytest.raises(ValueError, match="non-negative integer"):
        HarnessBoundaryEvent(HarnessBoundary.TASK_START, "evt-15", True)


def test_generic_runtime_session_dispatches_canonical_events_to_dp_lifecycle():
    observed = []
    identity = HarnessAdapterIdentity(
        harness_id="generic-host", adapter_version="1.0.0", host_version="1.0.0",
        protocol_version="1.1.0",
        capabilities=HarnessCapabilities(model_boundary=True), verified=True)
    lifecycle = CallbackCognitiveHarnessLifecycle(
        lambda event: observed.append(event) or {
            "status": "observed", "boundary": event.boundary.value})
    session = CognitiveRuntimeHarnessSession(identity, lifecycle)

    async def dispatch():
        first = await session.observe_boundary(
            HarnessBoundaryEvent(HarnessBoundary.TASK_START, "start-1", 1))
        second = await session.dispatch(
            HarnessBoundaryEvent(HarnessBoundary.MODEL_BOUNDARY, "model-2", 2))
        return first, second

    first, second = asyncio.run(dispatch())

    assert first["status"] == "observed"
    assert second["boundary"] == "model_boundary"
    assert [event.sequence for event in observed] == [1, 2]


def test_generic_stream_dispatch_is_bounded_and_structural():
    observed = []
    identity = HarnessAdapterIdentity.unidentified()
    session = CognitiveRuntimeHarnessSession(
        identity,
        CallbackCognitiveHarnessLifecycle(
            lambda event: observed.append(event) or {"status": "ok"}))

    async def events():
        yield HarnessBoundaryEvent(HarnessBoundary.TASK_START, "start-1", 1)
        yield HarnessBoundaryEvent(HarnessBoundary.TASK_END, "end-2", 2)

    summary = asyncio.run(dispatch_harness_events(session, events()))

    assert summary.events_dispatched == 2
    assert summary.first_sequence == 1
    assert summary.last_sequence == 2
    assert summary.degraded_events == 0
    assert len(observed) == 2


def test_canonical_lifecycle_methods_accept_structural_control_data():
    observed = []
    identity = HarnessAdapterIdentity(
        harness_id="control-host", adapter_version="1.0.0", host_version="1.0.0",
        protocol_version="1.1.0",
        capabilities=HarnessCapabilities(
            pre_action=True, post_action=True, action_blocking=True,
            completion_interception=True),
        verified=True)
    lifecycle = CallbackCognitiveHarnessLifecycle(
        lambda event: observed.append(event) or {
            "status": "observed", "boundary": event.boundary.value})
    session = CognitiveRuntimeHarnessSession(identity, lifecycle)

    async def send_control_data():
        await session.start_task({
            "task_id": "task-1",
            "objective": "Complete the requested change",
            "criterion_claims": {"tests": "Relevant checks pass", "docs": "Docs updated"},
            "required_criterion_ids": ["tests"],
            "non_goals": ["No unrelated refactor"],
        }, event_id="start-1", sequence=1)
        await session.propose_action({
            "action_id": "action-1", "action_class": "implementation",
            "tool_name": "write_file", "operation": "update", "purpose": "Implement the change",
            "affected_criterion_ids": ["tests", "docs"],
            "parameter_digest": "a" * 64,
            "mutating": True, "reversible": True, "external": False,
        }, event_id="proposal-2", sequence=2)
        await session.observe_action({
            "action_id": "action-1", "action_index": 1,
            "criterion_status": {"tests": "verified", "docs": "in_progress"},
            "action_completed": True, "progress_made": True,
            "evidence_refs": ["evidence://tests/1"],
            "criterion_evidence_refs": {"tests": ["evidence://tests/1"]},
        }, event_id="observation-3", sequence=3)
        await session.propose_completion({
            "proposal_id": "complete-4", "claim": "The requested change is complete",
            "required_criterion_ids": ["tests"],
            "criterion_evidence_refs": {"tests": ["evidence://tests/1"]},
        }, event_id="completion-4", sequence=4)

    asyncio.run(send_control_data())

    assert [event.boundary for event in observed] == [
        HarnessBoundary.TASK_START,
        HarnessBoundary.ACTION_PROPOSED,
        HarnessBoundary.ACTION_COMPLETED,
        HarnessBoundary.COMPLETION_PROPOSED,
    ]
    assert isinstance(observed[0].control_data, TaskContract)
    assert isinstance(observed[1].control_data, ActionProposal)
    assert isinstance(observed[2].control_data, RuntimeObservationProjection)
    assert isinstance(observed[3].control_data, CompletionProposal)
    assert observed[1].control_data.parameter_digest == "a" * 64
    assert observed[2].control_data.criterion_evidence_refs["tests"] == (
        "evidence://tests/1",)


def test_athena_cannot_receive_typed_action_or_completion_control_hooks():
    observed = []
    identity = HarnessAdapterIdentity(
        harness_id="athena", adapter_version="1.0.0", host_version="1.0.0",
        protocol_version="1.1.0",
        capabilities=HarnessCapabilities(pre_action=True, completion_interception=False),
        verified=True)
    session = CognitiveRuntimeHarnessSession(
        identity,
        CallbackCognitiveHarnessLifecycle(
            lambda event: observed.append(event) or {"status": "observed"}))

    async def send_unsupported_hooks():
        await session.start_task(
            TaskContract("task-1", "Test task", {"tests": "Checks pass"}),
            event_id="start-1", sequence=1)
        action_result = await session.propose_action(ActionProposal(
            action_id="action-1", action_class="implementation", tool_name="edit",
            operation="write", purpose="Change a file", affected_criterion_ids=("tests",)),
            event_id="proposal-2", sequence=2)
        completion_result = await session.propose_completion(CompletionProposal(
            proposal_id="complete-3", claim="Done", required_criterion_ids=("tests",)),
            event_id="completion-3", sequence=3)
        return action_result, completion_result

    action_result, completion_result = asyncio.run(send_unsupported_hooks())

    assert action_result["status"] == "degraded"
    assert completion_result["status"] == "degraded"
    assert [event.boundary for event in observed] == [HarnessBoundary.TASK_START]


@pytest.mark.parametrize(
    "boundary", [HarnessBoundary.ACTION_AUTHORIZED, HarnessBoundary.ACTION_REJECTED])
def test_action_resolution_boundaries_require_verified_action_blocking(boundary):
    observed = []
    identity = HarnessAdapterIdentity(
        harness_id="athena", adapter_version="1.0.0", host_version="1.0.0",
        protocol_version="1.1.0",
        capabilities=HarnessCapabilities(pre_action=True, action_blocking=False),
        verified=True)
    session = CognitiveRuntimeHarnessSession(
        identity,
        CallbackCognitiveHarnessLifecycle(
            lambda event: observed.append(event) or {"status": "observed"}))

    async def send_boundaries():
        await session.start_task(
            TaskContract("task-1", "Test task", {"tests": "Checks pass"}),
            event_id="start-1", sequence=1)
        return await session.dispatch(HarnessBoundaryEvent(
            boundary, event_id="action-result-2", sequence=2, action_id="action-1"))

    result = asyncio.run(send_boundaries())

    assert result["status"] == "degraded"
    assert result["decision"] == "capability_unavailable"
    assert [event.boundary for event in observed] == [HarnessBoundary.TASK_START]


def test_task_contract_preserves_optional_criteria_and_legacy_defaults():
    legacy = TaskContract.from_mapping({
        "task_id": "task-1", "objective": "Improve the feature",
        "criterion_claims": {"core": "Core behavior", "optional": "Optional polish"},
    })
    explicit = TaskContract.from_mapping({
        "task_id": "task-2", "objective": "Improve the feature",
        "criterion_claims": {"core": "Core behavior", "optional": "Optional polish"},
        "required_criterion_ids": ["core"],
    })

    assert set(legacy.required_criterion_ids) == {"core", "optional"}
    assert explicit.required_criterion_ids == ("core",)
    assert set(explicit.criterion_claims) == {"core", "optional"}


def test_task_contract_supports_harness_tasks_without_acceptance_criteria():
    contract = TaskContract(
        task_id="task-1", objective="Inspect the requested system",
        criterion_claims={}, required_criterion_ids=())

    assert contract.criterion_claims == {}
    assert contract.required_criterion_ids == ()


def test_criterion_scoped_evidence_accepts_all_64_validator_refs():
    refs = tuple(f"evidence://validator/{index}" for index in range(64))
    observation = RuntimeObservationProjection(
        action_id="action-1", action_index=1,
        criterion_status={"tests": "verified"},
        evidence_refs=refs,
        criterion_evidence_refs={"tests": refs},
    )
    completion = CompletionProposal(
        proposal_id="complete-1", claim="Core criteria are ready",
        required_criterion_ids=("tests",),
        criterion_evidence_refs={"tests": refs},
    )

    assert len(observation.criterion_evidence_refs["tests"]) == 64
    assert len(completion.criterion_evidence_refs["tests"]) == 64


def test_neutral_runtime_contracts_contain_no_harness_product_names():
    runtime = Path(__file__).resolve().parents[1] / "lib" / "runtime"
    neutral_modules = (
        "harness.py", "actions.py", "validators.py", "completion.py",
        "bindings.py", "validator_reuse.py",
    )
    forbidden = (
        "codex_host", "thread/start", "turn/start", "item/tool/call",
        "CODEX_HOME", "Claude", "Athena",
    )

    violations = []
    for filename in neutral_modules:
        source = (runtime / filename).read_text(encoding="utf-8")
        for token in forbidden:
            if token.casefold() in source.casefold():
                violations.append(f"{filename} contains {token}")

    assert not violations, "harness-specific details leaked into DP core: " + "; ".join(
        violations)
