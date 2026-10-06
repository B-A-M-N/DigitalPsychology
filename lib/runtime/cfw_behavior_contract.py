"""Validation for CFW-owned task baseline floors in DP sessions."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping

from ..behavior_state import BehaviorState, FRAMEWORK_MODES
from ..control_protocol import ControlSessionDescriptor
from ..compatibility import CFW_TASK_BEHAVIOR_CONTRACT_VERSION, MAX_AVAILABLE_FRAMEWORKS


_COMPONENTS = frozenset({"owl", "anchor", "fuse", "ward", "flow", "sispis", "dox"})
_HEX_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_SUPPRESSION_MODES = frozenset({"off", "passive"})
_TRANSIENT_OPERATION_MODES = frozenset({"reset", "reopen", "recovery"})
_FIELDS = frozenset({
    "protocol_version", "pipeline_manifest_version", "pipeline_manifest_hash",
    "selected_components", "baseline_floors", "suppression_modes",
    "transient_operation_modes", "contract_hash",
})


def validate_cfw_behavior_contract(value: Any, *,
                                   available_components: tuple[str, ...]
                                   ) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _FIELDS:
        raise ValueError("CFW task behavior contract fields are invalid")
    contract = dict(value)
    if contract["protocol_version"] != CFW_TASK_BEHAVIOR_CONTRACT_VERSION:
        raise ValueError("CFW task behavior contract version is incompatible")
    if (not isinstance(contract["pipeline_manifest_version"], str)
            or not contract["pipeline_manifest_version"]
            or len(contract["pipeline_manifest_version"]) > 64
            or not isinstance(contract["pipeline_manifest_hash"], str)
            or not _HEX_DIGEST.fullmatch(contract["pipeline_manifest_hash"])):
        raise ValueError("CFW pipeline manifest identity is invalid")
    selected = contract["selected_components"]
    if (not isinstance(selected, (list, tuple))
            or len(selected) > MAX_AVAILABLE_FRAMEWORKS
            or any(not isinstance(item, str) or item not in _COMPONENTS
                   for item in selected)
            or len(selected) != len(set(selected))):
        raise ValueError("CFW selected task components are invalid")
    selected = tuple(selected)
    if selected != tuple(sorted(available_components)):
        raise ValueError("CFW task contract differs from the session component set")
    for name in ("baseline_floors", "suppression_modes", "transient_operation_modes"):
        mapping = contract[name]
        if not isinstance(mapping, Mapping) or set(mapping) != set(selected):
            raise ValueError(f"CFW {name} do not cover the selected task components")
    baselines: dict[str, str] = {}
    suppression: dict[str, tuple[str, ...]] = {}
    transient: dict[str, tuple[str, ...]] = {}
    for component in selected:
        baseline = contract["baseline_floors"][component]
        if not isinstance(baseline, str) or baseline not in FRAMEWORK_MODES:
            raise ValueError("CFW task baseline mode is unsupported")
        baselines[component] = baseline
        for source, result, allowed in (
                ("suppression_modes", suppression, _SUPPRESSION_MODES),
                ("transient_operation_modes", transient,
                 _TRANSIENT_OPERATION_MODES)):
            values = contract[source][component]
            if (not isinstance(values, (list, tuple)) or len(values) > 4
                    or any(not isinstance(item, str) or item not in allowed
                           for item in values)
                    or len(values) != len(set(values))):
                raise ValueError(f"CFW component {source} are invalid")
            result[component] = tuple(values)
        if baseline in suppression[component] or baseline in transient[component]:
            raise ValueError("CFW baseline mode conflicts with its mode contract")
        if set(suppression[component]) & set(transient[component]):
            raise ValueError("CFW transient modes overlap suppression modes")
    body = {key: contract[key] for key in _FIELDS if key != "contract_hash"}
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False)
    expected_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    if (not isinstance(contract["contract_hash"], str)
            or contract["contract_hash"] != expected_hash):
        raise ValueError("CFW task behavior contract hash is invalid")
    return {
        **body,
        "selected_components": selected,
        "baseline_floors": baselines,
        "suppression_modes": suppression,
        "transient_operation_modes": transient,
        "contract_hash": expected_hash,
    }


def cfw_behavior_contract(descriptor: ControlSessionDescriptor
                          ) -> dict[str, Any] | None:
    raw = descriptor.host_capabilities.get("cfw_behavior_contract")
    if raw is None:
        return None
    return validate_cfw_behavior_contract(
        raw, available_components=descriptor.available_frameworks)


def initial_behavior_state(descriptor: ControlSessionDescriptor) -> BehaviorState:
    """Seed new DP state with CFW's selected task baselines."""
    contract = cfw_behavior_contract(descriptor)
    if contract is None:
        return BehaviorState()
    return initial_behavior_state_from_contract(contract)


def initial_behavior_state_from_contract(contract: Mapping[str, Any]) -> BehaviorState:
    """Seed a direct RuntimeSession from a validated CFW task contract."""
    state = BehaviorState()
    framework_modes = dict(state.framework_modes)
    framework_modes.update(contract["baseline_floors"])
    return BehaviorState(framework_modes=framework_modes)


def cfw_suppression_modes(descriptor: ControlSessionDescriptor
                          ) -> dict[str, tuple[str, ...]]:
    contract = cfw_behavior_contract(descriptor)
    return dict(contract["suppression_modes"]) if contract is not None else {}


def validate_state_against_cfw_contract(
        state: BehaviorState, descriptor: ControlSessionDescriptor, *,
        label: str = "DP behavior state") -> None:
    contract = cfw_behavior_contract(descriptor)
    if contract is None:
        return
    validate_state_against_behavior_contract(state, contract, label=label)


def validate_state_against_behavior_contract(
        state: BehaviorState, contract: Mapping[str, Any], *,
        label: str = "DP behavior state") -> None:
    """Apply an already validated CFW baseline contract to DP-owned state."""
    for component, prohibited in contract["suppression_modes"].items():
        mode = state.framework_modes.get(component, "off")
        if mode in prohibited:
            raise PermissionError(
                f"{label} lowers the {component.upper()} task baseline floor")
    for intervention in state.active_interventions:
        for target in intervention.targets:
            if not target.target.startswith("framework:"):
                continue
            component = target.target.split(":", 1)[1].lower()
            prohibited = contract["suppression_modes"].get(component, ())
            if (target.applied_value in prohibited
                    or target.previous_value in prohibited):
                raise PermissionError(
                    f"{label} has a target lease below the {component.upper()} task floor")


def validate_directives_against_cfw_contract(
        directives: tuple[Any, ...], descriptor: ControlSessionDescriptor) -> None:
    contract = cfw_behavior_contract(descriptor)
    if contract is None:
        return
    validate_directives_against_behavior_contract(directives, contract)


def validate_directives_against_behavior_contract(
        directives: tuple[Any, ...], contract: Mapping[str, Any]) -> None:
    """Apply an already validated CFW baseline contract to DP directives."""
    for directive in directives:
        if directive.operation != "set_framework":
            continue
        component = directive.target.lower()
        if directive.value in contract["suppression_modes"].get(component, ()):
            raise PermissionError(
                f"DP directive lowers the {component.upper()} task baseline floor")


__all__ = ["cfw_behavior_contract", "cfw_suppression_modes",
           "initial_behavior_state", "initial_behavior_state_from_contract",
           "validate_cfw_behavior_contract",
           "validate_directives_against_cfw_contract",
           "validate_directives_against_behavior_contract",
           "validate_state_against_cfw_contract",
           "validate_state_against_behavior_contract"]
