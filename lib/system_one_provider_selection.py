"""Conservative provider strategy and source selection for DP's semantic gate.

The policy is host-owned and qualification-first. It never turns provider
confidence into authorization and never averages independent probabilities.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class SemanticProviderPlan:
    strategy: str
    selected_provider_id: str | None
    reason: str

    def __post_init__(self) -> None:
        if self.strategy not in {"none", "jev", "laya", "dual"}:
            raise ValueError("unsupported semantic provider strategy")
        if self.strategy == "none" and self.selected_provider_id is not None:
            raise ValueError("no-inference strategy must not select a provider")
        if len(self.reason) > 256 or not self.reason:
            raise ValueError("semantic provider selection reason must be bounded non-empty text")
        if self.strategy == "jev" and self.selected_provider_id not in {
                "digitalpsychology-jev-openrouter", None}:
            raise ValueError("Jev strategy selected an unsupported provider")
        if self.strategy == "laya" and self.selected_provider_id not in {
                "digitalpsychology-laya-native", None}:
            raise ValueError("Laya strategy selected an unsupported provider")


class SemanticProviderSelector:
    """Select at most one advisory provider unless dual evaluation is explicitly justified."""

    def __init__(self, *, default_provider: str = "laya",
                 qualification: Mapping[str, str] | None = None) -> None:
        if default_provider not in {"jev", "laya"}:
            raise ValueError("default provider must be Jev or Laya")
        qualification = dict(qualification or {})
        if any(value not in {"qualified", "unqualified"} for value in qualification.values()):
            raise ValueError("provider qualification must be qualified or unqualified")
        self.default_provider = default_provider
        self._qualification = qualification

    def select(self, *, query_planned: bool, remote_suitable: bool,
               local_only_data: bool = False,
               explicit_dual_trial: bool = False,
               laya_available: bool = True,
               jev_available: bool = True,
               authorized_providers: frozenset[str] | set[str] | None = None
               ) -> SemanticProviderPlan:
        """Select only available, privacy-eligible, and qualified providers."""
        authorized = ({"jev", "laya"} if authorized_providers is None
                      else set(authorized_providers))
        if not query_planned:
            return SemanticProviderPlan("none", None,
                                        "deterministic evidence resolves this boundary")
        if local_only_data:
            remote_suitable = False
        laya_usable = (laya_available and "laya" in authorized
                       and self._qualified("laya"))
        jev_usable = (jev_available and remote_suitable and "jev" in authorized
                      and self._qualified("jev"))
        if local_only_data:
            if laya_usable:
                return SemanticProviderPlan("laya", "digitalpsychology-laya-native",
                                            "local-only data requires the local provider")
            return SemanticProviderPlan("none", None,
                                        "local-only data has no available local provider")
        if explicit_dual_trial and laya_usable and jev_usable:
            return SemanticProviderPlan("dual", self._default_id(),
                                        "operator-authorized dual qualification trial")
        if explicit_dual_trial:
            reason = ("dual trial is missing an independently qualified or available "
                      "provider")
            return SemanticProviderPlan("none", None, reason)
        if self.default_provider == "jev" and jev_usable:
            return SemanticProviderPlan("jev", "digitalpsychology-jev-openrouter",
                                        "bounded remote question with suitable public data")
        if laya_usable:
            return SemanticProviderPlan("laya", "digitalpsychology-laya-native",
                                        "locally qualified provider is preferred")
        if jev_usable:
            return SemanticProviderPlan("jev", "digitalpsychology-jev-openrouter",
                                        "remotely qualified provider is preferred")
        return SemanticProviderPlan("none", None,
                                    "no provider is available, qualified, and privacy-eligible")

    def _default_id(self) -> str:
        return ("digitalpsychology-jev-openrouter" if self.default_provider == "jev"
                else "digitalpsychology-laya-native")

    def _qualified(self, provider: str) -> bool:
        return self._qualification.get(provider) == "qualified"


__all__ = ["SemanticProviderPlan", "SemanticProviderSelector"]
