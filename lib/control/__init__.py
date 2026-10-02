"""DigitalPsychology's out-of-process control plane."""

__all__ = ["ControlDecisionEngine", "ControlPlaneService"]

def __getattr__(name: str):
    # Keep focused submodule imports (for example policy_selector) free of an
    # eager engine/service import cycle through fast_loop.
    if name == "ControlDecisionEngine":
        from .engine import ControlDecisionEngine
        return ControlDecisionEngine
    if name == "ControlPlaneService":
        from .service import ControlPlaneService
        return ControlPlaneService
    raise AttributeError(name)
