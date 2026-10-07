"""One task contract through the Codex, Athena, and reference ingress paths."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from cognitive_runtime_core.harness import (
    CallbackCognitiveHarnessLifecycle,
    CognitiveRuntimeHarnessSession,
    CompletionProposal,
    HARNESS_ADAPTER_PROTOCOL_VERSION,
    HarnessAdapterIdentity,
    HarnessBoundary,
    HarnessCapabilities,
    HarnessBoundaryEvent,
    HarnessConformanceLevel,
    HarnessRequirement,
    HarnessRequirementError,
    TaskContract,
)
from cognitive_runtime_core.integrations.athena import AthenaRuntimeAdapter
from cognitive_runtime_core.integrations.athena import (
    ATHENA_ADAPTER_VERSION, ATHENA_TASK_PORT_CONTRACT_VERSION,
)
from cognitive_runtime_core.integrations.codex.adapter import CodexHarnessAdapter
from cognitive_runtime_core.integrations.reference import ReferenceHarnessAdapter


@dataclass(frozen=True)
class _AthenaEvent:
    id: str
    type: str
    sequence: int
    task_id: str
    payload: object = None


class _AthenaPort:
    def __init__(self, events):
        self.events = tuple(events)

    async def get_task(self, task_id):
        return SimpleNamespace(
            id=task_id, objective="Update the parser",
            acceptance_criteria=(SimpleNamespace(
                id="parser", description="Nested input parses", required=True),),
            non_goals=("Keep the public API unchanged",),
            metadata={"private": "must not cross"})

    async def stream_events(self, task_id, after_sequence=0):
        for event in self.events:
            if event.sequence > after_sequence:
                yield event

    async def get_result(self, task_id):
        return None


def _verified_identity(harness_id: str) -> HarnessAdapterIdentity:
    return HarnessAdapterIdentity(
        harness_id=harness_id, adapter_version="1.0.0", host_version="1.0.0",
        protocol_version="1.1.0",
        capabilities=HarnessCapabilities(
            model_boundary=True, pre_action=True, post_action=True,
            action_blocking=True, context_projection=True,
            validator_boundary=True, completion_interception=True),
        catalog_pin={"fixture": "sha256:" + "a" * 64}, verified=True)


def test_named_harness_requirements_gate_only_on_verified_capabilities():
    full_control = _verified_identity("codex-fixture")
    requirement = HarnessRequirement.for_level(
        HarnessConformanceLevel.FULL_CONTROL)

    assert full_control.require(requirement) is full_control
    assert HarnessConformanceLevel.FULL_CONTROL in full_control.conformance_levels

    observation_only = HarnessAdapterIdentity(
        harness_id="another-harness", adapter_version="1.0.0",
        host_version="1.0.0", protocol_version=HARNESS_ADAPTER_PROTOCOL_VERSION,
        capabilities=HarnessCapabilities(
            model_boundary=True, pre_action=True, post_action=True,
            validator_boundary=True),
        verified=True)
    assert observation_only.conformance_levels == (
        HarnessConformanceLevel.OBSERVATION,
        HarnessConformanceLevel.BEHAVIORAL_OBSERVATION)
    with pytest.raises(HarnessRequirementError, match="full_control") as raised:
        observation_only.require(requirement)
    assert raised.value.missing_capabilities == (
        "action_blocking", "context_projection", "completion_interception")


def test_one_task_keeps_shared_semantics_and_reports_athena_capability_gaps():
    async def run():
        task = TaskContract(
            task_id="task-1", objective="Update the parser",
            criterion_claims={"parser": "Nested input parses"},
            non_goals=("Keep the public API unchanged",))
        expected = [
            HarnessBoundary.TASK_START,
            HarnessBoundary.MODEL_BOUNDARY,
            HarnessBoundary.ACTION_PROPOSED,
            HarnessBoundary.ACTION_COMPLETED,
            HarnessBoundary.VALIDATION_COMPLETED,
            HarnessBoundary.COMPLETION_PROPOSED,
            HarnessBoundary.TASK_END,
        ]

        codex_events = []
        codex_session = CognitiveRuntimeHarnessSession(
            _verified_identity("codex-fixture"),
            CallbackCognitiveHarnessLifecycle(
                lambda event: codex_events.append(event) or {"status": "observed"}))
        codex = CodexHarnessAdapter(codex_session)
        await codex.start_task(task)
        await codex.model_boundary(transport_event_id="model-1", context="bounded")
        await codex.action_proposed(
            invocation_id="action-1", tool_name="write", operation="update",
            purpose="Implement parser change", arguments={"path": "parser.py"},
            affected_criterion_ids=("parser",), action_class="write")
        await codex.action_completed(
            invocation_id="action-1", action_index=1,
            criterion_status={"parser": "verified"}, progress_made=True,
            evidence_refs=("evidence://parser",),
            criterion_evidence_refs={"parser": ("evidence://parser",)})
        await codex.validation_completed(
            validator_id="parser-check", criterion_ids=("parser",),
            evidence_refs=("evidence://parser",), passed=True)
        await codex.completion_proposed(CompletionProposal(
            proposal_id="complete-1", claim="Parser change is complete",
            required_criterion_ids=("parser",),
            criterion_evidence_refs={"parser": ("evidence://parser",)}))
        await codex.task_end()

        reference_events = []
        reference = ReferenceHarnessAdapter(
            CallbackCognitiveHarnessLifecycle(
                lambda event: reference_events.append(event) or {"status": "observed"}),
            capabilities=HarnessCapabilities(
                model_boundary=True, pre_action=True, post_action=True,
                action_blocking=True, context_projection=True,
                validator_boundary=True, completion_interception=True),
            host_version="reference-test-host-1.0.0", verified=True)
        canonical = (
            HarnessBoundaryEvent(HarnessBoundary.TASK_START, "ref-start", 1,
                                 control_data=task),
            HarnessBoundaryEvent(HarnessBoundary.MODEL_BOUNDARY, "ref-model", 2),
            HarnessBoundaryEvent(HarnessBoundary.ACTION_PROPOSED, "ref-action", 3,
                                 control_data=codex_events[2].control_data),
            HarnessBoundaryEvent(HarnessBoundary.ACTION_COMPLETED, "ref-result", 4,
                                 control_data=codex_events[3].control_data),
            HarnessBoundaryEvent(HarnessBoundary.VALIDATION_COMPLETED, "ref-validate", 5,
                                 criterion_ids=("parser",), validator_id="parser-check",
                                 validation_passed=True,
                                 evidence_refs=("evidence://parser",)),
            HarnessBoundaryEvent(
                HarnessBoundary.COMPLETION_PROPOSED, "ref-complete", 6,
                control_data=codex_events[5].control_data),
            HarnessBoundaryEvent(HarnessBoundary.TASK_END, "ref-end", 7),
        )
        reference_summary = await reference.dispatch(canonical)

        athena_events = [
            _AthenaEvent("athena-start", "TaskStarted", 1, "task-1"),
            _AthenaEvent("athena-model", "ModelRequestStarted", 2, "task-1"),
            _AthenaEvent("athena-action", "CapabilityRequested", 3, "task-1"),
            _AthenaEvent("athena-result", "CapabilityCompleted", 4, "task-1"),
            _AthenaEvent("athena-validation", "VerificationCheckCompleted", 5,
                         "task-1", {"validator_id": "parser-check", "status": "passed",
                                    "criterion_ids": ["parser"],
                                    "evidence_refs": ["evidence://parser"]}),
            _AthenaEvent("athena-end", "TaskCompleted", 6, "task-1"),
        ]
        athena_observed = []
        athena = AthenaRuntimeAdapter(
            _AthenaPort(athena_events),
            verified_identity=HarnessAdapterIdentity(
                harness_id="athena", adapter_version=ATHENA_ADAPTER_VERSION,
                host_version="athena-test-host-1.0.0",
                protocol_version=HARNESS_ADAPTER_PROTOCOL_VERSION,
                capabilities=HarnessCapabilities(
                    model_boundary=True, pre_action=True, post_action=True,
                    validator_boundary=True),
                catalog_pin={
                    "athena_task_port_contract": ATHENA_TASK_PORT_CONTRACT_VERSION,
                }, verified=True))
        athena_session = CognitiveRuntimeHarnessSession(
            athena.identity,
            CallbackCognitiveHarnessLifecycle(
                lambda event: athena_observed.append(event) or {"status": "observed"}))
        summary = await athena.observe_task("task-1", athena_session)

        return (expected, codex_events, reference_events, reference_summary,
                athena, athena_observed, summary)

    expected, codex, reference, reference_summary, athena, observed, summary = asyncio.run(run())
    assert [event.boundary for event in codex] == expected
    assert [event.boundary for event in reference] == expected
    assert [event.boundary for event in codex] == [event.boundary for event in reference]
    assert reference_summary.events_dispatched == len(expected)
    assert reference_summary.degraded_events == 0
    assert reference[0].event_id == "ref-start"
    assert codex[0].control_data.to_dict() == reference[0].control_data.to_dict()
    assert codex[2].control_data.to_dict() == reference[2].control_data.to_dict()
    assert codex[3].control_data.to_dict() == reference[3].control_data.to_dict()
    assert codex[5].control_data.to_dict() == reference[5].control_data.to_dict()

    # Athena exposes the common observation boundaries through a separately
    # pinned identity. Its public stream cannot block action execution or
    # intercept completion, so those unavailable control claims stay explicit.
    assert athena.identity.verified is True
    assert athena.capabilities.action_blocking is False
    assert athena.capabilities.completion_interception is False
    assert [event.boundary for event in observed] == [
        HarnessBoundary.TASK_START,
        HarnessBoundary.MODEL_BOUNDARY,
        HarnessBoundary.ACTION_COMPLETED,
        HarnessBoundary.VALIDATION_COMPLETED,
        HarnessBoundary.TASK_END,
    ]
    assert observed[0].control_data.to_dict() == codex[0].control_data.to_dict()
    assert summary.degraded_events == 1  # ACTION_PROPOSED cannot be enforced.
    assert "action_blocking_unavailable" in athena.identity.degradation_claims
    assert "completion_interception_unavailable" in athena.identity.degradation_claims
    # Athena's terminal event is not re-labeled as a completion proposal: its
    # public API has no pre-completion boundary to review.
    assert HarnessBoundary.COMPLETION_PROPOSED not in [event.boundary for event in observed]
