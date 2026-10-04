"""Normalizer for the declared CFW DigitalPsychology component port."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from ...behavior_state import BehaviorSignalBatch, FrameworkSignal
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

    def available_component_ids(self) -> tuple[str, ...]:
        raw = self.component_port.available_component_ids()
        if not isinstance(raw, Sequence) or len(raw) > 16:
            raise ValueError("CFW component capability set exceeds its bounded contract")
        values = tuple(str(item).lower() for item in raw)
        if (len(values) != len(set(values))
                or any(not item or len(item) > 64 for item in values)):
            raise ValueError("CFW returned malformed component capability IDs")
        return values

    def preflight_action(self, action: Mapping[str, Any]) -> Mapping[str, Any]:
        if not isinstance(action, Mapping):
            raise TypeError("CFW action preflight requires the host's original action request")
        result = self.component_port.preflight_action(action)
        return self._validate_action_gate_result(result, final=False)

    def authorize_action(self, action: Mapping[str, Any]) -> Mapping[str, Any]:
        if not isinstance(action, Mapping):
            raise TypeError("CFW final action gate requires the host's original action request")
        result = self.component_port.authorize_action(action)
        return self._validate_action_gate_result(result, final=True)

    def _validate_action_gate_result(self, value: Any, *, final: bool) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise RuntimeError("CFW action gate returned a malformed receipt")
        action_digest = value.get("action_digest")
        decision = value.get("decision")
        if (not isinstance(action_digest, str) or len(action_digest) != 64
                or any(char not in "0123456789abcdef" for char in action_digest)
                or decision not in {
                    "allow", "block", "require_confirmation", "require_reinspection",
                    "require_recovery", "require_changed_hypothesis",
                    "require_goal_reanchor"}):
            raise RuntimeError("CFW action gate receipt is outside its contract")
        receipt = value.get("gate_receipt")
        stage = "final" if final else "preflight"
        identity = self.authority_identity()
        if not isinstance(receipt, Mapping) or receipt.get("stage") != stage:
            raise RuntimeError("CFW action gate omitted its authority receipt")
        if (identity is None
                or receipt.get("authority_id") != identity.get("authority_id")
                or receipt.get("adapter_kind") != identity.get("adapter_kind")
                or receipt.get("adapter_protocol_version") != "1.2.0"
                or receipt.get("action_digest") != action_digest
                or receipt.get("decision") != decision):
            raise RuntimeError("CFW action gate authority receipt is inconsistent")
        reasons = receipt.get("reason_codes")
        if (not isinstance(reasons, (tuple, list)) or not 1 <= len(reasons) <= 8
                or any(not isinstance(item, str) or not item or len(item) > 128
                       for item in reasons)):
            raise RuntimeError("CFW action gate reason codes are malformed")
        result = {"action_digest": action_digest, "decision": str(decision),
                  "gate_receipt": dict(receipt)}
        if final:
            request_id = value.get("action_request_id")
            if not isinstance(request_id, str) or not request_id:
                raise RuntimeError("CFW final gate omitted its action correlation ID")
            result["action_request_id"] = request_id
        return result

    def lifecycle_revision(self) -> int:
        raw = getattr(self.component_port, "lifecycle_revision", None)
        value = raw() if callable(raw) else raw
        if type(value) is not int or value < -1:
            raise RuntimeError("CFW did not expose a valid lifecycle revision")
        return value

    def authority_identity(self) -> Mapping[str, str] | None:
        """Return stable CFW authority identity when the host port provides it."""
        provider = getattr(self.component_port, "authority_identity", None)
        value = provider() if callable(provider) else provider
        if value is None:
            return None
        if (not isinstance(value, Mapping)
                or any(not isinstance(key, str) or not isinstance(item, str)
                       for key, item in value.items())):
            raise RuntimeError("CFW returned a malformed authority identity")
        return dict(value)

    def applied_behavior_projection(self) -> Mapping[str, Any] | None:
        raw = getattr(self.component_port, "applied_behavior_projection", None)
        value = raw() if callable(raw) else raw
        if value is None:
            return None
        if not isinstance(value, Mapping):
            raise RuntimeError("CFW returned a malformed applied behavior projection")
        return dict(value)

    def effective_behavior_projection(self) -> Mapping[str, Any] | None:
        """Return target leases after CFW applies its local expiry clock."""
        raw = getattr(self.component_port, "effective_behavior_projection", None)
        value = raw() if callable(raw) else raw
        if value is None:
            return None
        if not isinstance(value, Mapping):
            raise RuntimeError("CFW returned a malformed effective behavior projection")
        return dict(value)

    def collect_behavior_signals(self, observation: RuntimeObservation
                                 ) -> tuple[FrameworkSignal, ...]:
        return self.peek_behavior_signals(
            after_cursor=None, maximum=32, observation=observation).signals

    def peek_behavior_signals(self, *, after_cursor: int | None,
                              maximum: int = 32,
                              observation: RuntimeObservation | None = None
                              ) -> BehaviorSignalBatch:
        peek = getattr(self.component_port, "peek_behavior_signals", None)
        if not callable(peek):
            raise RuntimeError("CFW port does not expose retryable behavioral signals")
        raw = peek(after_cursor=after_cursor, maximum=maximum)
        if not isinstance(raw, Mapping):
            raise ValueError("CFW signal batch is malformed")
        after = raw.get("after_cursor")
        next_cursor = raw.get("next_cursor")
        values = raw.get("signals")
        if (type(after) is not int or type(next_cursor) is not int
                or not isinstance(values, Sequence) or len(values) > maximum):
            raise ValueError("CFW signal batch cursor or signal sequence is malformed")
        signals = self._normalize_signals(values, observation)
        return BehaviorSignalBatch(after, next_cursor, signals)

    def acknowledge_behavior_signals(self, *, through_cursor: int) -> None:
        acknowledge = getattr(self.component_port, "acknowledge_behavior_signals", None)
        if not callable(acknowledge):
            raise RuntimeError("CFW port does not expose behavioral-signal acknowledgement")
        acknowledge(through_cursor=through_cursor)

    def _normalize_signals(self, raw: Sequence[Any],
                           observation: RuntimeObservation | None = None
                           ) -> tuple[FrameworkSignal, ...]:
        if not isinstance(raw, Sequence) or len(raw) > 32:
            raise ValueError("CFW signal port exceeded its bounded contract")
        goal_set = getattr(observation, "goal_set", None)
        objective_criteria = ({item.criterion_id for item in goal_set.items}
                              if goal_set is not None else set())
        result = []
        for item in raw:
            if not isinstance(item, Mapping):
                raise ValueError("CFW signal port returned a non-object signal")
            source = str(item.get("source") or "cfw")
            raw_code = str(item.get("code") or item.get("signal_type") or "")
            code = _TRIGGER_CODES.get(raw_code, raw_code)
            severity = _SEVERITIES.get(str(item.get("severity") or "medium"))
            if severity is None:
                raise ValueError("CFW signal has an unsupported severity")
            active = item.get("active", True)
            refs = item.get("evidence_refs") or ()
            if not isinstance(active, bool) or not isinstance(refs, (tuple, list)):
                raise ValueError("CFW signal activity or evidence references are malformed")
            criterion_id = item.get("criterion_id")
            subject_ref = item.get("subject_ref")
            if criterion_id is None and subject_ref in objective_criteria:
                criterion_id = subject_ref
            result.append(FrameworkSignal(
                source=source, code=code,
                active=active,
                severity=severity,
                criterion_id=(str(criterion_id) if criterion_id is not None else None),
                evidence_refs=tuple(refs[:8]),
                signal_id=(str(item["signal_id"])
                           if item.get("signal_id") is not None else None),
                cause_id=(str(item["cause_id"])
                          if item.get("cause_id") is not None else None),
                required_action=(str(item["required_action"])
                                 if item.get("required_action") is not None else None),
                scope=(str(item["scope"])
                       if item.get("scope") is not None else None),
                subject_ref=(str(subject_ref) if subject_ref is not None else None),
            ))
        return tuple(result)

    def prepare_behavior_directives(self, directives: Sequence[Mapping[str, Any]]) -> Any:
        return self.component_port.prepare_behavior_directives(tuple(directives))

    def apply_behavior_projection(self, prepared: Any,
                                  projection: Mapping[str, Any]) -> Sequence[str]:
        return self.component_port.apply_behavior_projection(prepared, projection)

    def synchronize_behavior_projection(self, projection: Mapping[str, Any]) -> None:
        self.component_port.synchronize_behavior_projection(projection)

    def rollback_behavior_directives(self, prepared: Any) -> None:
        self.component_port.rollback_behavior_directives(prepared)

    def restore_behavior_projection(self, projection: Mapping[str, Any], *,
                                    expected_current_revision: int) -> None:
        restore = getattr(self.component_port, "restore_behavior_projection", None)
        if not callable(restore):
            raise RuntimeError("CFW adapter cannot restore a behavior projection after restart")
        restore(projection, expected_current_revision=expected_current_revision)

    def expire_local_behavior_leases(self, action_index: int) -> bool:
        expire = getattr(self.component_port, "expire_local_behavior_leases", None)
        return bool(expire(action_index)) if callable(expire) else False

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
