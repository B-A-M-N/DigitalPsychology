from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lib.behavior_state import (BehaviorIntervention, BehaviorState,
                                InterventionTarget)
from lib.lease_window import lease_window
from lib.runtime.cfw_projection import build_cfw_behavior_projection


@pytest.mark.parametrize(
    ("boundary", "duration", "effective_from", "expires_at"),
    [
        ("PRE_ACTION", 1, 10, 10),
        ("POST_ACTION", 1, 11, 11),
        ("PRE_ACTION", 3, 10, 12),
        ("POST_ACTION", 3, 11, 13),
    ],
)
def test_one_absolute_lease_window_drives_the_cfw_projection(
        boundary, duration, effective_from, expires_at):
    window = lease_window(boundary, 10, duration)
    assert (window.effective_from_action, window.expires_at_action) == (
        effective_from, expires_at)

    intervention = BehaviorIntervention(
        intervention_id="lease-window-test", cause="test lease",
        expected_effect="Keep CFW on the shared absolute lease window.",
        start_revision=1, scope="test-scope", expires_after_actions=duration,
        actions_elapsed=0,
        targets=(InterventionTarget("mode", "DELIVER", "VERIFY"),),
        baseline_action_index=window.effective_from_action,
        expires_at_action=window.expires_at_action,
    )
    projection = build_cfw_behavior_projection(
        BehaviorState(revision=1, cognitive_mode="VERIFY",
                      active_interventions=(intervention,)),
        host_action_index=window.effective_from_action,
    )
    assert projection.target_leases[0].expires_at_action == expires_at
