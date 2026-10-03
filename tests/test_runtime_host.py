from __future__ import annotations

from pathlib import Path
import sys
import json
from dataclasses import replace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.behavior_state import (BehaviorIntervention, BehaviorState,
                                CSWBehaviorSnapshot, GoalItemSnapshot,
                                GoalSetSnapshot, InterventionTarget,
                                StateWorkOption)
from lib.mcp_server import DigitalPsychologyService
from lib.outcomes import HostOutcomeFact
from lib.runtime import (BehaviorStateStore, CFWRuntimeAdapter, RuntimeHost,
                         RuntimeIdentity, RuntimeObservation)
from lib.runtime.adapters import NullCFWComponentPort, NullCSWWorkflowPort
from lib.protocol_artifact import QUESTION_REGISTRY
from lib.system_one_contracts import (ActivityFact, CriterionState, QuestionSpec,
                                      SemanticState, SystemOneFrame,
                                      SystemOneIdentity, canonical_hash)


class _CFWPort:
    def __init__(self):
        self.signals = [{"source": "owl", "code": "owl.approach_failed",
                         "severity": "warning", "evidence_refs": []}]
        self.controls = {}
        self.active_statework_contexts = []

    def available_component_ids(self):
        return ("owl", "anchor", "fuse", "ward", "sispis")

    def collect_behavior_signals(self, _observation):
        return tuple(self.signals)

    def prepare_behavior_directives(self, directives):
        return dict(self.controls), tuple(dict(item) for item in directives)

    def apply_behavior_directives(self, prepared, behavior_state):
        _prior, directives = prepared
        for item in directives:
            self.controls[item["directive_id"]] = item
        self.behavior_state = dict(behavior_state)
        return tuple(item["directive_id"] for item in directives)

    def synchronize_behavior_state(self, behavior_state):
        self.behavior_state = dict(behavior_state)

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
        self.activation_interventions = {}
        self.requested_candidates = []
        self.released = []
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
                           candidate_statework_ids, intervention_id,
                           expires_at_action):
        assert intervention_id
        assert expires_at_action >= 0
        assert statework_id in candidate_statework_ids
        if expected_selection_revision != self.revision:
            raise RuntimeError("stale CSW selection revision")
        self.active = tuple(sorted(set(self.active) | {statework_id}))
        self.activation_interventions[statework_id] = intervention_id
        self.revision += 1
        return self.behavioral_snapshot()

    def deactivate_statework(self, statework_id, *, expected_selection_revision,
                             candidate_statework_ids, intervention_id):
        assert intervention_id
        if expected_selection_revision != self.revision:
            raise RuntimeError("stale CSW selection revision")
        self.released.append(statework_id)
        self.active = tuple(item for item in self.active if item != statework_id)
        self.activation_interventions.pop(statework_id, None)
        self.revision += 1
        return self.behavioral_snapshot()

    def activation_matches(self, statework_id, *, intervention_id):
        return self.activation_interventions.get(statework_id) == intervention_id

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


class _JudgeService(DigitalPsychologyService):
    def __init__(self, *, event_db):
        super().__init__(event_db=event_db)
        self.calls = []

    def judge(self, frame, *, mode=None):
        self.calls.append(("judge", frame))
        return None

    def decide_behavior(self, context, *, judgment=None):
        self.calls.append(("decide", judgment))
        return super().decide_behavior(context, judgment=judgment)


class _FailOnceEventDispatchService(DigitalPsychologyService):
    def __init__(self, *, event_db):
        super().__init__(event_db=event_db)
        self.fail_next_dispatch = True

    def dispatch_runtime_event(self, value):
        if self.fail_next_dispatch:
            self.fail_next_dispatch = False
            raise OSError("simulated event sink interruption")
        return super().dispatch_runtime_event(value)


def _system_one_frame(identity):
    system_identity = SystemOneIdentity(
        namespace_id=identity.namespace_id,
        application_id=identity.application_id,
        application_instance_id=identity.application_instance_id,
        agent_id=identity.agent_id,
        agent_instance_id=identity.agent_instance_id,
        session_id=identity.session_id,
        task_id=identity.task_id,
        attempt_id=identity.attempt_id,
    )
    activity = ActivityFact(
        sequence=1, activity_type="action", source="host_observed",
        host_declared_relation="ambiguous", target_kind="workspace",
        proposed_purpose="advance the requested result",
        affected_criterion_ids=("criterion-visual",),
    )
    state = SemanticState(
        task={"objective": "Render the requested page", "work_mode": "delivery",
              "deliverable_kind": "code_behavior", "task_complexity": "standard",
              "phase": "implementation", "active_segment": identity.segment_id,
              "active_statework": None, "active_flow": None, "non_goals": []},
        situation={"current_state": "IMPLEMENTING", "blockers": [],
                   "actions_since_goal_progress": 0,
                   "actions_since_artifact_change": 0,
                   "verification_actions_since_artifact_change": 0,
                   "recovery_epochs": 0, "repeated_branch_count": 0},
        criteria=(CriterionState("criterion-visual", "Render the requested page",
                                 "functional"),),
        activity=(activity,), workflows=(), convergence=(), candidate=activity,
        legal_routes=("implement", "verify"),
    )
    question = QUESTION_REGISTRY["progress_relation"]
    questions = (QuestionSpec(
        question_id="progress_relation", family=question["family"],
        question_type=question["type"], instructions=question["instructions"],
        criteria=tuple(question["criteria"].items())),)
    state_digest = canonical_hash(state.to_dict())
    question_digest = canonical_hash([item.to_dict() for item in questions])
    policy_hash = "c" * 64
    request_digest = canonical_hash({
        "identity": system_identity.to_dict(), "sequence": 1,
        "state_digest": state_digest,
        "question_bundle_digest": question_digest,
        "policy_hash": policy_hash,
    })
    return SystemOneFrame(
        identity=system_identity, request_id="host-frame-1", sequence=1,
        state_digest=state_digest, question_bundle_digest=question_digest,
        request_digest=request_digest, policy_hash=policy_hash,
        backend_id="host-test", model_id="none", model_revision="none",
        model_content_hash="a" * 64, provider_protocol_hash="b" * 64,
        calibration_bundle_hash="", state=state, questions=questions,
    )


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
    assert second.state.active_stateworks == ()
    assert "Required work:" in context and "CFW context" in context
    assert "cognitive_mode" not in context
    assert "DIGITAL PSYCHOLOGY BEHAVIOR STATE" not in context
    assert "csw_eligible_steps" not in context
    assert "csw_workflow_states" not in context
    assert ("repair",) in csw.requested_candidates
    assert all(candidates in {(), ("repair",)}
               for candidates in csw.requested_candidates)
    assert cfw_port.active_statework_contexts[-1] == ()


def test_runtime_host_supports_every_optional_system_combination(tmp_path):
    for index, (with_cfw, with_csw) in enumerate((
            (False, False), (True, False), (False, True), (True, True))):
        service = DigitalPsychologyService(event_db=tmp_path / f"events-{index}.sqlite3")
        identity = replace(_identity(), task_id=f"task-combo-{index}")
        raw_cfw = _CFWPort() if with_cfw else None
        cfw = CFWRuntimeAdapter(raw_cfw) if raw_cfw is not None else None
        csw = _CSWPort() if with_csw else None
        evidence = _EvidenceAuthority({})
        try:
            host = RuntimeHost(
                service=service, state_path=tmp_path / f"state-{index}.sqlite3")
            session = host.open_session(
                identity=identity, objective="Render the requested page",
                objective_criteria=("criterion-visual",), cfw=cfw, csw=csw,
                outcome_evidence_authority=evidence)

            result = session.process_action(RuntimeObservation(
                action_index=1, equivalent_retry_count=2,
                criterion_status={"criterion-visual": "unmet"}))
            context = session.before_model_call(tool_capable=True)

            assert result.state.revision == 1
            assert isinstance(session.cfw, NullCFWComponentPort) is (not with_cfw)
            assert isinstance(session.csw, NullCSWWorkflowPort) is (not with_csw)
            assert result.state.active_stateworks == result.csw_snapshot.active_stateworks
            if with_csw:
                assert result.csw_snapshot.active_stateworks == ("repair",)
            else:
                assert result.csw_snapshot.active_stateworks == ()
                assert "csw\"" not in context
            if with_cfw:
                assert raw_cfw.controls
                assert "cfw\"" not in context
            else:
                assert not any(item.operation == "set_framework"
                               for item in result.decision.directives)
                assert "cfw\"" not in context

            assert host.open_session(
                identity=identity, objective="Render the requested page",
                objective_criteria=("criterion-visual",), cfw=cfw, csw=csw,
                outcome_evidence_authority=evidence) is session
        finally:
            service.close()


def test_runtime_host_keeps_twelve_item_task_set_complete_and_rotates_attention(tmp_path):
    identity = _identity()
    service = DigitalPsychologyService(event_db=tmp_path / "events.sqlite3")
    criteria = tuple(f"criterion-{index}" for index in range(1, 13))
    goal_set = GoalSetSnapshot(revision=7, items=tuple(
        GoalItemSnapshot(
            criterion_id=criterion_id,
            priority=100 if index == 1 else 0,
            status="READY",
            equivalent_retry_count=2 if index == 1 else 0,
        ) for index, criterion_id in enumerate(criteria, start=1)))
    try:
        host = RuntimeHost(service=service,
                           state_path=tmp_path / "behavior-state.sqlite3")
        cfw_port = _CFWPort()
        cfw_port.signals = []
        session = host.open_session(
            identity=identity, objective="Complete all twelve requested items",
            objective_criteria=criteria,
            cfw=CFWRuntimeAdapter(cfw_port), csw=_CSWPort(),
            outcome_evidence_authority=_EvidenceAuthority({}),
        )

        observed: set[str] = set()
        focus_after_first_lease = ()
        for action_index in range(1, 13):
            result = session.process_action(RuntimeObservation(
                action_index=action_index,
                goal_set=goal_set,
                # These task-global values must not trigger a retry/recovery
                # against unrelated criteria.
                actions_since_goal_progress=999,
                equivalent_retry_count=999,
            ))
            assert result.state.cognitive_mode != "BLOCKED"
            assert len(result.state.focus_criteria) <= 3
            observed.update(result.state.focus_criteria)
            if action_index == 1:
                assert result.state.cognitive_mode == "RECOVER"
                assert "criterion-1" in result.state.focus_criteria
            if action_index == 4:
                focus_after_first_lease = result.state.focus_criteria
                projection = result.behavior_context
                assert "0 of 12 criteria verified" in projection
                assert "objective_criteria" not in projection

        assert observed == set(criteria)
        assert "criterion-1" not in focus_after_first_lease
        context = session.before_model_call()
        assert "Current focus" in context
        assert all(item in context for item in result.state.focus_criteria)
        assert session.project_behavior_context().count("criterion-") < 16
    finally:
        service.close()


def test_runtime_host_releases_statework_when_its_dp_lease_closes(tmp_path):
    identity = _identity()
    service = DigitalPsychologyService(event_db=tmp_path / "events.sqlite3")
    cfw_port = _CFWPort()
    cfw_port.signals = []
    csw = _CSWPort()
    csw.active = ("repair",)
    csw.options = ()
    store = BehaviorStateStore(tmp_path / "behavior-state.sqlite3")
    intervention = BehaviorIntervention(
        intervention_id="lease-repair-1", cause="equivalent_retry",
        expected_effect="Use the recovery StateWork for one bounded attempt.",
        start_revision=0, scope=identity.scope, expires_after_actions=2,
        actions_elapsed=0,
        targets=(InterventionTarget("statework:repair", "unselected", "preferred"),),
        target_criteria=("criterion-visual",),
        baseline_criterion_status={"criterion-visual": "unmet"},
        baseline_action_index=0, evaluation_horizon_actions=2)
    initial = BehaviorState(
        revision=1, active_stateworks=("repair",),
        active_interventions=(intervention,))
    store.save(
        identity.scope, expected_revision=0, expected_action_index=-1,
        action_index=0, state=initial)
    try:
        host = RuntimeHost(service=service, state_path=tmp_path / "behavior-state.sqlite3")
        session = host.open_session(
            identity=identity, objective="Render the requested page",
            objective_criteria=("criterion-visual",),
            cfw=CFWRuntimeAdapter(cfw_port), csw=csw,
            outcome_evidence_authority=_EvidenceAuthority({}),
        )

        turn = session.process_action(RuntimeObservation(action_index=1))

        assert csw.released == ["repair"]
        assert not turn.state.active_stateworks
        assert "lease-repair-1" not in {
            item.intervention_id for item in turn.state.active_interventions}
        release_ids = {directive.directive_id for directive in turn.decision.directives
                       if directive.operation == "release_statework"}
        assert release_ids and release_ids.issubset(set(turn.applied_directive_ids))
    finally:
        service.close()


def test_runtime_event_outbox_recovers_after_host_restart(tmp_path):
    identity = _identity()
    state_path = tmp_path / "behavior-state.sqlite3"
    event_path = tmp_path / "events.sqlite3"
    failing_service = _FailOnceEventDispatchService(event_db=event_path)
    csw = _CSWPort()
    cfw_port = _CFWPort()

    host = RuntimeHost(service=failing_service, state_path=state_path)
    first_session = host.open_session(
        identity=identity, objective="Render the requested page",
        objective_criteria=("criterion-visual",),
        cfw=CFWRuntimeAdapter(cfw_port), csw=csw,
        outcome_evidence_authority=_EvidenceAuthority({}),
    )
    first = first_session.process_action(RuntimeObservation(
        action_index=1, equivalent_retry_count=2,
        criterion_status={"criterion-visual": "unmet"},
    ))
    assert first.application_event_ids
    assert first.pending_event_ids == first.application_event_ids
    persisted = BehaviorStateStore(state_path).pending_outbox(identity.scope)
    assert tuple(item.event_id for item in persisted) == first.application_event_ids
    failing_service.close()

    resumed_service = DigitalPsychologyService(event_db=event_path)
    try:
        resumed_host = RuntimeHost(service=resumed_service, state_path=state_path)
        resumed_session = resumed_host.open_session(
            identity=identity, objective="Render the requested page",
            objective_criteria=("criterion-visual",),
            cfw=CFWRuntimeAdapter(_CFWPort()), csw=csw,
            outcome_evidence_authority=_EvidenceAuthority({}),
        )
        resumed_session.process_action(RuntimeObservation(
            action_index=2,
            criterion_status={"criterion-visual": "unmet"},
        ))

        delivered = [json.loads(row[0]) for row in resumed_service._sink._conn.execute(
            "SELECT payload FROM events WHERE event_id=?",
            (first.application_event_ids[0],)).fetchall()]
        assert len(delivered) == 1
        assert delivered[0]["event_type"] == "behavior_intervention_applied"
        assert not BehaviorStateStore(state_path).pending_outbox(identity.scope)
    finally:
        resumed_service.close()


def test_runtime_host_dispatches_validated_system_one_frame_before_fast_loop(tmp_path):
    identity = _identity()
    frame = _system_one_frame(identity)
    service = _JudgeService(event_db=tmp_path / "events.sqlite3")
    try:
        host = RuntimeHost(
            service=service,
            state_path=tmp_path / "behavior-state.sqlite3",
            system_one_frame_factory=lambda *_context: frame,
        )
        session = host.open_session(
            identity=identity, objective="Render the requested page",
            objective_criteria=("criterion-visual",),
            cfw=CFWRuntimeAdapter(_CFWPort()), csw=_CSWPort(),
            outcome_evidence_authority=_EvidenceAuthority({}),
            # A semantic provider requires the host to declare task semantics.
            policy_context={"deliverable_kind": "visual_ui"},
        )
        session.process_action(RuntimeObservation(action_index=1))
        assert service.calls == [("judge", frame), ("decide", None)]
    finally:
        service.close()
