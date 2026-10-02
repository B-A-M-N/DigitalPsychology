"""P2: the provider interface is provider-neutral; legacy names still import.

`SemanticProvider` names the interface, `LayaProvider` names the implementation,
and every pre-existing `SystemOne*` spelling keeps working so hosts are not
forced to migrate. No provider name may leak into a model-facing surface.
"""
from __future__ import annotations

import lib.system_one_backends as backends
import lib.system_one_contracts as contracts


def test_provider_interface_and_implementation_use_neutral_names() -> None:
    assert backends.SemanticProvider.__name__ == "SemanticProvider"
    assert backends.LayaProvider.__name__ == "LayaProvider"
    assert contracts.SemanticProvider.__name__ == "SemanticProvider"


def test_legacy_system_one_names_remain_importable_aliases() -> None:
    assert backends.SystemOneBackend is backends.SemanticProvider
    assert backends.LayaSystemOneBackend is backends.LayaProvider
    assert contracts.BehavioralJudge is contracts.SemanticProvider


def test_provider_neutral_names_are_exported() -> None:
    for name in ("SemanticProvider", "LayaProvider"):
        assert name in backends.__all__
    for name in ("SystemOneBackend", "LayaSystemOneBackend"):
        assert name in backends.__all__
    assert "SemanticProvider" in contracts.__all__
    assert "BehavioralJudge" in contracts.__all__


def test_disabled_and_loopback_providers_satisfy_the_same_interface() -> None:
    for implementation in (backends.DisabledSystemOneBackend,
                           backends.StatelessLoopbackHTTPBackend):
        for method in ("capability", "judge", "reset_identity", "resync"):
            assert callable(getattr(implementation, method))


def test_provider_interface_is_not_a_model_facing_tool() -> None:
    # The judge contract stays host-internal; its name must not be advertised.
    for tool_name in ("semantic_provider", "judge", "system_one", "laya"):
        assert tool_name not in backends.__all__
