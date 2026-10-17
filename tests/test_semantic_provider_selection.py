from __future__ import annotations

import pytest

from lib.system_one_provider_selection import SemanticProviderPlan, SemanticProviderSelector


def test_no_query_never_selects_a_provider():
    plan = SemanticProviderSelector(default_provider="jev").select(
        query_planned=False, remote_suitable=True)
    assert plan.strategy == "none"
    assert plan.selected_provider_id is None


def test_local_only_data_requires_laya():
    plan = SemanticProviderSelector(default_provider="jev", qualification={
        "jev": "qualified"}).select(query_planned=True, remote_suitable=True,
                                     local_only_data=True)
    assert plan.strategy == "laya"
    assert plan.selected_provider_id == "digitalpsychology-laya-native"


def test_qualified_jev_is_preferred_for_suitable_remote_data():
    plan = SemanticProviderSelector(default_provider="jev", qualification={
        "jev": "qualified"}).select(query_planned=True, remote_suitable=True)
    assert plan.strategy == "jev"
    assert plan.selected_provider_id == "digitalpsychology-jev-openrouter"


def test_unqualified_providers_are_not_consulted():
    plan = SemanticProviderSelector(default_provider="jev", qualification={
        "jev": "unqualified"}).select(query_planned=True, remote_suitable=True)
    assert plan.strategy == "none"
    assert plan.selected_provider_id is None


def test_dual_evaluation_requires_explicit_trial():
    plan = SemanticProviderSelector(default_provider="jev", qualification={
        "jev": "qualified", "laya": "qualified"}).select(
        query_planned=True, remote_suitable=True, explicit_dual_trial=True)
    assert plan.strategy == "dual"
    assert plan.selected_provider_id == "digitalpsychology-jev-openrouter"


def test_invalid_contracts_are_rejected():
    with pytest.raises(ValueError):
        SemanticProviderSelector(default_provider="dual")
    selector = SemanticProviderSelector(qualification={"jev": "qualified"})
    with pytest.raises(ValueError):
        SemanticProviderPlan("not-a-strategy", None, "reason")
    with pytest.raises(ValueError):
        SemanticProviderSelector(qualification={"jev": "maybe"})
