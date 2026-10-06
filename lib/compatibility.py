"""Single source for DigitalPsychology's cross-runtime versions and limits."""
from __future__ import annotations

from types import MappingProxyType
from typing import Any, Mapping


DP_CONTROL_PROTOCOL_VERSION = "1.9.0"
CFW_ADAPTER_PROTOCOL_VERSION = "1.2.0"
CFW_BEHAVIOR_PROJECTION_VERSION = "2.1.0"
CFW_RUNTIME_EVENT_SCHEMA_VERSION = "1.0.0"
CFW_TASK_BEHAVIOR_CONTRACT_VERSION = "1.0.0"
CSW_ADAPTER_PROTOCOL_VERSION = "1.2.0"
CSW_WORKFLOW_PORT_VERSION = "1.4.0"

COMPATIBILITY = MappingProxyType({
    "dp_control": DP_CONTROL_PROTOCOL_VERSION,
    "cfw_adapter": CFW_ADAPTER_PROTOCOL_VERSION,
    "cfw_projection": CFW_BEHAVIOR_PROJECTION_VERSION,
    "cfw_runtime_event": CFW_RUNTIME_EVENT_SCHEMA_VERSION,
    "cfw_task_behavior_contract": CFW_TASK_BEHAVIOR_CONTRACT_VERSION,
    "csw_adapter": CSW_ADAPTER_PROTOCOL_VERSION,
    "csw_workflow_port": CSW_WORKFLOW_PORT_VERSION,
})

MAX_CONTROL_PAYLOAD_BYTES = 131_072
MAX_ACTIVE_STATEWORKS = 16
MAX_ELIGIBLE_STATEWORKS = 32
MAX_GOAL_CRITERIA = 256
MAX_CFW_SIGNALS = 32
MAX_AVAILABLE_FRAMEWORKS = 16
MAX_CONTROL_BATCH_ITEMS = 32
MAX_OUTCOME_FACTS = 128

CONTROL_LIMITS = MappingProxyType({
    "max_objective_criteria": MAX_GOAL_CRITERIA,
    "max_cfw_signals": MAX_CFW_SIGNALS,
    "max_active_stateworks": MAX_ACTIVE_STATEWORKS,
    "max_eligible_stateworks": MAX_ELIGIBLE_STATEWORKS,
    "max_available_frameworks": MAX_AVAILABLE_FRAMEWORKS,
    "max_control_payload_bytes": MAX_CONTROL_PAYLOAD_BYTES,
})


def advertised_control_capabilities() -> dict[str, Any]:
    """Return the exact DP feature, compatibility, and count contract."""
    return {
        "prepare_commit": True,
        "abort": True,
        "reconcile": True,
        **dict(CONTROL_LIMITS),
        "protocol_versions": dict(COMPATIBILITY),
    }


def validate_control_capabilities(value: Mapping[str, Any]) -> None:
    """Fail closed when a host and DP server do not share one contract."""
    if not isinstance(value, Mapping):
        raise ValueError("DP control capabilities are missing")
    expected = advertised_control_capabilities()
    if value.get("protocol_versions") != expected["protocol_versions"]:
        raise ValueError("DP control protocol compatibility negotiation failed")
    for name, limit in CONTROL_LIMITS.items():
        offered = value.get(name)
        if type(offered) is not int or offered != limit:
            raise ValueError(f"DP control capability {name} is incompatible")
    for feature in ("prepare_commit", "abort", "reconcile"):
        if value.get(feature) is not True:
            raise ValueError(f"DP control feature {feature} is unavailable")
