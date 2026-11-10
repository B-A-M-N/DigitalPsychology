"""Versioned schema for typed executable adjustments: semantic-adjustment/v1.

Recommendations (proposal drafts) stay advisory. Adjustments are the separate,
executable lane: a host may apply them mechanically, so every adjustment must
carry its full provenance (origin), its exact target (owner + authorized path
+ base revision/hash), its evidence posture (refs or an explicit
unknown-evidence flag -- this module never fabricates refs), its projected
benefit, its rollback criteria, and its expiry.

Three kinds:
- runtime_control: target a CFW control (lib/control/policy_selector.py
  control names) with a proposed value, plus scope; expiry is top-level.
- workflow_preference: target a StateWork with the basis revisions judged.
- skill_improvement: target a skill (flows/*.md procedural skill ids) with a
  proposed invariant; rollback criteria are top-level.

Validation is fail-closed: unknown kinds, unknown targets, out-of-range
values, missing required fields, or an ambiguous evidence posture all raise
ValueError. This module performs no I/O, mints no evidence, and grants no
authority.
"""

from __future__ import annotations

from typing import Any

SCHEMA_VERSION = "semantic-adjustment/v1"

ADJUSTMENT_KINDS = (
    "runtime_control",
    "workflow_preference",
    "skill_improvement",
)

# CFW controls this lane may target, with the legal value set per control.
# Mirrors the bounded control names in lib/control/policy_selector.py.
RUNTIME_CONTROLS = {
    "verification_intensity": ("passive", "normal", "high", "strict"),
    "exploration_budget": ("disabled", "low", "normal", "bounded"),
    "retry_policy": ("normal", "materially_different_only", "no_retry"),
    "recovery_level": (0, 1, 2, 3),
    "context_strategy": ("standard", "compact", "criterion_focused"),
    "response_strategy": ("direct", "detailed", "structured"),
}

# StateWork ids this lane may target (CSW statework namespace, cf. the
# advisory owner mapping in proposal_emitter.FAMILY_TO_OWNER).
AUTHORIZED_STATEWORKS = (
    "godkiller",
    "terraformer",
    "testworks",
    "getter",
    "gitter",
    "infrae",
    "tuid",
)

# Procedural skill ids this lane may target (flows/*.md in this repo).
AUTHORIZED_SKILLS = (
    "characterize",
    "diagnose",
    "intervene",
    "monitor",
    "observe",
    "recover",
    "reproduce",
    "validate",
)

MAX_EVIDENCE_REFS = 16
MAX_BASIS_REVISIONS = 16

_LIMITS = {
    "provider": 64,
    "model": 256,
    "question": 128,
    "decision_id": 256,
    "calibration_digest": 128,
    "owner": 64,
    "path": 256,
    "base_hash": 128,
    "evidence_ref": 512,
    "projected_benefit": 1024,
    "rollback_criteria": 1024,
    "expiry": 128,
    "scope": 128,
    "proposed_invariant": 1024,
}

def _kind_spec(kind):
    if kind == "runtime_control":
        return {
            "target_path": "control/<name>",
            "detail": {"control": sorted(RUNTIME_CONTROLS), "proposed_value": "per-control",
                       "scope": "text"},
        }
    if kind == "workflow_preference":
        return {
            "target_path": "statework/<id>",
            "detail": {"statework": list(AUTHORIZED_STATEWORKS),
                       "basis_revisions": "non-empty list of non-negative ints"},
        }
    return {
        "target_path": "skill/<name>",
        "detail": {"skill": list(AUTHORIZED_SKILLS),
                   "proposed_invariant": "text"},
    }


#: Versioned, machine-readable shape of the schema (one place).
ADJUSTMENT_SCHEMA = {
    "schema": SCHEMA_VERSION,
    "kinds": {kind: None for kind in ADJUSTMENT_KINDS},
    "origin": ["provider", "model", "question", "decision_id", "calibration_digest"],
    "target": ["owner", "path", "base_revision and/or base_hash"],
    "required": ["projected_benefit", "rollback_criteria", "expiry",
                 "evidence_refs or unknown_evidence=True"],
}

ADJUSTMENT_SCHEMA["kinds"] = {kind: _kind_spec(kind) for kind in ADJUSTMENT_KINDS}


def _text(name, value, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"adjustment field {name!r} must be bounded non-empty text")
    return value


def _origin(origin):
    if not isinstance(origin, dict):
        raise ValueError("adjustment origin must be a mapping")
    return {
        "provider": _text("origin.provider", origin.get("provider"), _LIMITS["provider"]),
        "model": _text("origin.model", origin.get("model"), _LIMITS["model"]),
        "question": _text("origin.question", origin.get("question"), _LIMITS["question"]),
        "decision_id": _text("origin.decision_id", origin.get("decision_id"),
                             _LIMITS["decision_id"]),
        "calibration_digest": _text("origin.calibration_digest",
                                    origin.get("calibration_digest"),
                                    _LIMITS["calibration_digest"]),
    }


def _target(target):
    if not isinstance(target, dict):
        raise ValueError("adjustment target must be a mapping")
    out: dict[str, Any] = {
        "owner": _text("target.owner", target.get("owner"), _LIMITS["owner"]),
        "path": _text("target.path", target.get("path"), _LIMITS["path"]),
    }
    revision = target.get("base_revision", None)
    base_hash = target.get("base_hash", None)
    if revision is None and base_hash is None:
        raise ValueError("adjustment target needs a base revision and/or base hash")
    if revision is not None:
        if type(revision) is not int or revision < 0:
            raise ValueError("adjustment target base_revision must be a non-negative int")
        out["base_revision"] = revision
    if base_hash is not None:
        out["base_hash"] = _text("target.base_hash", base_hash, _LIMITS["base_hash"])
    return out


def _evidence(evidence_refs, unknown_evidence):
    if evidence_refs is None:
        evidence_refs = ()
    if isinstance(evidence_refs, str) or not isinstance(evidence_refs, (tuple, list)):
        raise ValueError("adjustment evidence_refs must be a list/tuple of text refs")
    refs = tuple(evidence_refs)
    if len(refs) > MAX_EVIDENCE_REFS:
        raise ValueError("adjustment evidence_refs exceed the bound")
    for ref in refs:
        _text("evidence_ref", ref, _LIMITS["evidence_ref"])
    if not isinstance(unknown_evidence, bool):
        raise ValueError("adjustment unknown_evidence must be an explicit bool")
    if not refs and not unknown_evidence:
        raise ValueError("adjustment with no evidence refs must set unknown_evidence=True")
    if refs and unknown_evidence:
        raise ValueError("adjustment citing evidence refs must set unknown_evidence=False")
    return list(refs)


def _detail(kind, target_path, detail):
    if not isinstance(detail, dict):
        raise ValueError("adjustment detail must be a mapping")
    if kind == "runtime_control":
        control = _text("detail.control", detail.get("control"), 64)
        if control not in RUNTIME_CONTROLS:
            raise ValueError(f"adjustment targets unknown CFW control {control!r}")
        if target_path != f"control/{control}":
            raise ValueError("adjustment target.path must match detail.control")
        proposed = detail.get("proposed_value")
        allowed = RUNTIME_CONTROLS[control]
        if isinstance(allowed[0], int):
            if type(proposed) is not int or proposed not in allowed:
                raise ValueError("adjustment proposed_value is outside the control range")
        else:
            if proposed not in allowed:
                raise ValueError("adjustment proposed_value is outside the control range")
        return {
            "control": control,
            "proposed_value": proposed,
            "scope": _text("detail.scope", detail.get("scope"), _LIMITS["scope"]),
        }
    if kind == "workflow_preference":
        statework = _text("detail.statework", detail.get("statework"), 64)
        if statework not in AUTHORIZED_STATEWORKS:
            raise ValueError(f"adjustment targets unknown StateWork {statework!r}")
        if target_path != f"statework/{statework}":
            raise ValueError("adjustment target.path must match detail.statework")
        revisions = detail.get("basis_revisions")
        if (not isinstance(revisions, (tuple, list)) or not revisions
                or len(revisions) > MAX_BASIS_REVISIONS
                or any(type(item) is not int or item < 0 for item in revisions)):
            raise ValueError("adjustment basis_revisions must be a non-empty list "
                             "of non-negative ints")
        return {"statework": statework, "basis_revisions": list(revisions)}
    if kind == "skill_improvement":
        skill = _text("detail.skill", detail.get("skill"), 64)
        if skill not in AUTHORIZED_SKILLS:
            raise ValueError(f"adjustment targets unknown skill {skill!r}")
        if target_path != f"skill/{skill}":
            raise ValueError("adjustment target.path must match detail.skill")
        return {
            "skill": skill,
            "proposed_invariant": _text("detail.proposed_invariant",
                                        detail.get("proposed_invariant"),
                                        _LIMITS["proposed_invariant"]),
        }
    raise ValueError(f"adjustment kind {kind!r} is unknown")  # pragma: no cover


def build_adjustment(kind, *, origin, target, detail,
                     evidence_refs=(), unknown_evidence=False,
                     projected_benefit, rollback_criteria, expiry):
    """Build one validated semantic-adjustment/v1 payload (pure formatting).

    Caller supplies every field; nothing is minted or defaulted into
    existence. Raises ValueError on any malformed input, unknown kind, or
    unknown target.
    """
    if kind not in ADJUSTMENT_KINDS:
        raise ValueError(f"adjustment kind {kind!r} is unknown")
    normalized_target = _target(target)
    return {
        "schema": SCHEMA_VERSION,
        "kind": kind,
        "origin": _origin(origin),
        "target": normalized_target,
        "evidence_refs": _evidence(evidence_refs, unknown_evidence),
        "unknown_evidence": bool(unknown_evidence),
        "projected_benefit": _text("projected_benefit", projected_benefit,
                                   _LIMITS["projected_benefit"]),
        "rollback_criteria": _text("rollback_criteria", rollback_criteria,
                                   _LIMITS["rollback_criteria"]),
        "expiry": _text("expiry", expiry, _LIMITS["expiry"]),
        "detail": _detail(kind, normalized_target["path"], detail),
    }


def validate_adjustment(payload):
    """Re-validate a built payload mapping; returns its normalized copy."""
    if not isinstance(payload, dict):
        raise ValueError("adjustment payload must be a mapping")
    if payload.get("schema") != SCHEMA_VERSION:
        raise ValueError("adjustment schema version mismatch")
    return build_adjustment(
        payload.get("kind"),
        origin=payload.get("origin"),
        target=payload.get("target"),
        detail=payload.get("detail"),
        evidence_refs=payload.get("evidence_refs", ()),
        unknown_evidence=payload.get("unknown_evidence", False),
        projected_benefit=payload.get("projected_benefit"),
        rollback_criteria=payload.get("rollback_criteria"),
        expiry=payload.get("expiry"),
    )


__all__ = [
    "ADJUSTMENT_KINDS",
    "ADJUSTMENT_SCHEMA",
    "AUTHORIZED_SKILLS",
    "AUTHORIZED_STATEWORKS",
    "MAX_BASIS_REVISIONS",
    "MAX_EVIDENCE_REFS",
    "RUNTIME_CONTROLS",
    "SCHEMA_VERSION",
    "build_adjustment",
    "validate_adjustment",
]
