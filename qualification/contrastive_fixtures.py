"""Contract-grounded contrastive fixtures with DECOUPLED names and obligations.

Why this exists: the Phase 1 permutation probe showed the model associates the
route NAME, not the obligation semantics, because in every earlier fixture the
two co-vary. Training on correlated fixtures teaches the correlation and eval
certifies it.

Design: gold is derived from the CONTRACT, never from the name.

Each capability names a **trigger** that more than one StateWork can service, so
the contracts themselves supply a real conflict. The authoritative CSW
transitions.yaml contains several such families, e.g. `mutate` is offered by both
`getter` (PLANNED->CHANGING, requires [authority, rollback]) and `infrae`
(PLANNED->CHANGING, requires [change_gate, authority]). The task says which
trigger is needed and which evidence the host holds; gold is then the UNIQUE
StateWork whose obligation for that trigger the host can satisfy. Where none or
several qualify, gold is None (explicit no-preference) and the case is labelled
ambiguous -- never forced.

Decoupling is structural: the obligation list is ROTATED among the family's
routes, so the same obligation attaches to a different name. Gold follows the
obligation because gold is derived from feasibility, not identity -- so a
name-matching model scores wrong on a known fraction of cases by construction,
and the generator reports that fraction.
"""
from __future__ import annotations

import itertools
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

# ------------------------------------------------------------------ contracts
# Trigger-confusable families. Each entry maps StateWork -> the required
# evidence of ITS transition for that trigger, read from the authoritative
# transitions.yaml:
#
#   planning_change    getter OBSERVED->PLANNED on plan    [intent, risk]
#                      infrae ASSESSED->PLANNED on design  [plan, invariants, rollback]
#   mutating_change    getter PLANNED->CHANGING on mutate  [authority, rollback]
#                      infrae PLANNED->CHANGING on mutate  [change_gate, authority]
#   observing_state    gitter UNKNOWN->OBSERVED on observe [repository_truth]
#                      infrae UNKNOWN->OBSERVED on discover [discovery]
#                      getter RECOVERING->OBSERVED on observe [repository_evidence]
#   verifying_change   getter CHANGING->VERIFIED on validate [repository_evidence]
#                      infrae CHANGING->VERIFIED on validate [effective_state, regression_check]
#   stabilizing_change getter VERIFIED->STABLE on stabilize   [repository_evidence]
#                      infrae VERIFIED->STABLE on stabilize [verified_invariants, operational_health]
#   recovering_change  getter CHANGING->RECOVERING on recover  [rollback_plan]
#                      infrae CHANGING->RECOVERING on recover  [recovery_plan]
#   invalidating_state getter VERIFIED->RECOVERING on invalidate [invalidation]
#                      gitter OBSERVED->STALE      on invalidate [invalidation]
#                      infrae VERIFIED->DEGRADED   on invalidate [invalidation]
#   observing_repository gitter UNKNOWN->OBSERVED on observe [repository_truth]
#                         getter RECOVERING->OBSERVED on observe [repository_evidence]
#
# The last four are the strongest cases: the SAME trigger name with DIFFERENT
# obligations, so only the evidence can decide between them. A family lists only
# the routes that actually service that trigger -- listing all three would invent
# obligations the contract does not have.
#
# NOTE on `invalidate`: all three bundles require the single evidence kind
# `invalidation` for their invalidate transition, so the contract genuinely cannot
# prefer one over another there. That family is kept for coverage of genuine
# ambiguity (it should always derive no-preference, never a forced gold), not as a
# discriminating case.
_DEFAULT_CAPABILITIES: dict[str, dict[str, tuple[str, ...]]] = {
    "planning_change": {
        "getter": ("intent", "risk"),
        "infrae": ("plan", "invariants", "rollback"),
    },
    "mutating_change": {
        "getter": ("authority", "rollback"),
        "infrae": ("change_gate", "authority"),
    },
    "observing_state": {
        "gitter": ("repository_truth",),
        "infrae": ("discovery",),
        "getter": ("repository_evidence",),
    },
    "verifying_change": {
        "getter": ("repository_evidence",),
        "infrae": ("effective_state", "regression_check"),
    },
    "stabilizing_change": {
        "getter": ("repository_evidence",),
        "infrae": ("verified_invariants", "operational_health"),
    },
    "recovering_change": {
        "getter": ("rollback_plan",),
        "infrae": ("recovery_plan",),
    },
    "observing_repository": {
        "gitter": ("repository_truth",),
        "getter": ("repository_evidence",),
    },
    "invalidating_state": {
        "getter": ("invalidation",),
        "gitter": ("invalidation",),
        "infrae": ("invalidation",),
    },
    # Two more families the contracts supply, both decidable:
    #   infrae ASSESSED->BLOCKED on block  [unknown_ownership]
    #   infrae PLANNED->BLOCKED  on block  [unacceptable_risk]
    "blocking_change": {
        "infrae": ("unknown_ownership",),
        "getter": ("unacceptable_risk",),
    },
    #   getter VERIFIED->RECOVERING on recover [failed_verification]
    #   infrae CHANGING->RECOVERING on recover [recovery_plan]
    "recovering_verification": {
        "getter": ("failed_verification",),
        "infrae": ("recovery_plan",),
    },
    #   gitter STALE->UNKNOWN on reobserve [reinspection]
    #   getter RECOVERING->OBSERVED on observe [repository_evidence]
    # gitter's third transition, and the only trigger besides `invalidate` that
    # is not already shared -- this is what lifts the under-represented class.
    "reobserving_after_stale": {
        "gitter": ("reinspection",),
        "getter": ("repository_evidence",),
    },
}

# Evidence kinds actually referenced by the obligations above, verified against
# the authoritative transitions.yaml files.
EVIDENCE_KINDS = (
    "intent", "risk", "authority", "rollback", "plan", "invariants",
    "change_gate", "repository_truth", "repository_evidence", "discovery",
    "effective_state", "regression_check", "verified_invariants",
    "operational_health", "rollback_plan", "recovery_plan", "invalidation",
    "unknown_ownership", "unacceptable_risk", "failed_verification",
    "reinspection",
)

# Fixed seed for the instance split, so the same request always yields the same
# partitions. A moving split would make results uncomparable across runs.
_STABLE_SEED = 20261014

# Objective prose per capability: names the trigger the task needs.
_CAPABILITY_PROSE: dict[str, tuple[str, ...]] = {
    "planning_change": (
        "Plan the change: confirm the intent and assess the risk before committing.",
        "Prepare the change plan, identifying what is intended and what it risks.",
        "Work out the design for the change, with invariants and a rollback path.",
    ),
    "mutating_change": (
        "Apply the change now that authority and a rollback exist.",
        "Perform the mutation through the change gate with authority in place.",
        "Carry out the change that has been approved for execution.",
    ),
    "observing_state": (
        "Establish the current state of the target before acting.",
        "Find out what the target actually is right now.",
        "Capture the authoritative current state.",
    ),
    "verifying_change": (
        "Verify the change before declaring it complete.",
        "Confirm the change had the intended effect and nothing regressed.",
        "Check that the applied change is correct.",
    ),
    "stabilizing_change": (
        "Stabilise the change once it has been verified.",
        "Bring the change to a stable, healthy end state.",
        "Finish the change and confirm it is steady.",
    ),
    "recovering_change": (
        "Recover from the failure that the change introduced.",
        "Get back to a safe state after the change went wrong.",
        "Roll back and re-establish a working state.",
    ),
    "observing_repository": (
        "Look again at the repository to see what it really contains.",
        "Re-observe the repository and establish its true current state.",
        "Check what the repository records before acting on it.",
    ),
    "invalidating_state": (
        "Mark the current state as stale and in need of re-observation.",
        "Invalidate what is believed about the current state.",
        "Declare the recorded state no longer trustworthy.",
    ),
    "blocking_change": (
        "Block the change until its ownership is established.",
        "Hold the change because the risk involved is not acceptable.",
        "Stop the change until it can be safely owned.",
    ),
    "recovering_verification": (
        "Recover after the change failed its verification.",
        "Get back to a safe state now that verification has failed.",
        "Roll back the change that verification rejected.",
    ),
    "reobserving_after_stale": (
        "Re-inspect what has been marked stale and re-establish the truth.",
        "Look again at the stale state before trusting it.",
        "Re-observe the target now that it has been invalidated.",
    ),
}


# Task-instance vocabulary. A task is only usable if the plan can state its
# intent and risk, the mutation can be authorised with a rollback, and the
# verification can be settled by repository evidence. Real task instances are
# what make partitions genuinely independent: two cases about the same
# referent share their wording and referent, so partitioning on referent
# (rather than on case_id alone) is the difference between separated families
# and separated rows.
TASK_INSTANCES = (
    "the checkout retry backoff",
    "the OAuth token refresh",
    "the nightly report cache",
    "the telemetry batching window",
    "the user-profile migration",
    "the feature-flag reader",
    "the invoice PDF renderer",
    "the webhook signature check",
    "the search indexer",
    "the rate-limit bucket sweep",
    "the audit-log rotation",
    "the billing proration path",
    "the session store eviction",
    "the notification fan-out",
    "the image thumbnail queue",
    "the config hot-reload hook",
    "the LDAP group sync",
    "the payment retry scheduler",
    "the email digest composer",
    "the SSO metadata refresh",
    # Second cohort. The under-selected `gitter` is reachable only through a few
    # of the eleven trigger families, so the instance count is what sets its
    # per-class sample size; these lift every class, and `gitter` especially,
    # above the per-class recall floor.
    "the payment-gateway reconciliation",
    "the repository-read-only mirror",
    "the cache-key namespace check",
    "the config drift audit",
    "the duplicate-event detector",
    "the ledger balance checker",
    "the webhook replay filter",
    "the schema-version inspector",
    "the artifact provenance scan",
    "the stale-marker sweeper",
    "the repository diff summariser",
    "the infrastructure inventory probe",
    "the health-check aggregation",
    "the secret rotation schedule",
    "the TLS certificate watcher",
    "the log-shipping pipeline",
    "the queue backpressure monitor",
    "the failover region selector",
    "the capacity forecast model",
    "the dependency-graph builder",
)


@dataclass(frozen=True)
class ContrastiveCase:
    """One routing decision with contract-derived gold."""

    case_id: str
    capability: str
    objective: str
    eligible: tuple[str, ...]
    held_evidence: tuple[str, ...]
    obligations: Mapping[str, tuple[str, ...]]
    gold: str | None
    ambiguity_reason: str | None
    permutation: int = 0
    # The task instance this case is about. Partitioning on this field is what
    # makes splits independent -- see `partition_by_instance`.
    task_instance: str = ""

    @property
    def is_ambiguous(self) -> bool:
        return self.gold is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "capability": self.capability,
            "objective": self.objective,
            "eligible": list(self.eligible),
            "held_evidence": list(self.held_evidence),
            "obligations": {k: list(v) for k, v in self.obligations.items()},
            "gold": self.gold,
            "ambiguity_reason": self.ambiguity_reason,
            "permutation": self.permutation,
            "task_instance": self.task_instance,
        }


def _derive_gold(eligible: Sequence[str], held: Sequence[str],
                 obligations: Mapping[str, tuple[str, ...]]
                 ) -> tuple[str | None, str | None]:
    """Contract derivation: unique eligible route whose obligation is satisfiable.

    Returns (gold, ambiguity_reason). Never guesses: where the contract does not
    determine a unique answer, gold is None and the reason is recorded.
    """
    held_set = set(held)
    satisfiable = [sid for sid in eligible
                   if set(obligations.get(sid, ())) <= held_set]
    if len(satisfiable) == 1:
        return satisfiable[0], None
    if not satisfiable:
        return None, "no_eligible_obligation_satisfiable"
    return None, f"multiple_eligible:{','.join(sorted(satisfiable))}"


def distinct_rotations(capability: str) -> int:
    """How many genuinely different assignments exist for a family.

    Equals the number of routes in the family: rotating an n-route family by 1
    produces the next distinct assignment. Stating this matters because holding
    out a rotation that is IDENTICAL to the trained one would leak.
    """
    return len(_DEFAULT_CAPABILITIES[capability])


def _assign_obligations(capability: str, permutation: int
                        ) -> Mapping[str, tuple[str, ...]]:
    """Assign obligations to the family's routes, optionally rotated.

    permutation=0 -> canonical (obligation co-varies with the owning name).
    permutation=k -> rotate the obligation list by k.

    Rotation is the decoupling mechanism: the same evidence obligation attaches
    to a different StateWork. Gold follows the OBLIGATION because gold is derived
    from feasibility, not identity. Rotation is modulo the family size, so a
    rotation that would be the identity is refused rather than silently ignored.
    """
    canonical = _DEFAULT_CAPABILITIES[capability]
    routes = list(canonical)
    pool = [tuple(canonical[sid]) for sid in routes]
    if permutation:
        k = permutation % len(pool)
        if k == 0:
            raise ValueError(
                f"rotation {permutation} is the identity for a {len(routes)}-route "
                f"family ({capability}); it would not decouple anything")
        pool = pool[-k:] + pool[:-k]
    return {sid: pool[index] for index, sid in enumerate(routes)}


def _subsets(kinds: Sequence[str]) -> list[tuple[str, ...]]:
    """Every subset of `kinds`, deduplicated and canonically ordered."""
    out: set[tuple[str, ...]] = set()
    for size in range(len(kinds) + 1):
        for combo in itertools.combinations(sorted(set(kinds)), size):
            out.add(combo)
    return sorted(out, key=lambda t: (len(t), t))


def _held_patterns(capability: str, *, keep_ambiguous: int | None = None
                   ) -> list[tuple[str, ...]]:
    """Held-evidence patterns, ranked decidable-first.

    Enumerating every subset of the union of the family's obligation kinds gives a
    mix: patterns where exactly one obligation is satisfiable are the useful
    ones, and the rest (partial obligations, unions, empty) are the abstention
    cases. Decidable patterns come first so capping the list keeps the ratio
    deliberate rather than an accident of set sizes, and a bounded number of
    abstention cases follows so the model still has to learn to abstain.
    """
    obligations = _assign_obligations(capability, 0)
    eligible = list(_DEFAULT_CAPABILITIES[capability])
    union: set[str] = set()
    for obligation in obligations.values():
        union = union | set(obligation)
    decidable: list[tuple[str, ...]] = []
    ambiguous: list[tuple[str, ...]] = []
    for subset in _subsets(sorted(union)):
        gold, _ = _derive_gold(eligible, subset, obligations)
        (decidable if gold is not None else ambiguous).append(subset)
    if keep_ambiguous is None:
        # One abstention case per three decidable cases, and at most half the
        # pattern list, so small families (where the whole pattern list is only a
        # handful of subsets) are not forced to a 50/50 decidable/ambiguous mix
        # by the minimum. The decidable share is the binding constraint: with too
        # few decidable cases the coupled/decoupled comparison has no footing.
        keep_ambiguous = min(len(decidable) // 3, max(1, len(decidable) // 2))
    if not decidable:
        # A family whose contract genuinely cannot prefer any route (e.g.
        # `invalidating_state`, where all three bundles require the same single
        # evidence kind) contributes ambiguity coverage only. Keep its patterns
        # so that coverage is not silently lost, but never manufacture a gold.
        keep_ambiguous = len(ambiguous)
    return decidable + ambiguous[:keep_ambiguous]


def generate_cases(*, task_instances: Sequence[str] = TASK_INSTANCES,
                   max_patterns: int = 12,
                   permutations: Sequence[int] | None = None
                   ) -> tuple[ContrastiveCase, ...]:
    """Generate contract-grounded cases across capabilities, instances, rotations.

    Three axes multiply out:
      * capability      -- a trigger more than one StateWork can service
      * task instance   -- the concrete referent, which is what makes cases
                           separable into independent families
      * held evidence   -- the subset of obligation kinds the host holds, which
                           is what makes gold derivable
      * rotation        -- rotating obligations among routes, which is what
                           decouples name from obligation

    The objective text is `f"{trigger prose} for {task instance}."`, so every
    (capability, instance) pair is a distinct surface form rather than the same
    three sentences re-used. That matters for partitioning: wording is part of
    what makes two examples non-independent.
    """
    cases: list[ContrastiveCase] = []
    for capability in _DEFAULT_CAPABILITIES:
        rotations = range(distinct_rotations(capability))
        if permutations is not None:
            rotations = [r for r in rotations if r in permutations]
        patterns = _held_patterns(capability)[:max_patterns]
        prose = _CAPABILITY_PROSE[capability]
        for permutation in rotations:
            obligations = _assign_obligations(capability, permutation)
            eligible = tuple(_DEFAULT_CAPABILITIES[capability])
            for variant, held in enumerate(patterns):
                gold, reason = _derive_gold(eligible, held, obligations)
                for instance_index, instance in enumerate(task_instances):
                    objective = (
                        f"{prose[(variant + instance_index) % len(prose)]} "
                        f"Target: {instance}.")
                    cases.append(ContrastiveCase(
                        case_id=(f"{capability}-p{permutation}-v{variant}"
                                 f"-{_slug(instance)}"),
                        capability=capability,
                        objective=objective,
                        eligible=eligible,
                        held_evidence=held,
                        obligations=dict(obligations),
                        gold=gold,
                        ambiguity_reason=reason,
                        permutation=permutation,
                        task_instance=instance))
    return tuple(cases)


def _slug(text: str) -> str:
    """Stable, filename-safe id fragment for a task instance."""
    return "-".join("".join(ch for ch in word if ch.isalnum())
                    for word in text.split()).lower()


def default_partitions(cases: Sequence[ContrastiveCase]
                       ) -> dict[str, list[ContrastiveCase]]:
    """A usable train / calibration / holdout split, grouped by task instance.

    The split assigns INSTANCES to partitions, never individual cases, so no
    held-out case shares a referent or wording with a training case. That is the
    property a case_id-based split cannot give.

    Sizing is by block, not by a fraction of a shuffled list: each capability x
    instance block is one unit, and units are dealt round-robin into partitions
    so every partition sees a comparable mix of capabilities and rotations
    instead of an accidental skew. Ambiguous blocks are kept in every partition
    so abstention is learned and measured everywhere, not just in train.
    """
    import random

    holdout_units, calib_units, train_units = [], [], []
    for capability in _DEFAULT_CAPABILITIES:
        blocks = [c for c in cases if c.capability == capability]
        instances = sorted({c.task_instance for c in blocks})
        rng = random.Random(_STABLE_SEED)
        ordered = list(instances)
        rng.shuffle(ordered)
        # Every instance contributes its block to exactly one partition, so the
        # partitions are independent by construction.
        for position, instance in enumerate(ordered):
            block = [c for c in blocks if c.task_instance == instance]
            if position % 5 == 4:
                holdout_units.append(block)
            elif position % 5 == 3:
                calib_units.append(block)
            else:
                train_units.append(block)

    out: dict[str, list[ContrastiveCase]] = {
        "train": [c for block in train_units for c in block],
        "calibration": [c for block in calib_units for c in block],
        "holdout": [c for block in holdout_units for c in block],
    }
    if not out["holdout"]:
        raise ValueError("no holdout cases produced")
    return out


def partition_by_instance(cases: Sequence[ContrastiveCase], *,
                          holdout_instances: Sequence[str] = (),
                          calib_instances: Sequence[str] = ()
                          ) -> dict[str, list[ContrastiveCase]]:
    """Partition by TASK INSTANCE, never by case_id.

    Distinct case ids do not imply independent examples: two cases about the same
    referent share wording and referent, so holding out one while training on the
    other leaks. Partitioning on the instance field means a held-out case is about
    something the model has never seen the wording or the referent of.

    Rotation is orthogonal and is preserved inside each partition, so the holdout
    still contains gold that contradicts the canonical names -- adversarial to
    name-matching AND about unseen targets.
    """
    hold = {i for i in holdout_instances}
    calib = {i for i in calib_instances}
    out: dict[str, list[ContrastiveCase]] = {"train": [], "calibration": [],
                                             "holdout": []}
    for case in cases:
        if case.task_instance in hold:
            out["holdout"].append(case)
        elif case.task_instance in calib:
            out["calibration"].append(case)
        else:
            out["train"].append(case)
    if not out["holdout"]:
        raise ValueError("no holdout cases; name held-out instances")
    return out


def coverage_report(cases: Sequence[ContrastiveCase]) -> dict[str, Any]:
    """Report gold distribution and the decoupling (name-breaking) fraction.

    Comparison is keyed on (capability, held_evidence), NOT on case_id alone:
    case_id embeds the permutation, and the canonical gold must come from the
    matching rotation-0 case rather than whichever one was seen last.
    """
    by_capability: dict[str, int] = {}
    golds: dict[str, int] = {}
    ambiguous = 0
    permuted = 0
    decoupled = 0

    canonical: dict[tuple[str, tuple[str, ...]], str | None] = {}
    for case in cases:
        if case.permutation == 0:
            canonical[(case.capability, case.held_evidence)] = case.gold

    for case in cases:
        by_capability[case.capability] = by_capability.get(case.capability, 0) + 1
        if case.gold is None:
            ambiguous += 1
        else:
            golds[case.gold] = golds.get(case.gold, 0) + 1
        if case.permutation == 0:
            continue
        permuted += 1
        expected = canonical.get((case.capability, case.held_evidence))
        if expected is not None and case.gold is not None and case.gold != expected:
            decoupled += 1

    total = len(cases)
    return {
        "total": total,
        "by_capability": by_capability,
        "gold_distribution": golds,
        "ambiguous": ambiguous,
        "ambiguous_fraction": round(ambiguous / max(1, total), 3),
        "decidable_fraction": round((total - ambiguous) / max(1, total), 3),
        "permuted_cases": permuted,
        "name_decoupled": decoupled,
        "name_decoupled_fraction": (round(decoupled / max(1, permuted), 3)
                                    if permuted else 0.0),
    }


def validate(cases: Sequence[ContrastiveCase]) -> tuple[str, ...]:
    """Actionable diagnostics. Empty means the set is well-formed."""
    problems: list[str] = []
    seen: set[str] = set()
    for case in cases:
        if case.case_id in seen:
            problems.append(f"duplicate case_id {case.case_id}")
        seen.add(case.case_id)
        if case.gold is not None and case.gold not in case.eligible:
            problems.append(
                f"{case.case_id}: gold {case.gold!r} is not eligible")
        # Derivation must be reproducible from the stated facts.
        derived, _ = _derive_gold(case.eligible, case.held_evidence,
                                  case.obligations)
        if derived != case.gold:
            problems.append(
                f"{case.case_id}: gold {case.gold!r} does not match contract "
                f"derivation {derived!r}")
        if case.gold is None and not case.ambiguity_reason:
            problems.append(f"{case.case_id}: ambiguous case lacks a reason")
        if not case.objective.strip():
            problems.append(f"{case.case_id}: missing objective")
        # A selected route must actually have its evidence held.
        if case.gold is not None:
            held = set(case.held_evidence)
            if not set(case.obligations[case.gold]) <= held:
                problems.append(
                    f"{case.case_id}: gold {case.gold!r} selected but its "
                    "obligation is not satisfiable from held evidence")
        for sid, obligation in case.obligations.items():
            if not set(obligation) <= set(EVIDENCE_KINDS):
                problems.append(
                    f"{case.case_id}: {sid} obligation names unknown evidence")
    return tuple(problems)


def export_jsonl(cases: Sequence[ContrastiveCase]) -> str:
    """Serialise for the Laya fine-tuning JSONL pipeline."""
    return "\n".join(json.dumps(case.to_dict(), ensure_ascii=False)
                     for case in cases) + "\n"


__all__ = [
    "EVIDENCE_KINDS",
    "ContrastiveCase",
    "coverage_report",
    "distinct_rotations",
    "export_jsonl",
    "generate_cases",
    "validate",
]
