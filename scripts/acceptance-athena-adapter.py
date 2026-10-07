#!/usr/bin/env python3
"""Qualify DP's structural Athena adapter against Athena's public in-memory API.

This is an opt-in cross-repository acceptance check. Athena remains an
optional harness: it is loaded from a sibling checkout (or ATHENA_ROOT) only
when this script runs, and is not a DigitalPsychology package dependency.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import sys
from typing import Any


DP_ROOT = Path(__file__).resolve().parents[1]
ATHENA_ROOT = Path(os.environ.get("ATHENA_ROOT", DP_ROOT.parent / "Athena")).resolve()
ATHENA_SRC = ATHENA_ROOT / "src"

if str(DP_ROOT) not in sys.path:
    sys.path.insert(0, str(DP_ROOT))

from cognitive_runtime_core.harness import (  # noqa: E402
    CallbackCognitiveHarnessLifecycle,
    CognitiveRuntimeHarnessSession,
    HarnessBoundary,
)
from cognitive_runtime_core.integrations.athena import (  # noqa: E402
    AthenaRuntimeAdapter,
)


TASK_ID = "triad-athena-adapter-acceptance"


def _status(value: Any) -> str:
    return str(getattr(value, "value", value))


async def qualify() -> tuple[int, int, str, str]:
    if not (ATHENA_SRC / "athena").is_dir():
        raise FileNotFoundError(
            f"Athena source package not found at {ATHENA_SRC}; set ATHENA_ROOT"
        )
    if str(ATHENA_SRC) not in sys.path:
        sys.path.insert(0, str(ATHENA_SRC))

    try:
        from athena.protocol.tasks import AgentRequest
        from athena.service.service import AthenaService
    except ImportError as exc:
        raise RuntimeError(f"Athena public service API could not be imported: {exc}") from exc

    service = AthenaService.in_memory()
    await asyncio.wait_for(service.start(), timeout=15)
    try:
        task = await asyncio.wait_for(
            service.submit(
                AgentRequest(
                    prompt="What is 2+2? Reply with the number only.",
                    task_id=TASK_ID,
                ),
                wait=True,
            ),
            timeout=30,
        )
        task_id = task.id
        result = await service.get_result(task_id)
        if result is None or _status(result.status) != "COMPLETE":
            result_status = _status(getattr(result, "status", None))
            raise AssertionError(
                f"Athena in-memory task did not complete: {result_status}"
            )

        adapter = AthenaRuntimeAdapter(service)
        contract = await adapter.get_task_contract(task_id)
        if contract.task_id != task_id or "2+2" not in contract.objective:
            raise AssertionError("Athena TaskSpec did not project through the neutral contract")

        normalized = [event async for event in adapter.stream_events(task_id)]
        boundaries = [event.boundary for event in normalized]
        required = {
            HarnessBoundary.TASK_START,
            HarnessBoundary.MODEL_BOUNDARY,
            HarnessBoundary.TASK_END,
        }
        if not required.issubset(set(boundaries)):
            raise AssertionError(
                "Athena public stream did not map task, model, and terminal boundaries"
            )

        delivered = []
        session = CognitiveRuntimeHarnessSession(
            adapter.identity,
            CallbackCognitiveHarnessLifecycle(
                lambda event: delivered.append(event) or {"status": "observed"}
            ),
        )
        summary = await adapter.observe_task(task_id, session)
        if summary.events_dispatched != len(normalized):
            raise AssertionError("Athena stream and shared lifecycle dispatch counts differ")
        delivered_boundaries = {event.boundary for event in delivered}
        if not {
            HarnessBoundary.TASK_START,
            HarnessBoundary.TASK_END,
        }.issubset(delivered_boundaries):
            raise AssertionError("Shared lifecycle did not receive the Athena task envelope")

        projection = await adapter.get_result(task_id)
        if projection is None or _status(projection.status) != "COMPLETE":
            raise AssertionError("Athena result did not project through the neutral result API")

        # No independent host attestation is available from this local service
        # instance. Keep the adapter unverified and its control gaps explicit.
        if adapter.identity.verified:
            raise AssertionError("Local Athena service was promoted without host attestation")
        if adapter.capabilities.action_blocking:
            raise AssertionError("Athena observation must not claim action blocking")
        if adapter.capabilities.completion_interception:
            raise AssertionError("Athena observation must not claim completion interception")
        if HarnessBoundary.COMPLETION_PROPOSED in boundaries:
            raise AssertionError("Athena terminal event was mislabeled as a completion proposal")

        return (
            len(normalized),
            summary.degraded_events,
            adapter.identity.adapter_version,
            adapter.identity.catalog_pin["athena_task_port_contract"],
        )
    finally:
        await asyncio.wait_for(service.stop(), timeout=10)


def main() -> int:
    try:
        (event_count, degraded_count,
         adapter_version, athena_contract) = asyncio.run(qualify())
    except Exception as exc:
        print(
            f"ATHENA_ADAPTER_ACCEPTANCE=FAIL {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1
    print(
        "ATHENA_ADAPTER_ACCEPTANCE=PASS "
        f"events={event_count} degraded={degraded_count} "
        f"adapter={adapter_version} athena_contract={athena_contract} "
        "action_blocking=false completion_interception=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
