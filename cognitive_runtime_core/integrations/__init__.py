"""Optional product and harness integrations for the neutral runtime.

Importing this namespace does not load any integration. Applications can
depend on the neutral contracts and select only the adapters they compose.
The lazy exports preserve the original convenience API for application ports.
"""
from __future__ import annotations

from importlib import import_module
from typing import Any


_EXPORTS = {
    "ATHENA_TASK_PORT_CONTRACT_VERSION": (".athena", "ATHENA_TASK_PORT_CONTRACT_VERSION"),
    "AthenaRuntimeAdapter": (".athena", "AthenaRuntimeAdapter"),
    "AthenaTaskPort": (".athena", "AthenaTaskPort"),
    "AthenaTaskResultProjection": (".athena", "AthenaTaskResultProjection"),
    "DigitalPsychologyBridgeHost": (".cfw", "DigitalPsychologyBridgeHost"),
    "DigitalPsychologyHarnessLifecycle": (".cfw", "DigitalPsychologyHarnessLifecycle"),
    "ActuationFailure": (".triad", "ActuationFailure"),
    "ControlActuator": (".triad", "ControlActuator"),
    "ControlApplication": (".triad", "ControlApplication"),
    "ControlReconciliation": (".triad", "ControlReconciliation"),
    "expected_csw_active_stateworks": (".triad", "expected_csw_active_stateworks"),
    "REFERENCE_ADAPTER_VERSION": (".reference", "REFERENCE_ADAPTER_VERSION"),
    "ReferenceHarnessAdapter": (".reference", "ReferenceHarnessAdapter"),
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    try:
        module_name, attribute = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    value = getattr(import_module(module_name, __name__), attribute)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
