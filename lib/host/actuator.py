"""Backward-compatible import shim for the in-DP runtime actuator."""
from ..runtime.actuator import ActuationFailure, ControlActuator, ControlApplication

__all__ = ["ActuationFailure", "ControlActuator", "ControlApplication"]
