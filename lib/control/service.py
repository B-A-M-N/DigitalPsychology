"""Stable facade for host-owned DigitalPsychology control operations.

Proposal production runs through an optional host-registered sink. The host
registers a ``submit`` callable (the CSW proposal-inbox submit in production,
a fake in tests) plus a subject-context builder, and the service drains each
completed semantic judgment exactly once per ``request_id``:

- Default off: with no registered sink no proposal path runs and service
  responses are untouched.
- Idempotency: a bounded seen-request ledger (``PROPOSAL_SEEN_REQUEST_BOUND``
  entries, oldest evicted first) suppresses re-submission of an already
  drained ``request_id``. The ledger is a process-local secondary guard; the
  primary duplicate guard in production is inbox content-idempotency (each
  payload carries ``body.provider_decision_id`` = the judgment request id).
- Visibility: a failed ``submit`` is reported as an ``ok: False`` receipt on
  the service result path (``proposal_receipts``), never swallowed. A retry
  replays the retained judgment; already-accepted proposals are deduped by the
  inbox, never re-attested here.
- Malformed judgments raise ``ValueError`` on the direct drain entry and are
  reported as failed receipts on the automatic prepare path; either way the
  sink is never invoked with them.
"""
from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping
from typing import Any, Callable

from ..runtime.store import BehaviorStateStore
from .engine import ControlDecisionEngine
from .proposal_watcher import drain_judgment
from .session_registry import SessionRegistryMethods
from .transaction_service import ControlTransactionMethods
from ..telemetry.trial_report import TrialReportMethods
from ..telemetry.production_metrics import (ProductionMetrics,
                                            default_production_metrics_path)

PROPOSAL_SEEN_REQUEST_BOUND = 512

PROPOSAL_FAILED_KIND = "unknown"


class _JudgmentCapturingDecisionService:
    """Transparent decision-service delegate that records completed judgments.

    The engine consumes the semantic judgment internally and only reports
    flags outward, so the service observes completions at this boundary: any
    ``judge`` result (or selected portfolio result) with ``status == "ok"``
    and a non-``None`` judgment is a completed semantic judgment. Everything
    else — attribute access, ``decide_behavior``, failures — delegates
    untouched, so engine behavior is unchanged.
    """

    def __init__(self, inner: Any, record: Callable[[Any], None]) -> None:
        object.__setattr__(self, "_inner", inner)
        object.__setattr__(self, "_record", record)

    def __getattr__(self, name: str) -> Any:
        target = getattr(object.__getattribute__(self, "_inner"), name)
        if name in ("judge", "judge_portfolio") and callable(target):
            record = object.__getattribute__(self, "_record")
            portfolio = name == "judge_portfolio"

            def _wrapped(*args: Any, **kwargs: Any) -> Any:
                outcome = target(*args, **kwargs)
                if portfolio:
                    selected = getattr(outcome, "selected_result", None)
                    if selected is not None:
                        record(selected)
                else:
                    record(outcome)
                return outcome

            return _wrapped
        return target


def _proposal_request_id(judgment: Any) -> str:
    request_id = getattr(judgment, "request_id", None)
    if (not isinstance(request_id, str) or not request_id
            or len(request_id) > 256):
        raise ValueError(
            "proposal drain needs a judgment with a bounded request_id")
    return request_id


class ControlPlaneService(SessionRegistryMethods, ControlTransactionMethods,
                          TrialReportMethods):
    """Coordinate the focused session, transaction, and reporting services."""

    def __init__(self, *, decision_service: Any, state_store: BehaviorStateStore,
                 system_one_frame_factory: Any = None,
                 production_metrics: ProductionMetrics | None = None) -> None:
        self.decision_service = decision_service
        self.state_store = state_store
        if production_metrics is None:
            path = default_production_metrics_path(
                fallback=self.state_store.path.with_name("production-metrics.sqlite3"))
            production_metrics = ProductionMetrics(path)
        self.production_metrics = production_metrics
        if system_one_frame_factory is None:
            factory_provider = getattr(
                decision_service, "control_system_one_frame_factory", None)
            if callable(factory_provider):
                system_one_frame_factory = factory_provider()
        self.engine = ControlDecisionEngine(
            decision_service=_JudgmentCapturingDecisionService(
                decision_service, self._record_completed_judgment),
            system_one_frame_factory=system_one_frame_factory)
        self._proposal_submit: Callable[..., Any] | None = None
        self._proposal_subject_context: Callable[[Any], Mapping[str, Any]] | None = None
        self._proposal_seen: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._captured_judgments: list[Any] = []

    def close(self) -> None:
        close = getattr(self.decision_service, "close", None)
        if callable(close):
            close()

    def production_metrics_snapshot(self) -> dict[str, Any]:
        """Return bounded host/operator production counters."""
        return self.production_metrics.snapshot()

    def register_proposal_sink(self, *, submit: Callable[[Mapping[str, Any]], str],
                               subject_context: Callable[[Any], Mapping[str, Any]]) -> None:
        """Register the host-owned proposal sink (default: none, path off).

        ``submit`` receives one inbox-shaped payload per emitted proposal and
        returns the inbox proposal id; ``subject_context`` maps a completed
        judgment to a mapping with ``subject_ref`` (plus optional
        ``statework_id`` / ``basis_revision``). Re-registering replaces the
        sink without clearing the seen-request ledger, so replay protection
        survives sink rotation.
        """
        if not callable(submit):
            raise ValueError("proposal sink needs a submit callable")
        if not callable(subject_context):
            raise ValueError("proposal sink needs a subject context builder")
        self._proposal_submit = submit
        self._proposal_subject_context = subject_context

    def clear_proposal_sink(self) -> None:
        """Unregister the proposal sink; the proposal path goes quiet again."""
        self._proposal_submit = None
        self._proposal_subject_context = None

    @property
    def proposal_sink_registered(self) -> bool:
        """Whether a host proposal sink is currently registered."""
        return (self._proposal_submit is not None
                and self._proposal_subject_context is not None)

    def _record_completed_judgment(self, result: Any) -> None:
        try:
            judgment = getattr(result, "judgment", None)
            status = getattr(result, "status", None)
        except Exception:
            return
        if status != "ok" or judgment is None:
            return
        try:
            _proposal_request_id(judgment)
        except ValueError:
            return
        self._captured_judgments.append(judgment)

    def _remember_proposals(self, request_id: str, judgment: Any,
                            receipts: list[dict[str, Any]]) -> None:
        self._proposal_seen[request_id] = {
            "judgment": judgment,
            "receipts": [dict(item) for item in receipts],
        }
        self._proposal_seen.move_to_end(request_id)
        while len(self._proposal_seen) > PROPOSAL_SEEN_REQUEST_BOUND:
            self._proposal_seen.popitem(last=False)

    def _subject_kwargs(self, judgment: Any) -> dict[str, Any]:
        assert self._proposal_subject_context is not None
        context = self._proposal_subject_context(judgment)
        if not isinstance(context, Mapping):
            raise ValueError(
                "proposal subject context must return a mapping")
        subject_ref = context.get("subject_ref")
        if (not isinstance(subject_ref, str) or not subject_ref.strip()
                or len(subject_ref) > 256):
            raise ValueError(
                "proposal subject context must supply a bounded subject_ref")
        kwargs: dict[str, Any] = {"subject_ref": subject_ref}
        if context.get("statework_id") is not None:
            kwargs["statework_id"] = context["statework_id"]
        if context.get("basis_revision") is not None:
            kwargs["basis_revision"] = context["basis_revision"]
        return kwargs

    def drain_completed_judgment(self, judgment: Any) -> list[dict[str, Any]]:
        """Drain one completed semantic judgment into the registered sink.

        Returns one receipt per emitted proposal (submitted ones carry the
        inbox proposal id; failed ones carry ``ok: False`` plus the error).
        A replayed ``request_id`` whose first drain fully succeeded returns
        the stored receipts without touching the sink; a replay after any
        failure replays the same judgment. With no registered sink this is a
        no-op returning ``[]``. Malformed judgments raise ``ValueError``
        before the sink is ever invoked.
        """
        if not self.proposal_sink_registered:
            return []
        assert self._proposal_submit is not None
        request_id = _proposal_request_id(judgment)
        cached = self._proposal_seen.get(request_id)
        if cached is not None:
            self._proposal_seen.move_to_end(request_id)
            if all(item.get("ok") for item in cached["receipts"]):
                return [dict(item) for item in cached["receipts"]]
        try:
            receipts = drain_judgment(judgment, submit=self._proposal_submit,
                                      **self._subject_kwargs(judgment))
        except ValueError:
            raise
        except Exception as exc:
            # drain_judgment converts submit failures to receipts, so any
            # other escape is a malformed judgment, never a sink outcome.
            # The emitter raises before the first submit, so the sink was
            # not invoked on this path either.
            raise ValueError(
                f"proposal drain needs a well-formed judgment: {exc}") from exc
        self._remember_proposals(request_id, judgment, receipts)
        return receipts

    def retry_proposal_drain(self, request_id: str) -> list[dict[str, Any]]:
        """Replay the retained judgment for ``request_id`` (recover a failure).

        Returns the stored receipts untouched when the first drain fully
        succeeded; otherwise replays the same judgment through the sink and
        stores the fresh receipts. Unknown ids raise ``ValueError``; with no
        registered sink this is a no-op returning ``[]``.
        """
        if not self.proposal_sink_registered:
            return []
        if not isinstance(request_id, str) or not request_id:
            raise ValueError("proposal retry needs a request_id")
        cached = self._proposal_seen.get(request_id)
        if cached is None:
            raise ValueError("proposal retry knows no such request_id")
        return self.drain_completed_judgment(cached["judgment"])

    def prepare_control_tick(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        """Prepare a tick, then drain any completed semantic judgment once.

        Proposal receipts ride on the result as ``proposal_receipts``; a
        drain problem here is reported as a failed receipt, never raised, so
        the proposal path cannot break the control path. With no registered
        sink the prepared response is returned untouched.
        """
        self._captured_judgments = []
        try:
            response = super().prepare_control_tick(raw)
        except Exception:
            self._captured_judgments = []
            raise
        captured, self._captured_judgments = self._captured_judgments, []
        if not self.proposal_sink_registered:
            return response
        receipts: list[dict[str, Any]] = []
        for judgment in captured:
            try:
                receipts.extend(self.drain_completed_judgment(judgment))
            except ValueError as exc:
                receipts.append({"ok": False, "kind": PROPOSAL_FAILED_KIND,
                                 "error": f"{type(exc).__name__}: {exc}"[:256]})
        response["proposal_receipts"] = receipts
        return response


__all__ = ["ControlPlaneService", "PROPOSAL_SEEN_REQUEST_BOUND"]
