"""Multi-provider System-1 portfolio with independent runtime state.

Each provider owns its own breaker, cache, calibration identity, and provider
binding. The portfolio records independent observations and never averages or
fuses their probabilities.
"""
from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Mapping, Sequence

from .system_one_contracts import SystemOneFrame
from .system_one_runtime import SystemOneResult, SystemOneRuntime


@dataclass(frozen=True)
class ProviderDecision:
    provider_id: str
    question_id: str
    result: SystemOneResult
    decision_id: str | None
    observed_revision: int
    elapsed_ms: float


@dataclass(frozen=True)
class SemanticDecisionBundle:
    question_id: str
    observations: tuple[ProviderDecision, ...]
    selected_provider_id: str | None
    selection_reason: str

    @property
    def selected_result(self) -> SystemOneResult | None:
        if self.selected_provider_id is None:
            return None
        return next((item.result for item in self.observations
                     if item.provider_id == self.selected_provider_id), None)


class SystemOnePortfolio:
    """Evaluate independently configured System-1 runtimes for one decision."""

    def __init__(self, runtimes: Sequence[SystemOneRuntime]) -> None:
        if not runtimes or not all(isinstance(item, SystemOneRuntime) for item in runtimes):
            raise ValueError("System-1 portfolio requires one or more runtimes")
        providers = [item.backend_id for item in runtimes]
        if len(providers) != len(set(providers)):
            raise ValueError("System-1 portfolio providers must have unique backend IDs")
        self.runtimes = tuple(runtimes)

    def evaluate(self, frames: Mapping[str, SystemOneFrame], *,
                 observed_revision: int, mode: str,
                 selected_provider_id: str | None = None,
                 selection_reason: str = "provider_not_selected") -> SemanticDecisionBundle:
        if selection_reason == "provider_not_selected":
            if selected_provider_id is not None:
                raise ValueError("selection reason conflicts with selected provider")
        else:
            if selected_provider_id not in {item.backend_id for item in self.runtimes}:
                raise ValueError("selected provider is not part of the portfolio")
        question_id = next(iter(frames.values()), None)
        question_id = question_id.question_ids[0] if question_id is not None else ""
        observations: list[ProviderDecision] = []
        for runtime in self.runtimes:
            frame = frames.get(runtime.backend_id)
            if frame is None:
                continue
            started = time.perf_counter()
            result = runtime.judge(frame, mode=mode)
            observations.append(ProviderDecision(
                provider_id=runtime.backend_id,
                question_id=frame.question_ids[0],
                result=result,
                decision_id=result.judgment.request_id if result.judgment else None,
                observed_revision=observed_revision,
                elapsed_ms=(time.perf_counter() - started) * 1000.0))
        return SemanticDecisionBundle(
            question_id=question_id, observations=tuple(observations),
            selected_provider_id=selected_provider_id,
            selection_reason=selection_reason)


__all__ = ["ProviderDecision", "SemanticDecisionBundle", "SystemOnePortfolio"]
