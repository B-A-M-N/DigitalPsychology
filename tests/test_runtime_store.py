from __future__ import annotations

from lib.behavior_state import BehaviorIntervention, BehaviorState, InterventionTarget
from lib.runtime.store import BehaviorStateStore


def test_application_receipts_remain_bounded_to_active_interventions(tmp_path):
    store = BehaviorStateStore(tmp_path / "behavior.sqlite3")
    scope = "tenant/app/agent/session/task/attempt"
    record = store.load(scope)

    for index in range(1001):
        intervention = BehaviorIntervention(
            intervention_id=f"intervention-{index}", cause="equivalent_retry",
            expected_effect="Try a materially different approach.",
            start_revision=record.state.revision + 1, scope=scope,
            expires_after_actions=1, actions_elapsed=0,
            targets=(InterventionTarget("mode", "DELIVER", "RECOVER"),),
            target_criteria=("criterion-1",),
            baseline_criterion_status={"criterion-1": "unmet"},
            baseline_action_index=index, evaluation_horizon_actions=1,
            condition_id=f"condition-{index}",
            condition_basis_digest=f"basis-{index}",
        )
        state = BehaviorState(
            revision=record.state.revision + 1,
            cognitive_mode="RECOVER",
            active_interventions=(intervention,),
        )
        action_index = record.action_index + 1
        store.save(
            scope, expected_revision=record.state.revision,
            expected_action_index=record.action_index,
            action_index=action_index, state=state,
            application_receipts={intervention.intervention_id: (f"directive-{index}",)},
        )
        record = store.load(scope)
        assert record.application_receipts == {
            intervention.intervention_id: (f"directive-{index}",)}

    with store._connect() as database:
        count, = database.execute(
            "SELECT COUNT(*) FROM intervention_receipts WHERE scope=?", (scope,)
        ).fetchone()
    assert count == 1
