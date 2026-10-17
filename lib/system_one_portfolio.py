"""Multi-provider System-1 portfolio with independent runtime state.

Each provider owns its own breaker, cache, calibration identity, and provider
binding. The portfolio records independent observations and never averages or
fuses their probabilities.
"""
from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Mapping, Sequence

from .system_one_contracts import SystemOneFrame, canonical_hash
from .system_one_runtime import SystemOneResult, SystemOneRuntime


class PortfolioDecisionMismatch(ValueError):
    """Provider frames do not bind one common host decision snapshot."""


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
    decision_basis_digest: str | None = None

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

    @staticmethod
    def _common_decision_basis(frame: SystemOneFrame) -> tuple[object, ...]:
        return (frame.identity.to_dict(), frame.request_id, frame.sequence,
                frame.state_digest, frame.question_bundle_digest,
                frame.request_digest, frame.policy_hash, frame.question_ids,
                frame.protocol_version, frame.protocol_hash)

    @staticmethod
    def _provider_identity(frame: SystemOneFrame) -> tuple[object, ...]:
        return (frame.backend_id, frame.model_id, frame.model_revision,
                frame.model_content_hash, frame.provider_protocol_hash)

    def _validate_common_decision(self, frames: Mapping[str, SystemOneFrame]) -> None:
        if not frames:
            raise PortfolioDecisionMismatch("portfolio requires at least one frame")
        for provider_id, frame in frames.items():
            if provider_id != frame.backend_id:
                raise PortfolioDecisionMismatch(
                    "provider frame is not keyed by its own backend ID")
        reference = self._common_decision_basis(next(iter(frames.values())))
        identities = {self._provider_identity(frame) for frame in frames.values()}
        if len(identities) != len(frames):
            raise PortfolioDecisionMismatch(
                "portfolio frames do not have provider-specific identities")
        for provider_id, frame in frames.items():
            if self._common_decision_basis(frame) != reference:
                raise PortfolioDecisionMismatch(
                    f"provider frame differs from the common decision basis: {provider_id}")

    def evaluate(self, frames: Mapping[str, SystemOneFrame], *,
                 observed_revision: int, mode: str,
                 selected_provider_id: str | None = None,
                 selection_reason: str = "provider_not_selected",
                 current_decision_revision: int | None = None,
                 total_deadline_ms: int | None = None) -> SemanticDecisionBundle:
        self._validate_common_decision(frames)
        if current_decision_revision is not None and current_decision_revision != observed_revision:
            raise PortfolioDecisionMismatch("portfolio decision revision is stale")
        if selection_reason == "provider_not_selected":
            if selected_provider_id is not None:
                raise ValueError("selection reason conflicts with selected provider")
        else:
            if selected_provider_id not in {item.backend_id for item in self.runtimes}:
                raise ValueError("selected provider is not part of the portfolio")
        question_id = next(iter(frames.values()), None)
        question_id = question_id.question_ids[0] if question_id is not None else ""
        observations: list[ProviderDecision] = []
        evaluation_start = time.perf_counter()
        for runtime in self.runtimes:
            frame = frames.get(runtime.backend_id)
            if frame is None:
                continue
            started = time.perf_counter()
            if total_deadline_ms is not None and (
                    time.perf_counter() - evaluation_start) * 1000.0 >= total_deadline_ms:
                raise TimeoutError("portfolio deadline exceeded")
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
            selection_reason=selection_reason,
            decision_basis_digest=canonical_hash(
                self._common_decision_basis(next(iter(frames.values())))))


__all__ = ["ProviderDecision", "SemanticDecisionBundle", "SystemOnePortfolio"]
