from __future__ import annotations

from pathlib import Path
import sys
import json

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.behavior_state import CSWBehaviorSnapshot, StateWorkOption
from lib.mcp_server import DigitalPsychologyService
from lib.outcomes import HostOutcomeFact
from lib.runtime import (BehaviorStateStore, CFWRuntimeAdapter, RuntimeHost,
                         RuntimeIdentity, RuntimeObservation)


class _CFWPort:
    def __init__(self):
        self.signals = [{"source": "owl", "code": "owl.approach_failed",
                         "severity": "warning", "evidence_refs": []}]
        self.controls = {}
        self.active_statework_contexts = []

    def available_statework_ids(self):
        return ("repair",)

    def collect_behavior_signals(self, _observation):
        return tuple(self.signals)

    def prepare_behavior_directives(self, directives):
        return dict(self.controls), tuple(dict(item) for item in directives)

    def apply_behavior_directives(self, prepared):
        _prior, directives = prepared
        for item in directives:
            self.controls[item["directive_id"]] = item
        return tuple(item["directive_id"] for item in directives)

    def rollback_behavior_directives(self, prepared):
        prior, _directives = prepared
        self.controls = dict(prior)

    def render_behavior_context(self, context, *, active_statework_ids=()):
        return context

    def before_model_call(self, context, *, tool_capable=False, action_hint=None,
                          active_statework_ids=()):
        self.active_statework_contexts.append(tuple(active_statework_ids))
        return f"CFW context\n{context}"

    def before_tool_planning(self, context, *, action_hint=None, active_statework_ids=()):
        return f"CFW planning context\n{context}"


class _CSWPort:
    def __init__(self):
        self.revision = 0
        self.active = ()
        self.requested_candidates = []
        self.options = (StateWorkOption(
            "repair", behavior_tags=("recovery",),
            purpose="Rebuild a failed approach from evidence."),)

    def behavioral_snapshot(self, *, candidate_statework_ids=None):
        self.requested_candidates.append(tuple(candidate_statework_ids or ()))
        return CSWBehaviorSnapshot(
            revision=self.revision,
            active_stateworks=self.active,
            eligible_stateworks=tuple(item for item in self.options
                                      if item.statework_id not in self.active),
            workflow_states={item: "UNKNOWN" for item in self.active},
            snapshot_id=("a" * 64),
            workflow_revisions={item: 0 for item in self.active},
            eligible_steps={item: ("inspect",) for item in self.active},
        )

    def activate_statework(self, statework_id, *, expected_selection_revision,
                           candidate_statework_ids):
        assert statework_id in candidate_statework_ids
        if expected_selection_revision != self.revision:
            raise RuntimeError("stale CSW selection revision")
        self.active = tuple(sorted(set(self.active) | {statework_id}))
        self.revision += 1
        return self.behavioral_snapshot()

    def deactivate_statework(self, statework_id, *, expected_selection_revision,
                             candidate_statework_ids):
        if expected_selection_revision != self.revision:
            raise RuntimeError("stale CSW selection revision")
        self.active = tuple(item for item in self.active if item != statework_id)
        self.revision += 1
        return self.behavioral_snapshot()

    def apply_step(self, statework_id, step_id, *, evidence_refs,
                   expected_workflow_revision):
        if statework_id not in self.active or step_id != "inspect":
            raise ValueError("illegal CSW step")
        return {"accepted": True, "revision": expected_workflow_revision + 1}


class _EvidenceAuthority:
    def __init__(self, facts):
        self.facts = facts

    def resolve_outcome_fact(self, evidence_ref, *, scope):
        fact = self.facts[evidence_ref]
        if fact.scope != scope:
            raise ValueError("evidence scope mismatch")
        return fact


def _identity():
    return RuntimeIdentity(
        namespace_id="tenant-a", application_id="assistant",
        application_instance_id="assistant-1", agent_instance_id="agent-1",
        session_id="session-1", task_id="task-1", attempt_id="attempt-1",
        workflow_id="workflow-1", subject_ref="repo:example", segment_id="segment-0",
        agent_id="agent-1", behavioral_subject="task-1", goal_contract_hash="a" * 64,
    )


def test_runtime_host_applies_cfw_controls_and_credits_host_verified_progress(tmp_path):
    identity = _identity()
    service = DigitalPsychologyService(event_db=tmp_path / "events.sqlite3")
    cfw_port = _CFWPort()
    csw = _CSWPort()
    facts = {}
    try:
        host = RuntimeHost(service=service,
                           state_path=tmp_path / "behavior-state.sqlite3")
        session = host.open_session(
            identity=identity, objective="Render the requested page",
            objective_criteria=("criterion-visual",),
            cfw=CFWRuntimeAdapter(cfw_port), csw=csw,
            outcome_evidence_authority=_EvidenceAuthority(facts),
        )
        first = session.process_action(RuntimeObservation(
            action_index=10, equivalent_retry_count=2,
            criterion_status={"criterion-visual": "unmet"},
        ))

        assert first.state.cognitive_mode == "RECOVER"
        assert csw.active == ("repair",)
        assert first.csw_snapshot.active_stateworks == ("repair",)
        assert first.application_event_ids
        assert cfw_port.controls
        intervention = first.state.active_interventions[0]
        facts["verified"] = HostOutcomeFact(
            "verified", session.scope, "host_validator", "criterion_verified",
            observed_revision=1, criterion_id="criterion-visual", action_index=11)
        cfw_port.signals = []
        second = session.process_action(RuntimeObservation(
            action_index=11, criterion_status={"criterion-visual": "verified"},
            evidence_refs=("verified",), progress_made=True, artifact_changed=True,
        ))
        context = session.before_model_call(tool_capable=True)
        stored = BehaviorStateStore(tmp_path / "behavior-state.sqlite3").load(session.scope)
        events = [json.loads(row[0]) for row in service._sink._conn.execute(
            "SELECT payload FROM events ORDER BY row_id").fetchall()]
    finally:
        service.close()

    assessed = [event for event in events
                if event["event_type"] == "behavior_intervention_assessed"]
    assert len(assessed) == 1
    assert assessed[0]["payload"]["behavior_intervention_id"] == intervention.intervention_id
    assert assessed[0]["payload"]["behavior_intervention_status"] == "effective"
    assert assessed[0]["payload"]["behavior_intervention_attribution"] == "supported"
    assert assessed[0]["evidence_refs"] == ["verified"]
    assert second.state.intervention_history[-1].outcome == "effective"
    assert stored.action_index == 11
    assert stored.state.revision == 2
    assert "cognitive_mode" in context and "CFW context" in context
    assert "DIGITAL PSYCHOLOGY BEHAVIOR STATE" in context
    assert ("repair",) in csw.requested_candidates
    assert all(candidates in {(), ("repair",)}
               for candidates in csw.requested_candidates)
    assert cfw_port.active_statework_contexts[-1] == ("repair",)
