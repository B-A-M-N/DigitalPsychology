from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from lib.runtime.adapters import AthenaRuntimeAdapter, AthenaTaskResultProjection
from lib.runtime.harness import (ActionProposal,
                                 CallbackCognitiveHarnessLifecycle,
                                 CognitiveHarnessLifecycle,
                                 CognitiveRuntimeHarnessSession,
                                 HARNESS_ADAPTER_PROTOCOL_VERSION,
                                 HarnessAdapterIdentity,
                                 HarnessBoundary, HarnessCapabilities,
                                 RuntimeObservationProjection, TaskContract)


@dataclass(frozen=True)
class _Event:
    id: str
    type: str
    sequence: int
    task_id: str
    payload: object


class _Port:
    def __init__(self, events=(), result=None, task=None):
        self.events = tuple(events)
        self.result = result
        self.task = task or SimpleNamespace(
            id="task-1", objective="Default Athena task", acceptance_criteria=(),
            metadata={})
        self.stream_calls = []
        self.result_calls = []
        self.task_calls = []
        self.include_before_cursor = False

    async def get_task(self, task_id):
        self.task_calls.append(task_id)
        return self.task

    async def stream_events(self, task_id, after_sequence=0):
        self.stream_calls.append((task_id, after_sequence))
        for event in self.events:
            if self.include_before_cursor or event.sequence > after_sequence:
                yield event

    async def get_result(self, task_id):
        self.result_calls.append(task_id)
        return self.result


def _event(sequence=1, event_type="CapabilityRequested", **overrides):
    return _Event(
        id=overrides.get("id", f"evt-{sequence}"),
        type=overrides.get("type", event_type),
        sequence=overrides.get("sequence", sequence),
        task_id=overrides.get("task_id", "task-1"),
        payload=overrides.get("payload", {}),
    )


def test_athena_events_map_only_canonical_boundaries_and_safe_refs():
    secret_model_text = "private model output must not cross the adapter"
    port = _Port([
        _event(1, "TaskStarted", payload={"prompt": secret_model_text}),
        _event(2, "ModelDelta", payload={
            "text": secret_model_text, "arguments": {"secret": secret_model_text},
            "evidence_refs": ["artifact://sha256/" + "a" * 64],
        }),
        _event(3, "CapabilityRequested", payload={"arguments": secret_model_text}),
        _event(4, "CapabilityCompleted", payload={"result": secret_model_text}),
        _event(5, "UnmappedTelemetry", payload={"evidence_refs": 7}),
    ])
    adapter = AthenaRuntimeAdapter(port)

    events = asyncio.run(_collect(adapter.stream_events("task-1")))

    assert [event.boundary for event in events] == [
        HarnessBoundary.TASK_START,
        HarnessBoundary.MODEL_BOUNDARY,
        HarnessBoundary.ACTION_PROPOSED,
        HarnessBoundary.ACTION_COMPLETED,
    ]
    assert events[1].evidence_refs == ("artifact://sha256/" + "a" * 64,)
    assert isinstance(events[2].control_data, ActionProposal)
    assert events[2].control_data.action_class == "unknown"
    assert events[2].control_data.affected_criterion_ids == ()
    assert events[2].control_data.relation_certainty == "unknown"
    assert isinstance(events[3].control_data, RuntimeObservationProjection)
    assert events[3].control_data.evidence_refs == ()
    serialized = repr([event.to_dict() for event in events])
    assert secret_model_text not in serialized
    assert "arguments" not in serialized
    assert all(not hasattr(event, "payload") for event in events)


def test_stream_cursor_replays_only_later_durable_sequences():
    port = _Port([_event(1), _event(2), _event(3, "ExecutionExited")])
    adapter = AthenaRuntimeAdapter(port)

    replay = asyncio.run(_collect(adapter.stream_events("task-1", after_sequence=1)))
    fresh = asyncio.run(_collect(adapter.stream_events("task-1", after_sequence=0)))

    assert port.stream_calls == [("task-1", 1), ("task-1", 0)]
    assert [item.sequence for item in replay] == [2, 3]
    assert [item.sequence for item in fresh] == [1, 2, 3]


@pytest.mark.parametrize("event", [
    _event(sequence=True),
    _event(sequence=0),
    _event(id="bad id"),
    _event(task_id="task-other"),
])
def test_malformed_or_cross_task_durable_events_are_rejected(event):
    port = _Port([event])
    if event.sequence == 0:
        # Simulate a broken source that returns an item at/before the cursor.
        port.include_before_cursor = True
    adapter = AthenaRuntimeAdapter(port)

    with pytest.raises(ValueError):
        asyncio.run(_collect(adapter.stream_events("task-1")))


def test_duplicate_or_out_of_order_replay_sequence_is_rejected():
    port = _Port([_event(1), _event(1, "CapabilityCompleted")])

    with pytest.raises(ValueError, match="sequence must increase"):
        asyncio.run(_collect(AthenaRuntimeAdapter(port).stream_events("task-1")))


@pytest.mark.parametrize("refs", ["not-a-sequence", ["ok", {"bad": "ref"}], ["x", "x"]])
def test_malformed_explicit_event_evidence_refs_are_rejected(refs):
    port = _Port([_event(payload={"evidence_refs": refs})])

    with pytest.raises(ValueError, match="evidence"):
        asyncio.run(_collect(AthenaRuntimeAdapter(port).stream_events("task-1")))


def test_result_projection_keeps_status_and_refs_but_drops_free_form_fields():
    secret_summary = "model prose and internal details"
    result = SimpleNamespace(
        task_id="task-1",
        status="COMPLETE",
        summary=secret_summary,
        evidence=(SimpleNamespace(ref="repo://file/README.md", summary=secret_summary),),
        artifacts=(SimpleNamespace(uri="artifact://sha256/" + "b" * 64,
                                   metadata={"text": secret_summary}),),
        unresolved=(secret_summary,),
    )
    port = _Port(result=result)
    adapter = AthenaRuntimeAdapter(port)

    projection = asyncio.run(adapter.get_result("task-1"))

    assert isinstance(projection, AthenaTaskResultProjection)
    assert projection.to_dict() == {
        "task_id": "task-1",
        "status": "COMPLETE",
        "evidence_refs": [
            "context:repo://file/README.md",
            "artifact:artifact://sha256/" + "b" * 64,
        ],
    }
    assert secret_summary not in repr(projection.to_dict())
    assert port.result_calls == ["task-1"]


def test_missing_result_is_projected_as_none_and_foreign_result_is_rejected():
    assert asyncio.run(AthenaRuntimeAdapter(_Port()).get_result("task-1")) is None
    adapter = AthenaRuntimeAdapter(_Port(result=SimpleNamespace(
        task_id="task-other", status="COMPLETE", evidence=(), artifacts=())))

    with pytest.raises(ValueError, match="differs from the requested task"):
        asyncio.run(adapter.get_result("task-1"))


def test_capabilities_degrade_to_observation_only_and_identity_is_unverified():
    from cognitive_runtime_core.integrations.athena import (
        ATHENA_TASK_PORT_CONTRACT_VERSION,
    )

    adapter = AthenaRuntimeAdapter(_Port(), host_version="Athena 0.1")

    assert adapter.capabilities.model_boundary is True
    assert adapter.capabilities.pre_action is True
    assert adapter.capabilities.post_action is True
    assert adapter.capabilities.action_blocking is False
    assert adapter.capabilities.validator_boundary is True
    assert adapter.capabilities.completion_interception is False
    assert adapter.capabilities.context_projection is False
    assert adapter.capabilities.planning_boundary is False
    assert adapter.capabilities.multi_agent_identity is False
    assert adapter.identity.verified is False
    assert adapter.identity.catalog_pin == {
        "athena_task_port_contract": ATHENA_TASK_PORT_CONTRACT_VERSION,
    }


def test_verified_identity_must_pin_exact_athena_adapter_contract():
    from cognitive_runtime_core.integrations.athena import (
        ATHENA_ADAPTER_VERSION, ATHENA_TASK_PORT_CONTRACT_VERSION,
    )

    adapter = AthenaRuntimeAdapter(_Port())
    identity = HarnessAdapterIdentity(
        harness_id="athena", adapter_version=ATHENA_ADAPTER_VERSION,
        host_version="athena-test-host-1.0.0",
        protocol_version=HARNESS_ADAPTER_PROTOCOL_VERSION,
        capabilities=adapter.capabilities,
        catalog_pin={
            "athena_task_port_contract": ATHENA_TASK_PORT_CONTRACT_VERSION,
        }, verified=True)
    assert AthenaRuntimeAdapter(_Port(), verified_identity=identity).identity.verified

    wrong_capabilities = HarnessCapabilities(
        model_boundary=True, pre_action=True, post_action=True,
        action_blocking=True, validator_boundary=True)
    forged = HarnessAdapterIdentity(
        harness_id="athena", adapter_version=ATHENA_ADAPTER_VERSION,
        host_version="athena-test-host-1.0.0",
        protocol_version=HARNESS_ADAPTER_PROTOCOL_VERSION,
        capabilities=wrong_capabilities,
        catalog_pin={
            "athena_task_port_contract": ATHENA_TASK_PORT_CONTRACT_VERSION,
        }, verified=True)
    with pytest.raises(ValueError, match="differs from adapter capabilities"):
        AthenaRuntimeAdapter(_Port(), verified_identity=forged)
    assert adapter.identity.effective_capabilities == HarnessCapabilities()


def test_athena_observation_uses_shared_dp_lifecycle_and_degrades_unsupported_hooks():
    observed = []
    task = SimpleNamespace(
        id="task-1",
        objective="Update the parser",
        acceptance_criteria=(
            SimpleNamespace(id="parser", description="Parser handles nested input",
                            required=True),
            SimpleNamespace(id="docs", description="Usage is documented", required=False),
        ),
        non_goals=("Do not change the public API",),
        # Arbitrary metadata can contain raw prompt/task text and must not cross.
        metadata={"prompt": "PRIVATE RAW TASK TEXT", "private": "not projected"},
    )
    port = _Port([
        _event(1, "TaskCreated"),
        _event(2, "TaskStarted"),
        _event(3, "ModelResponseCompleted"),
        _event(4, "CapabilityRequested"),
        _event(5, "VerificationCheckCompleted", payload={
            "criterion": "parser", "status": "passed",
            "evidence_refs": ["evidence://verification/parser"],
        }),
        _event(6, "TaskCompleted"),
    ], task=task)
    adapter = AthenaRuntimeAdapter(port)
    lifecycle = CallbackCognitiveHarnessLifecycle(
        lambda event: observed.append(event) or {"status": "observed"})
    session = CognitiveRuntimeHarnessSession(adapter.identity, lifecycle)

    summary = asyncio.run(adapter.observe_task("task-1", session))

    assert summary.events_dispatched == 6
    assert summary.first_sequence == 1
    assert summary.last_sequence == 6
    # Athena's unverified declaration grants no effective model/action hooks.
    assert summary.degraded_events == 3
    assert [event.boundary for event in observed] == [
        HarnessBoundary.TASK_START,
        HarnessBoundary.OBSERVATION,
        HarnessBoundary.TASK_END,
    ]
    assert isinstance(observed[0].control_data, TaskContract)
    assert observed[0].control_data.objective == "Update the parser"
    assert observed[0].control_data.criterion_claims == {
        "parser": "Parser handles nested input",
        "docs": "Usage is documented",
    }
    assert observed[0].control_data.required_criterion_ids == ("parser",)
    assert observed[0].control_data.non_goals == ("Do not change the public API",)
    assert "PRIVATE RAW TASK TEXT" not in repr(observed[0].to_dict())
    assert port.task_calls == ["task-1"]
    assert adapter.capabilities.action_blocking is False
    assert adapter.capabilities.completion_interception is False


def test_adapter_requires_both_injected_public_port_methods():
    with pytest.raises(TypeError, match="get_task, stream_events, and get_result"):
        AthenaRuntimeAdapter(SimpleNamespace(stream_events=lambda *_args, **_kwargs: ()))


async def _collect(async_events):
    return [event async for event in async_events]
