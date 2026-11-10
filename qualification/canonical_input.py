"""ONE canonical model-input construction path shared by training and serving.

The defect this fixes: the training exporter rendered options as
`"getter: requires intent, risk (missing required evidence: risk)."` while the
production adapter (`lib/control/statework_selector.eligible_statework_descriptions`)
renders them as `"Purpose: ... Domain: ... Behavior tags: ..."` with no
evidence-obligation information at all. Fine-tuning on one representation and
deploying with another teaches the model distinctions it cannot observe at run
time.

This module is the single source of the model-facing text for a StateWork option.
Training, offline evaluation, and any probe call it, so all three drifts are
impossible by construction rather than by convention.

What it emits, and why each part is legitimate pre-decision fact:

  * `Purpose` -- from the CSW contract for that StateWork.
  * `Domain` -- repository / infrastructure / governance.
  * `Required evidence` -- the evidence kinds that StateWork's transition
    requires for the trigger under consideration. This is the fact the audit
    found behaviorally consequential, and it is known BEFORE the decision.
  * `Evidence held` / `Evidence missing` -- what the host actually holds, which
    is the pre-decision availability check.

What it must never emit: which route is correct, the label frequencies, or any
derived-from-gold signal. `test_canonical_input_has_no_answer_leak` enforces this.

CSW remains the source of authoritative workflow facts. Callers pass the
obligations in; this module only formats them.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

# Domain per StateWork, from the CSW contracts. getter manipulates repository
# state, gitter observes repository truth, infrae operates on infrastructure.
_DOMAIN = {
    "getter": "repository",
    "gitter": "repository",
    "infrae": "infrastructure",
}

# Purpose per StateWork, mirroring what the CSW bundle is for. Kept short so the
# rendered option stays inside the head budget.
_PURPOSE = {
    "getter": "Change repository state under a plan and rollback.",
    "gitter": "Observe and report authoritative repository state.",
    "infrae": "Discover, model, and change infrastructure.",
}

# Behavior tags, matching the production description vocabulary.
_BEHAVIOR_TAGS = {
    "getter": ("mutating", "planned", "reversible"),
    "gitter": ("observing", "read_only", "invalidating"),
    "infrae": ("modelling", "mutating", "recoverable"),
}

# The model-facing instruction, shared by training and serving.
CANONICAL_INSTRUCTION = (
    "Which StateWork should be selected for this task? "
    "Choose the workflow whose required evidence the host can actually satisfy."
)

# Strings that would leak the answer into the option text. Enforced by test.
BANNED_SUBSTRINGS = (
    "correct", "answer", "should be selected", "preferred", "gold", "winner",
    "definitely", "obvious",
)


class CanonicalInputError(ValueError):
    """Raised when a caller asks for input that cannot be built canonically."""


@dataclass(frozen=True)
class CanonicalOption:
    """One model-facing option: label plus text, built one way everywhere."""

    label: str
    text: str

    def to_dict(self) -> dict[str, Any]:
        return {"label": self.label, "text": self.text}


def canonical_description(sid: str, obligation: Sequence[str] = (),
                          held_evidence: Iterable[str] = ()) -> str:
    """The ONE rendering of a StateWork option, for training and serving alike.

    Deliberately includes the same fields the production adapter exposes
    (purpose, domain, behavior tags) PLUS the evidence obligation and its
    availability. Adding the obligation to the *existing* canonical form is what
    removes the skew -- the alternative, training on obligation-only text, would
    have created a second skew in the opposite direction.
    """
    if sid not in _DOMAIN:
        raise CanonicalInputError(f"unknown StateWork {sid!r}")
    if obligation is None:
        obligation = ()
    held = set(held_evidence or ())
    missing = [kind for kind in obligation if kind not in held]

    parts = [
        f"Purpose: {_PURPOSE[sid]}",
        f"Domain: {_DOMAIN[sid]}",
        "Behavior tags: " + ", ".join(_BEHAVIOR_TAGS[sid]),
    ]
    if obligation:
        parts.append("Required evidence: " + ", ".join(obligation))
        parts.append(
            "Evidence missing: " + ", ".join(missing) if missing
            else "Evidence held: " + ", ".join(sorted(held & set(obligation))))
    return " ".join(part for part in parts if part)


def canonical_options(eligible: Sequence[str],
                      obligations: Mapping[str, Sequence[str]],
                      held_evidence: Iterable[str] = ()
                      ) -> tuple[CanonicalOption, ...]:
    """Canonical options in the order the caller listed them.

    Order is the caller's choice, which is what makes option-order perturbation
    tests possible: the same case can be rendered with a permuted order, and the
    canonical text is unchanged.
    """
    held = set(held_evidence or ())
    return tuple(
        CanonicalOption(
            label=sid,
            text=canonical_description(sid, obligations.get(sid, ()), held))
        for sid in eligible)


def canonical_question(eligible: Sequence[str],
                       obligations: Mapping[str, Sequence[str]],
                       held_evidence: Iterable[str] = ()
                       ) -> dict[str, Any]:
    """The PUBLIC laya question, built from the canonical option text.

    Keys are `type`/`instructions`/`criteria` because that is what
    `Agent._check_question` reads; `t`/`crit`/`ins` is what `_to_internal`
    PRODUCES and is rejected as input.
    """
    options = canonical_options(eligible, obligations, held_evidence)
    return {
        "type": "choice",
        "instructions": CANONICAL_INSTRUCTION,
        "criteria": {option.label: option.text for option in options},
    }


def canonical_state(objective: str, eligible: Sequence[str],
                    held_evidence: Iterable[str] = ()) -> str:
    """The serialized task state, shared by training and serving.

    `laya.encode_state` calls `serialize_state`, which passes a `str` through
    unchanged, so a plain string is exactly what the model will read.
    """
    held = ", ".join(sorted(set(held_evidence or ()))) or "none"
    return (
        f"Task: {objective}\n"
        f"Eligible StateWorks: {', '.join(eligible)}\n"
        f"Evidence held: {held}"
    )


def production_description(sid: str, purpose: str = "", domain: str = "",
                           behavior_tags: Sequence[str] = ()) -> str:
    """The production adapter's rendering, reproduced for equivalence testing.

    Mirrors `lib/control/statework_selector.eligible_statework_descriptions` so
    the shared path can be asserted to CONTAIN everything production exposes.
    """
    return " ".join(part for part in (
        f"Purpose: {purpose}" if purpose else "",
        f"Domain: {domain}" if domain else "",
        "Behavior tags: " + ", ".join(behavior_tags) if behavior_tags else "",
    ) if part)
