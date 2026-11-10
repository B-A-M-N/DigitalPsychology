"""Deliverable B — adversarial route_preference fixtures.

Labels are derived from the REAL bundled StateWork contracts rather than authored
by assertion, so a fixture's "appropriate" route is the one whose governing
evidence requirements the task state can actually satisfy.

The `semantic_would_worsen` and `correct_deterministic` classes are deliberate
traps: a model that overrides correct deterministic selections, or prefers an
attractive-but-worse route, must score as harmful. Without them a benchmark
rewards overconfidence.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

# Evidence requirements read from the bundled transitions.yaml contracts.
# getter: 6 distinct required_evidence sets.
# gitter: 3 distinct required_evidence sets.
# infrae: 16 distinct required_evidence sets.
BUNDLED_CONTRACTS: Mapping[str, Mapping[str, Any]] = {
    "getter": {
        "routes": ("observe", "plan", "mutate", "verify", "repair", "rollback"),
        "evidence": {
            "observe": ("repository_evidence",),
            "plan": ("intent", "risk"),
            "mutate": ("intent", "risk", "rollback_plan"),
            "verify": ("repository_evidence",),
            "repair": ("failed_verification", "rollback_plan"),
            "rollback": ("rollback_plan", "authority"),
        },
    },
    "gitter": {
        "routes": ("observe", "invalidate", "reinspect"),
        "evidence": {
            "observe": ("repository_truth",),
            "invalidate": ("invalidation",),
            "reinspect": ("reinspection",),
        },
    },
    "infrae": {
        "routes": ("discover", "model", "assess", "change", "verify", "recover",
                   "decommission"),
        "evidence": {
            "discover": ("discovery",),
            "model": ("observed_model",),
            "assess": ("assessment",),
            "change": ("change_gate", "authority", "plan", "invariants", "rollback"),
            "verify": ("verified_invariants", "operational_health"),
            "recover": ("recovery_plan",),
            "decommission": ("decommission_checklist",),
        },
    },
}


# The ten required case classes.
CASE_CLASSES = (
    "correct_deterministic",
    "incorrect_deterministic",
    "ambiguous_preference",
    "contradictory_evidence",
    "missing_repository_truth",
    "recovery_vs_continuation",
    "evidence_gated_transition",
    "ineligible_candidate",
    "semantic_would_worsen",
    "verification_before_mutation",
)


@dataclass(frozen=True)
class RouteFixture:
    """One adversarial routing case, labelled against its governing contract."""

    case_id: str
    case_class: str
    bundle: str
    eligible_routes: tuple[str, ...]
    appropriate_route: str | None
    abstention_expected: bool
    available_evidence: tuple[str, ...]
    rationale: str
    # Set when the semantically attractive route is NOT the appropriate one.
    tempting_route: str | None = None

    def __post_init__(self) -> None:
        if self.case_class not in CASE_CLASSES:
            raise ValueError(f"unsupported fixture class: {self.case_class!r}")
        if self.bundle not in BUNDLED_CONTRACTS:
            raise ValueError(f"unknown StateWork bundle: {self.bundle!r}")
        contract = BUNDLED_CONTRACTS[self.bundle]
        allowed = set(contract["routes"])
        unknown = set(self.eligible_routes) - allowed
        if unknown:
            raise ValueError(
                f"fixture {self.case_id}: routes outside the {self.bundle} "
                f"contract: {sorted(unknown)}")
        if self.appropriate_route is not None:
            if self.appropriate_route not in self.eligible_routes:
                raise ValueError(
                    f"fixture {self.case_id}: appropriate route is not eligible")
            self._verify_evidence(contract, self.appropriate_route)
        if (self.tempting_route is not None
                and self.tempting_route not in self.eligible_routes):
            raise ValueError(
                f"fixture {self.case_id}: tempting route is not eligible")

    def _verify_evidence(self, contract: Mapping[str, Any], route: str) -> None:
        """The appropriate route's evidence requirements must be satisfiable.

        This is what makes the label contract-derived rather than asserted: a
        route whose required evidence the task state cannot supply is not the
        appropriate route.
        """
        required = set(contract["evidence"].get(route, ()))
        missing = required - set(self.available_evidence)
        if missing:
            raise ValueError(
                f"fixture {self.case_id}: route {route!r} requires evidence the "
                f"task state cannot supply: {sorted(missing)}")

    @property
    def is_trap(self) -> bool:
        """True when choosing the tempting route would worsen the result."""
        return (self.tempting_route is not None
                and self.tempting_route != self.appropriate_route)


def _fixture(case_id: str, case_class: str, bundle: str, eligible: tuple[str, ...],
             appropriate: str | None, abstention: bool, evidence: tuple[str, ...],
             rationale: str, tempting: str | None = None) -> RouteFixture:
    return RouteFixture(
        case_id=case_id, case_class=case_class, bundle=bundle,
        eligible_routes=eligible, appropriate_route=appropriate,
        abstention_expected=abstention, available_evidence=evidence,
        rationale=rationale, tempting_route=tempting)


def build_fixture_set() -> tuple[RouteFixture, ...]:
    """One fixture per required class, each labelled against its contract."""
    return (
        # 1. Deterministic selection is correct; intervention must NOT fire.
        _fixture("correct-001", "correct_deterministic", "getter",
                 ("observe", "plan"), "observe", False,
                 ("repository_evidence",),
                 "Repository state is unknown; observe is the only defensible route.",
                 tempting="plan"),

        # 2. Deterministic selection is wrong; the opportunity case.
        _fixture("incorrect-001", "incorrect_deterministic", "getter",
                 ("observe", "repair"), "repair", False,
                 ("failed_verification", "rollback_plan"),
                 "A verification failed, so repair is appropriate; the "
                 "deterministic tag match selects observe.",
                 tempting="observe"),

        # 3. No defensible preference; abstention expected.
        _fixture("ambiguous-001", "ambiguous_preference", "gitter",
                 ("observe", "reinspect"), None, True,
                 ("repository_truth", "reinspection"),
                 "Both routes are fully supported; no preference is defensible."),

        # 4. Evidence conflicts; abstention expected.
        _fixture("contradictory-001", "contradictory_evidence", "gitter",
                 ("observe", "invalidate"), None, True,
                 ("repository_truth", "invalidation"),
                 "Truth and invalidation evidence are both present and conflict."),

        # 5. Repository state unknown. `observe` REQUIRES repository_truth, which
        #    is precisely what is missing, so no route is satisfiable and the
        #    correct behaviour is abstention rather than a forced choice.
        _fixture("missing-truth-001", "missing_repository_truth", "gitter",
                 ("observe", "reinspect"), None, True,
                 (),
                 "gitter.observe requires repository_truth, which the task state "
                 "does not hold, so no route can be justified; abstain."),

        # 6. Recovery versus continuation.
        _fixture("recovery-001", "recovery_vs_continuation", "getter",
                 ("repair", "plan"), "repair", False,
                 ("failed_verification", "rollback_plan"),
                 "A failed verification forces repair before any new plan.",
                 tempting="plan"),

        # 7. Evidence-gated transition.
        _fixture("gated-001", "evidence_gated_transition", "infrae",
                 ("model", "change"), "model", False,
                 ("observed_model",),
                 "Change requires change_gate, authority, plan, invariants and "
                 "rollback evidence, none of which exist yet."),

        # 8. Preferred route is not CSW-eligible.
        _fixture("ineligible-001", "ineligible_candidate", "getter",
                 ("observe", "verify"), "verify", False,
                 ("repository_evidence",),
                 "A preference for rollback must be ignored: it is not eligible.",
                 tempting="observe"),

        # 9. The semantically attractive route makes the result WORSE.
        _fixture("worsen-001", "semantic_would_worsen", "getter",
                 ("observe", "mutate"), "observe", False,
                 ("repository_evidence",),
                 "Mutating before observing destroys the evidence needed to "
                 "verify the change; observe is correct despite mutate appearing "
                 "more productive.",
                 tempting="mutate"),

        # 10. Verification must precede mutation.
        _fixture("verify-first-001", "verification_before_mutation", "getter",
                 ("verify", "mutate"), "verify", False,
                 ("repository_evidence",),
                 "Unverified mutation is irreversible; verification comes first.",
                 tempting="mutate"),
    )


def coverage_report(fixtures: tuple[RouteFixture, ...]) -> dict[str, Any]:
    """Report class coverage and how many cases are deliberate traps."""
    by_class: dict[str, int] = {}
    for fixture in fixtures:
        by_class[fixture.case_class] = by_class.get(fixture.case_class, 0) + 1
    missing = [name for name in CASE_CLASSES if name not in by_class]
    return {
        "total": len(fixtures),
        "by_class": by_class,
        "missing_classes": missing,
        "traps": sum(1 for f in fixtures if f.is_trap),
        "abstention_expected": sum(1 for f in fixtures if f.abstention_expected),
        "complete": not missing,
    }


__all__ = ["BUNDLED_CONTRACTS", "CASE_CLASSES", "RouteFixture",
           "build_fixture_set", "coverage_report"]
