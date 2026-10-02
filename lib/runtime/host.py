"""DigitalPsychology runtime host construction and session factory."""
from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from threading import RLock
from typing import Any, Sequence

from ..feedback_loop import trusted_state_root
from .contracts import (BehavioralDecisionService, CFWComponentPort,
                        CSWWorkflowPort, RuntimeIdentity,
                        SystemOneFrameFactory)
from .adapters.standalone import NullCFWComponentPort, NullCSWWorkflowPort
from .session import RuntimeSession
from .store import BehaviorStateStore


class RuntimeHost:
    """Own shared DP services and open isolated host sessions on demand."""

    SESSION_CACHE_LIMIT = 256

    def __init__(self, *, service: BehavioralDecisionService,
                 state_path: Path | None = None,
                 system_one_frame_factory: SystemOneFrameFactory | None = None) -> None:
        self.service = service
        self.system_one_frame_factory = system_one_frame_factory
        if state_path is None:
            state_path = trusted_state_root() / "behavior-state.sqlite3"
        self.state_store = BehaviorStateStore(Path(state_path))
        self._sessions: OrderedDict[str, RuntimeSession] = OrderedDict()
        self._lock = RLock()

    def open_session(self, *, identity: RuntimeIdentity,
                     objective_criteria: Sequence[str],
                     cfw: CFWComponentPort | None = None,
                     csw: CSWWorkflowPort | None = None,
                     outcome_evidence_authority: Any,
                     objective: str = "") -> RuntimeSession:
        with self._lock:
            return self._open_session_locked(
                identity=identity, objective_criteria=objective_criteria,
                cfw=cfw, csw=csw,
                outcome_evidence_authority=outcome_evidence_authority,
                objective=objective)

    def _open_session_locked(self, *, identity: RuntimeIdentity,
                             objective_criteria: Sequence[str],
                             cfw: CFWComponentPort,
                             csw: CSWWorkflowPort,
                             outcome_evidence_authority: Any,
                             objective: str) -> RuntimeSession:
        existing = self._sessions.get(identity.scope)
        if existing is not None:
            same_cfw = (existing.cfw is cfw if cfw is not None else
                        isinstance(existing.cfw, NullCFWComponentPort))
            same_csw = (existing.csw is csw if csw is not None else
                        isinstance(existing.csw, NullCSWWorkflowPort))
            if (existing.identity != identity or not same_cfw or not same_csw
                    or existing.outcome_evidence_authority is not outcome_evidence_authority
                    or existing.objective_criteria != tuple(objective_criteria)
                    or existing.objective != objective):
                raise ValueError("runtime scope is already bound to different host adapters")
            self._sessions.move_to_end(identity.scope)
            return existing
        cfw_port = cfw if cfw is not None else NullCFWComponentPort()
        csw_port = csw if csw is not None else NullCSWWorkflowPort()
        session = RuntimeSession(
            identity=identity, objective_criteria=objective_criteria,
            objective=objective, controller=self.service, cfw=cfw_port, csw=csw_port,
            outcome_evidence_authority=outcome_evidence_authority,
            state_store=self.state_store,
            system_one_frame_factory=self.system_one_frame_factory,
        )
        self._sessions[identity.scope] = session
        if len(self._sessions) > self.SESSION_CACHE_LIMIT:
            self._sessions.popitem(last=False)
        return session

__all__ = ["RuntimeHost"]
