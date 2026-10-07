"""Pass through canonical lifecycle events for contract conformance.

This adapter is useful for deterministic integration tests and hosts that
already emit the shared event contract. It does not represent a real harness
or establish production capability claims by itself.
"""
from __future__ import annotations

from collections.abc import AsyncIterable, Iterable, Mapping
from typing import Any

from ...harness import (
    HARNESS_ADAPTER_PROTOCOL_VERSION,
    CognitiveHarnessLifecycle,
    CognitiveRuntimeHarnessSession,
    HarnessAdapterIdentity,
    HarnessBoundaryEvent,
    HarnessCapabilities,
    HarnessDispatchSummary,
    dispatch_harness_events,
)

REFERENCE_ADAPTER_VERSION = "1.0.0"


async def _as_async_events(events: Any) -> AsyncIterable[HarnessBoundaryEvent]:
    if isinstance(events, AsyncIterable):
        async for event in events:
            yield event
        return
    if isinstance(events, (str, bytes, bytearray, Mapping)):
        raise TypeError("reference event input must be an iterable of canonical events")
    try:
        iterator = iter(events)
    except TypeError as exc:
        raise TypeError(
            "reference event input must be an iterable of canonical events") from exc
    for event in iterator:
        yield event


class ReferenceHarnessAdapter:
    """Dispatch already-normalized lifecycle events through a pinned session."""

    def __init__(self, lifecycle: CognitiveHarnessLifecycle, *,
                 capabilities: HarnessCapabilities | None = None,
                 host_version: str = "reference-host",
                 verified: bool = False) -> None:
        declared = capabilities or HarnessCapabilities()
        self.identity = HarnessAdapterIdentity(
            harness_id="reference",
            adapter_version=REFERENCE_ADAPTER_VERSION,
            host_version=host_version,
            protocol_version=HARNESS_ADAPTER_PROTOCOL_VERSION,
            capabilities=declared,
            catalog_pin={"canonical_event_contract": HARNESS_ADAPTER_PROTOCOL_VERSION},
            verified=verified,
        )
        self.session = CognitiveRuntimeHarnessSession(self.identity, lifecycle)

    async def dispatch(self, events: Iterable[HarnessBoundaryEvent]
                       | AsyncIterable[HarnessBoundaryEvent], *,
                       max_events: int = 4096) -> HarnessDispatchSummary:
        """Validate and dispatch a bounded stream of canonical events."""
        return await dispatch_harness_events(
            self.session, _as_async_events(events), max_events=max_events)


__all__ = ["REFERENCE_ADAPTER_VERSION", "ReferenceHarnessAdapter"]
