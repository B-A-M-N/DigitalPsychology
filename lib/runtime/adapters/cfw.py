"""Normalizer for the declared CFW DigitalPsychology component port."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from ...behavior_state import FrameworkSignal
from ..contracts import RuntimeObservation


_TRIGGER_CODES = {
    "owl.user_observation_conflict": "operator_contradiction",
    "owl.approach_failed": "equivalent_retry",
    "fuse.retry_bound_exceeded": "equivalent_retry",
    "owl.constraint_drift": "goal_drift",
}
_SEVERITIES = {"low": "info", "medium": "warning", "high": "critical",
               "info": "info", "warning": "warning", "critical": "critical"}


class CFWRuntimeAdapter:
    """Normalize CFW signals and directive receipts for the DP event loop."""

    def __init__(self, component_port: Any) -> None:
        self.component_port = component_port

    def available_statework_ids(self) -> tuple[str, ...]:
        raw = self.component_port.available_statework_ids()
        if not isinstance(raw, Sequence) or len(raw) > 32:
            raise ValueError("CFW StateWork availability exceeds its bounded contract")
        values = tuple(raw)
        if (len(values) != len(set(values))
                or any(not isinstance(item, str) or not item or len(item) > 128
                       for item in values)):
            raise ValueError("CFW returned malformed available StateWork IDs")
        return values

    def collect_behavior_signals(self, observation: RuntimeObservation
                                 ) -> tuple[FrameworkSignal, ...]:
        raw = self.component_port.collect_behavior_signals(observation)
        if not isinstance(raw, Sequence) or len(raw) > 32:
            raise ValueError("CFW signal port exceeded its bounded contract")
        result = []
        for item in raw:
            if not isinstance(item, Mapping):
                raise ValueError("CFW signal port returned a non-object signal")
            source = str(item.get("source") or "cfw")
            raw_code = str(item.get("code") or "")
            code = _TRIGGER_CODES.get(raw_code, raw_code)
            severity = _SEVERITIES.get(str(item.get("severity") or "medium"))
            if severity is None:
                raise ValueError("CFW signal has an unsupported severity")
            active = item.get("active", True)
            refs = item.get("evidence_refs") or ()
            if not isinstance(active, bool) or not isinstance(refs, (tuple, list)):
                raise ValueError("CFW signal activity or evidence references are malformed")
            result.append(FrameworkSignal(
                source=source, code=code,
                active=active,
                severity=severity,
                criterion_id=(str(item["criterion_id"])
                              if item.get("criterion_id") is not None else None),
                evidence_refs=tuple(refs[:8]),
            ))
        return tuple(result)

    def prepare_behavior_directives(self, directives: Sequence[Mapping[str, Any]]) -> Any:
        return self.component_port.prepare_behavior_directives(tuple(directives))

    def apply_behavior_directives(self, prepared: Any) -> Sequence[str]:
        return self.component_port.apply_behavior_directives(prepared)

    def rollback_behavior_directives(self, prepared: Any) -> None:
        self.component_port.rollback_behavior_directives(prepared)

    def render_behavior_context(self, context: str, *,
                                active_statework_ids: Sequence[str] = ()) -> str:
        return self.component_port.render_behavior_context(
            context, active_statework_ids=tuple(active_statework_ids))

    def before_model_call(self, context: str, *, tool_capable: bool = False,
                          action_hint: Mapping[str, Any] | None = None,
                          active_statework_ids: Sequence[str] = ()) -> str:
        return self.component_port.before_model_call(
            context, tool_capable=tool_capable, action_hint=action_hint,
            active_statework_ids=tuple(active_statework_ids))

    def before_tool_planning(self, context: str,
                             action_hint: Mapping[str, Any] | None = None,
                             active_statework_ids: Sequence[str] = ()) -> str:
        return self.component_port.before_tool_planning(
            context, action_hint=action_hint,
            active_statework_ids=tuple(active_statework_ids))


__all__ = ["CFWRuntimeAdapter"]
