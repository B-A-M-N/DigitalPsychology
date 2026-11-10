"""Tests for the contrastive fixture generator.

The load-bearing properties are (a) DECOUPLING -- under a rotated assignment the
same held evidence must yield a different gold, so a model matching route names
rather than obligation semantics scores wrong by construction -- and (b)
DECIDABILITY -- the set must be mostly decidable, or there is nothing to measure.
"""
from __future__ import annotations

import json

import pytest

from qualification.contrastive_fixtures import (
    _DEFAULT_CAPABILITIES,
    EVIDENCE_KINDS,
    TASK_INSTANCES,
    _assign_obligations,
    _derive_gold,
    _held_patterns,
    coverage_report,
    default_partitions,
    distinct_rotations,
    export_jsonl,
    generate_cases,
    partition_by_instance,
    validate,
)


def test_generated_set_is_valid():
    cases = generate_cases()
    assert cases
    assert validate(cases) == ()


def test_gold_is_never_authored_only_derived():
    """Every gold must be reproducible from (eligible, held, obligations)."""
    for case in generate_cases():
        derived, _ = _derive_gold(case.eligible, case.held_evidence,
                                  case.obligations)
        assert derived == case.gold, case.case_id


def test_gold_always_eligible():
    for case in generate_cases():
        if case.gold is not None:
            assert case.gold in case.eligible


@pytest.mark.parametrize("capability", ["planning_change", "mutating_change",
                                        "verifying_change", "recovering_change"])
def test_rotation_actually_moves_obligations(capability):
    """If obligations do not move, the 'decoupled' claim is vacuous."""
    rotations = range(distinct_rotations(capability))
    assignments = [_assign_obligations(capability, p) for p in rotations]
    first_route = next(iter(_DEFAULT_CAPABILITIES[capability]))
    variations = {a[first_route] for a in assignments}
    assert len(variations) > 1, (
        f"{capability}: {first_route}'s obligation never moves -- decoupling "
        "is fake")


def test_identity_rotation_is_refused():
    """Rotating by the family size must raise, not silently no-op.

    Silently returning the canonical assignment would put an IDENTICAL case in
    train and holdout, leaking the answer.
    """
    with pytest.raises(ValueError, match="identity"):
        _assign_obligations("mutating_change", 2)  # 2-route family


def test_held_evidence_is_permutation_invariant():
    """The same evidence pattern must appear under every permutation.

    If evidence slid along with the obligation, gold would never move and the
    decoupling metric would read zero while looking healthy.
    """
    patterns = {p: set(_held_patterns("planning_change"))
                for p in (0, 1)}
    assert patterns[0] == patterns[1]
    assert patterns[0]  # non-empty


def test_set_is_mostly_decidable():
    """Too many ambiguous cases leaves nothing to measure.

    The first fixture set was 72% ambiguous and the probe could not distinguish
    anything. This is the regression guard for that.
    """
    report = coverage_report(generate_cases())
    assert report["decidable_fraction"] >= 0.70, (
        f"only {report['decidable_fraction']} decidable; the coupled/decoupled "
        "comparison would have no footing")


def test_set_is_genuinely_name_decoupled():
    """A name-matching model must be able to fail on a real fraction of cases."""
    report = coverage_report(generate_cases())
    assert report["name_decoupled_fraction"] >= 0.50, (
        f"only {report['name_decoupled_fraction']} decoupled; a name-matching "
        "model would pass this set")


def test_same_evidence_different_gold_under_rotation():
    """The concrete demonstration: identical facts, different correct answer."""
    for capability, held in (("planning_change", ("intent", "risk")),
                             ("mutating_change", ("authority", "rollback"))):
        golds = {}
        rotations = range(distinct_rotations(capability))
        for permutation in rotations:
            obligations = _assign_obligations(capability, permutation)
            gold, _ = _derive_gold(tuple(obligations), held, obligations)
            golds[permutation] = gold
        assert len(set(golds.values())) > 1, (
            f"{capability}: gold did not move under rotation: {golds}")


def test_families_only_list_routes_that_service_the_trigger():
    """Listing all three routes everywhere would invent obligations.

    A family must name only the bundles whose transitions.yaml offers that
    trigger; otherwise we are training on a contract that does not exist.
    """
    for case in generate_cases():
        assert set(case.eligible) == set(case.obligations), (
            f"{case.case_id}: eligible routes and obligations disagree")


def test_gold_distribution_is_not_collapsed():
    """A collapsed gold set lets a constant predictor score well."""
    report = coverage_report(generate_cases())
    counts = report["gold_distribution"]
    # The dominant route must not be most of the decidable cases.
    assert counts and max(counts.values()) / sum(counts.values()) < 0.6, (
        f"golds concentrated in one route: {counts}")


def test_ambiguous_cases_are_explicit_not_forced():
    """Where the contract does not decide, gold must be None with a reason."""
    cases = generate_cases()
    ambiguous = [c for c in cases if c.gold is None]
    assert ambiguous, "a set with no ambiguous cases teaches guessing"
    for case in ambiguous:
        assert case.ambiguity_reason


def test_invalidating_state_is_never_given_a_gold():
    """All three bundles require the same evidence for `invalidate`.

    The contract genuinely cannot prefer one, so any gold here would be invented.
    """
    cases = [c for c in generate_cases() if c.capability == "invalidating_state"]
    assert cases, "invalidating_state coverage was lost"
    assert all(c.gold is None for c in cases)


def test_no_satisfiable_obligation_is_ambiguous():
    """Holding none of the required evidence cannot yield a confident gold."""
    obligations = {"getter": ("intent", "risk"), "infrae": ("discovery",)}
    gold, reason = _derive_gold(("getter", "infrae"), (), obligations)
    assert gold is None
    assert reason == "no_eligible_obligation_satisfiable"


def test_multiple_satisfiable_is_ambiguous():
    obligations = {"getter": ("intent",), "infrae": ("risk",)}
    gold, reason = _derive_gold(("getter", "infrae"), ("intent", "risk"),
                                obligations)
    assert gold is None
    assert reason.startswith("multiple_eligible")


def test_held_patterns_rank_decidable_first():
    """The cap must not be what determines the decidable share."""
    patterns = _held_patterns("planning_change")
    obligations = _assign_obligations("planning_change", 0)
    first_none = next((i for i, p in enumerate(patterns)
                       if _derive_gold(tuple(obligations), p, obligations)[0] is None),
                      len(patterns))
    assert first_none > len(patterns) // 2


def test_export_round_trips():
    cases = generate_cases()[:5]
    lines = export_jsonl(cases).strip().split("\n")
    assert len(lines) == len(cases)
    for line in lines:
        record = json.loads(line)
        assert "gold" in record and "obligations" in record
        assert record["permutation"] in (0, 1, 2)


def test_obligations_only_name_known_evidence_kinds():
    known = set(EVIDENCE_KINDS)
    for case in generate_cases():
        for obligation in case.obligations.values():
            assert set(obligation) <= known, case.case_id


def test_objective_names_the_needed_trigger():
    """The task text must say what is needed, without naming the answer."""
    for case in generate_cases():
        assert case.objective.strip()
        # The objective must not simply be the gold route's name.
        assert case.gold is None or case.gold not in case.objective.lower()


# --- corpus size and partition independence (the 74-case limitation) ---------


def test_corpus_is_large_enough_to_train_on():
    """74 cases could not support fine-tuning a 421M-parameter model.

    This guards the expansion so the count cannot silently regress.
    """
    cases = generate_cases()
    assert len(cases) >= 1000, (
        f"only {len(cases)} cases; too small to fine-tune or calibrate on")


def test_every_class_is_represented():
    """gitter was the under-selected class; it needs real coverage too."""
    report = coverage_report(generate_cases())
    golds = report["gold_distribution"]
    for route in ("getter", "gitter", "infrae"):
        assert golds.get(route, 0) >= 100, (
            f"{route} has only {golds.get(route, 0)} cases; per-class recall "
            "cannot be trusted on fewer")
    assert sum(golds.values()) >= 1000


def test_calibration_set_is_large_enough():
    """Three calibration cases cannot calibrate confidence."""
    partitions = default_partitions(generate_cases())
    calib = partitions["calibration"]
    assert len(calib) >= 200, f"calibration has only {len(calib)} cases"
    # Calibration needs every class, or the temperature fit is dominated by one.
    golds = {c.gold for c in calib if c.gold is not None}
    assert golds == {"getter", "gitter", "infrae"}, (
        f"calibration lacks classes: {golds}")


def test_holdout_is_large_enough_for_per_class_recall():
    partitions = default_partitions(generate_cases())
    holdout = partitions["holdout"]
    assert len(holdout) >= 200, f"holdout has only {len(holdout)} cases"
    for route in ("getter", "gitter", "infrae"):
        count = sum(1 for c in holdout if c.gold == route)
        assert count >= 30, (
            f"holdout has {count} {route} cases; per-class recall would be "
            "measured on a handful of examples")


def test_partitions_share_no_task_instance():
    """The independence property the older split did not have.

    Case ids are unique by construction, but two cases about the same referent
    share wording and referent. Holding out one while training on the other is a
    leak that a unique case_id cannot detect -- so assert on the instance.
    """
    partitions = default_partitions(generate_cases())
    train = {c.task_instance for c in partitions["train"]}
    calib = {c.task_instance for c in partitions["calibration"]}
    holdout = {c.task_instance for c in partitions["holdout"]}
    assert not (train & calib), "task instance appears in train and calibration"
    assert not (train & holdout), "task instance appears in train and holdout"
    assert not (calib & holdout), "task instance appears in calib and holdout"


def test_partitions_cover_every_task_instance():
    partitions = default_partitions(generate_cases())
    assigned = {c.task_instance
                for part in partitions.values() for c in part}
    assert assigned == set(TASK_INSTANCES), (
        f"instances unassigned: {sorted(set(TASK_INSTANCES) - assigned)}")


def test_every_partition_covers_every_capability():
    """A partition missing a family cannot measure that family at all."""
    partitions = default_partitions(generate_cases())
    expected = set(_DEFAULT_CAPABILITIES_LIKE())
    for name, cases in partitions.items():
        seen = {c.capability for c in cases}
        missing = expected - seen
        assert not missing, f"{name} lacks capabilities: {sorted(missing)}"


def _DEFAULT_CAPABILITIES_LIKE():
    from qualification.contrastive_fixtures import _DEFAULT_CAPABILITIES
    return _DEFAULT_CAPABILITIES


def test_holdout_is_adversarial_to_name_matching():
    """Holdout gold must still contradict the canonical names.

    Instance grouping must not cost the adversarial property that made the
    holdout meaningful in the first place.
    """
    partitions = default_partitions(generate_cases())
    contradictions = 0
    for case in partitions["holdout"]:
        canonical = _assign_obligations(case.capability, 0)
        if (case.gold is not None and case.permutation
                and canonical.get(case.gold) != case.obligations.get(case.gold)):
            contradictions += 1
    assert contradictions > 0, (
        "no holdout case has gold that contradicts the canonical assignment")


def test_objective_mentions_its_task_instance():
    """Distinct surface forms are part of what makes cases non-independent."""
    cases = generate_cases()
    for case in cases:
        assert case.task_instance in case.objective, case.case_id


def test_partition_by_instance_respects_explicit_lists():
    cases = generate_cases()
    hold = TASK_INSTANCES[5:7]
    calib = TASK_INSTANCES[7:9]
    partitions = partition_by_instance(cases, holdout_instances=hold,
                                       calib_instances=calib)
    assert {c.task_instance for c in partitions["holdout"]} == set(hold)
    assert {c.task_instance for c in partitions["calibration"]} == set(calib)
    for case in partitions["train"]:
        assert case.task_instance not in set(hold) | set(calib)


def test_explicit_partition_without_holdout_instances_raises():
    cases = generate_cases()
    try:
        partition_by_instance(cases)
    except ValueError:
        return
    raise AssertionError("an empty holdout must raise, not silently pass")
