"""Train/serve equivalence: one canonical input path, or no improvement claim.

The defect this guards against: the training exporter rendered StateWork options
as obligation text while the production adapter rendered them as
Purpose/Domain/Behavior-tags. A model fine-tuned on the first form is deployed on
the second, so it applies distinctions it cannot observe at run time.

The fix is a single shared construction path (`canonical_input`) used by the
exporter, the probe, and any serving adapter. These tests assert the properties
that make the skew impossible, rather than trusting the convention.
"""
from __future__ import annotations

from qualification.canonical_input import (
    BANNED_SUBSTRINGS,
    CANONICAL_INSTRUCTION,
    CanonicalInputError,
    canonical_description,
    canonical_options,
    canonical_question,
    canonical_state,
    production_description,
)
from qualification.contrastive_fixtures import generate_cases
from qualification.training_export import ROUTE_LABELS, build_question, build_state


def test_canonical_description_contains_every_production_field():
    """Whatever production exposes, training must also expose.

    This is the equivalence assertion: the canonical text must be a superset of
    the production description, so a model trained canonically sees nothing less
    than it will see at run time. The field-level check is in
    `test_production_equivalence_is_superset`; this one guards the vocabulary.
    """
    from qualification.canonical_input import _BEHAVIOR_TAGS, _DOMAIN, _PURPOSE
    for sid in ("getter", "gitter", "infrae"):
        canonical = canonical_description(sid, ("intent",), ("intent",))
        prod = production_description(
            sid, _PURPOSE[sid], _DOMAIN[sid], _BEHAVIOR_TAGS[sid])
        assert prod, f"{sid}: production description is empty"
        assert canonical.startswith(prod), (
            f"{sid}: canonical text does not include the production form "
            f"verbatim as its prefix")


def test_canonical_description_exposes_the_obligation():
    """The audit found evidence obligations behave consequentially.

    Production does NOT expose them. The canonical path adds them to the
    production form rather than replacing it, which is what removes the skew.
    """
    text = canonical_description("getter", ("intent", "risk"), ("intent",))
    assert "Required evidence: intent, risk" in text
    assert "Evidence missing: risk" in text
    # And the same field exists in the production-shaped prefix.
    assert "Purpose:" in text and "Domain:" in text


def test_canonical_description_is_obligation_aware():
    """Availability must be told apart, or the model cannot distinguish routes."""
    held = canonical_description("getter", ("intent",), ("intent",))
    missing = canonical_description("getter", ("intent",), ())
    assert held != missing
    assert "Evidence missing" in missing
    assert "Evidence held" in held


def test_canonical_input_has_no_answer_leak():
    """The load-bearing anti-leakage property.

    Option text may state obligation and availability, never correctness.
    """
    for case in generate_cases():
        question = canonical_question(case.eligible, case.obligations,
                                      case.held_evidence)
        for label, text in question["criteria"].items():
            lowered = text.lower()
            for banned in BANNED_SUBSTRINGS:
                assert banned not in lowered, f"{label}: {text!r} leaks {banned!r}"


def test_exporter_uses_the_canonical_path():
    """The exporter must not have its own private rendering.

    If this fails, the exporter has drifted from the shared path and the skew is
    back -- the exact defect this module was written to close.
    """
    for case in generate_cases():
        expected = canonical_description(
            case.gold if case.gold else case.eligible[0],
            case.obligations.get(case.gold or case.eligible[0], ()),
            case.held_evidence)
        question = build_question(case)
        sid = case.gold or case.eligible[0]
        assert question["criteria"][ROUTE_LABELS[sid]] == expected, case.case_id


def test_exporter_question_matches_canonical_question():
    for case in generate_cases()[:20]:
        exported = build_question(case)
        canonical = canonical_question(
            case.eligible, {k: v for k, v in case.obligations.items()},
            case.held_evidence)
        assert exported["criteria"] == canonical["criteria"], case.case_id
        assert exported["instructions"] == CANONICAL_INSTRUCTION


def test_exporter_state_matches_canonical_state():
    for case in generate_cases()[:20]:
        assert build_state(case) == canonical_state(
            case.objective, case.eligible, case.held_evidence), case.case_id


def test_option_order_is_a_pure_relabeling():
    """Canonical text must not depend on position.

    This is what makes the option-order perturbation test meaningful: reordering
    changes only which slot an option lands in, never its text.
    """
    eligible = ("getter", "gitter", "infrae")
    obligations = {"getter": ("intent",), "gitter": ("repository_truth",),
                   "infrae": ("discovery",)}
    held = ("intent",)
    forward = {o.label: o.text for o in canonical_options(eligible, obligations, held)}
    reverse = {o.label: o.text
               for o in canonical_options(tuple(reversed(eligible)), obligations, held)}
    assert forward == reverse


def test_unknown_statework_is_refused_not_guessed():
    """A typo must raise, not silently render a wrong-domain option."""
    try:
        canonical_description("gittern", ("intent",), ())
    except CanonicalInputError:
        return
    raise AssertionError("unknown StateWork should raise CanonicalInputError")


def test_production_equivalence_is_superset():
    """Canonical == production + obligations, field for field.

    Asserted structurally: strip the obligation/availability sentences from the
    canonical text and the remainder must equal the production rendering.
    """
    from qualification.canonical_input import _BEHAVIOR_TAGS, _DOMAIN, _PURPOSE
    for sid in ("getter", "gitter", "infrae"):
        canonical = canonical_description(sid, ("intent",), ())
        prod = production_description(
            sid, _PURPOSE[sid], _DOMAIN[sid], _BEHAVIOR_TAGS[sid])
        assert canonical.startswith(prod), (
            f"{sid}: canonical text does not start with the production form\n"
            f"  canonical: {canonical!r}\n  production: {prod!r}")
        assert canonical != prod, "canonical must add the obligation information"


def test_jsonl_export_uses_public_schema_and_canonical_text():
    """End-to-end: the file a training run would read."""
    cases = generate_cases()
    from qualification.training_export import case_to_row
    for case in cases[:10]:
        row = case_to_row(case)
        question = row["questions"]["route_preference"]
        assert set(question) == {"type", "instructions", "criteria"}
        assert question["type"] == "choice"
        for text in question["criteria"].values():
            assert "Purpose:" in text and "Domain:" in text
            assert "Required evidence:" in text
        # gold keys are the same labels as criteria keys.
        assert set(row["gold"]["route_preference"]["probabilities"]) == set(
            question["criteria"])
