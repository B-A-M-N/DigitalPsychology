"""Compatibility imports for the neutral runtime's Athena adapter."""
from cognitive_runtime_core.integrations.athena import (
    ATHENA_TASK_PORT_CONTRACT_VERSION,
    AthenaRuntimeAdapter,
    AthenaTaskPort,
    AthenaTaskResultProjection,
)

__all__ = ["ATHENA_TASK_PORT_CONTRACT_VERSION", "AthenaRuntimeAdapter", "AthenaTaskPort",
           "AthenaTaskResultProjection"]
