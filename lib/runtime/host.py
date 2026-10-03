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

    def __init__(self, *, service: BehavioralDecisionService | None = None,
                 state_path: Path | None = None,
                 system_one_frame_factory: SystemOneFrameFactory | None = None,
                 session_cache_limit: int = SESSION_CACHE_LIMIT) -> None:
        if type(session_cache_limit) is not int or not 1 <= session_cache_limit <= 4096:
            raise ValueError("runtime session cache limit must be between 1 and 4096")
        self.service = service
        self.system_one_frame_factory = system_one_frame_factory
        self.session_cache_limit = session_cache_limit
        if state_path is None:
            state_path = trusted_state_root() / "behavior-state.sqlite3"
        self.state_store = (BehaviorStateStore(Path(state_path))
                            if service is not None else None)
        self._sessions: OrderedDict[str, RuntimeSession] = OrderedDict()
        self._lock = RLock()

    def open_session(self, *, identity: RuntimeIdentity,
                     objective_criteria: Sequence[str],
                     cfw: CFWComponentPort | None = None,
                     csw: CSWWorkflowPort | None = None,
                     outcome_evidence_authority: Any,
                     objective: str = "",
                     policy_context: dict[str, Any] | None = None) -> RuntimeSession:
        with self._lock:
            if self.service is None or self.state_store is None:
                raise RuntimeError(
                    "in-process sessions require a DP decision service; "
                    "use open_control_bridge for host-only MCP control")
            return self._open_session_locked(
                identity=identity, objective_criteria=objective_criteria,
                cfw=cfw, csw=csw,
                outcome_evidence_authority=outcome_evidence_authority,
                objective=objective, policy_context=policy_context)

    def _open_session_locked(self, *, identity: RuntimeIdentity,
                             objective_criteria: Sequence[str],
                             cfw: CFWComponentPort,
                             csw: CSWWorkflowPort,
                             outcome_evidence_authority: Any,
                             objective: str,
                             policy_context: dict[str, Any] | None = None) -> RuntimeSession:
        existing = self._sessions.get(identity.scope)
        if existing is not None:
            cfw_port = cfw if cfw is not None else NullCFWComponentPort()
            csw_port = csw if csw is not None else NullCSWWorkflowPort()
            same_cfw = self._same_authority(existing.cfw, cfw_port)
            same_csw = self._same_authority(existing.csw, csw_port)
            same_evidence = self._same_authority(
                existing.outcome_evidence_authority, outcome_evidence_authority)
            if (existing.identity != identity or not same_cfw or not same_csw
                    or not same_evidence
                    or existing.objective_criteria != tuple(objective_criteria)
                    or existing.objective != objective):
                raise ValueError("runtime scope is already bound to different host adapters")
            existing.cfw = cfw_port
            existing.csw = csw_port
            existing.outcome_evidence_authority = outcome_evidence_authority
            self._sessions.move_to_end(identity.scope)
            return existing
        cfw_port = cfw if cfw is not None else NullCFWComponentPort()
        csw_port = csw if csw is not None else NullCSWWorkflowPort()
        # Check contract versions before the first behavioral decision, so an
        # incompatible framework or workflow engine is refused at session
        # startup rather than discovered mid-intervention.
        self._check_contract_versions(cfw_port, csw_port)
        session = RuntimeSession(
            identity=identity, objective_criteria=objective_criteria,
            objective=objective, controller=self.service, cfw=cfw_port, csw=csw_port,
            outcome_evidence_authority=outcome_evidence_authority,
            state_store=self.state_store,
            system_one_frame_factory=self.system_one_frame_factory,
            policy_context=policy_context,
        )
        self._sessions[identity.scope] = session
        if len(self._sessions) > self.session_cache_limit:
            _scope, evicted = next(iter(self._sessions.items()))
            evicted.close()
            self._sessions.pop(_scope)
        return session

    @staticmethod
    def _check_contract_versions(cfw: Any, csw: Any) -> None:
        """Fail closed when an adapter does not declare a compatible contract."""
        from .cfw_projection import CFW_BEHAVIOR_PROJECTION_VERSION
        from .adapters.csw import CSW_WORKFLOW_PORT_VERSION

        def _declared(adapter: Any, field: str) -> str | None:
            provider = getattr(adapter, "authority_identity", None)
            identity = provider() if callable(provider) else provider
            if hasattr(identity, "get"):
                value = identity.get(field)
                return str(value) if value else None
            value = getattr(adapter, field, None)
            return str(value) if value else None

        cfw_version = _declared(cfw, "behavior_projection_version")
        if cfw_version is not None and cfw_version != CFW_BEHAVIOR_PROJECTION_VERSION:
            raise ValueError(
                f"CFW behavior projection version {cfw_version!r} is incompatible "
                f"with DP's {CFW_BEHAVIOR_PROJECTION_VERSION!r}")
        csw_version = _declared(csw, "workflow_port_version")
        if csw_version is not None and csw_version != CSW_WORKFLOW_PORT_VERSION:
            raise ValueError(
                f"CSW workflow port version {csw_version!r} is incompatible "
                f"with DP's {CSW_WORKFLOW_PORT_VERSION!r}")

    @staticmethod
    def _same_authority(left: Any, right: Any) -> bool:
        if left is right:
            return True
        left_identity = RuntimeHost._authority_identity_for(left)
        right_identity = RuntimeHost._authority_identity_for(right)
        return (left_identity is not None and right_identity is not None
                and left_identity == right_identity)

    @staticmethod
    def _authority_identity_for(adapter: Any) -> str | None:
        provider = getattr(adapter, "authority_identity", None)
        identity = provider() if callable(provider) else provider
        if not isinstance(identity, dict) and not hasattr(identity, "items"):
            return None
        import json
        try:
            return json.dumps(dict(identity), sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError):
            raise ValueError("runtime adapter authority identity is not serializable")

    def close(self) -> None:
        """Flush and close all cached in-process sessions."""
        with self._lock:
            sessions = tuple(self._sessions.values())
            for session in sessions:
                session.close()
            self._sessions.clear()

    async def open_control_bridge(self, *, identity: RuntimeIdentity,
                                  objective: str,
                                  objective_criteria: Sequence[str],
                                  initial_goal_set: Any,
                                  cfw: Any = None, csw: Any = None,
                                  client: Any = None,
                                  csw_authority_identity: Any = None,
                                  outcome_evidence_connected: bool = False,
                                  fault_handler: Any = None):
        """Open a host-driven session whose decisions cross the private DP MCP."""
        from ..host.bridge import HostControlBridge
        bridge = HostControlBridge(
            identity=identity,
            objective=objective,
            objective_criteria=objective_criteria,
            initial_goal_set=initial_goal_set,
            cfw=cfw,
            csw=csw,
            client=client,
            csw_authority_identity=csw_authority_identity,
            outcome_evidence_connected=outcome_evidence_connected,
            fault_handler=fault_handler,
        )
        await bridge.open()
        return bridge

__all__ = ["RuntimeHost"]
