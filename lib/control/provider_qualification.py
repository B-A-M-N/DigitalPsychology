"""Versioned per-family provider qualification snapshots for selector binding.

The control engine must never infer qualification from the mere presence of
a calibration hash. Qualification is resolved per (provider, question-family)
against the calibration bundle carried by each System-1 runtime, and the
result is bound to a versioned snapshot id::

    snapshot_id = sha256(version | provider | question_id |
                         calibration_digest | model_revision)

The selector memoizes only while the snapshot id still matches; any change
(calibration rotation, model revision change, different question family)
rebuilds the selector instead of serving memoized state. ``unknown`` stays
unqualified (fail closed) when translated for the selector.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping

QUALIFIED = "qualified"
UNQUALIFIED = "unqualified"
UNKNOWN = "unknown"

STATUSES = frozenset({QUALIFIED, UNQUALIFIED, UNKNOWN})

#: Version tag mixed into every snapshot id so snapshot formats can evolve.
SNAPSHOT_VERSION = "provider-qualification/v1"


@dataclass(frozen=True)
class ProviderQualification:
    """Qualification of one provider for one question family."""

    provider: str
    question_id: str
    status: str
    snapshot_id: str
    calibration_digest: str
    model_revision: str
    reason: str

    def __post_init__(self) -> None:
        if self.provider not in {"laya", "jev", "unknown"}:
            raise ValueError("provider qualification names an unsupported provider")
        if not isinstance(self.question_id, str) or not self.question_id:
            raise ValueError("provider qualification requires a question family")
        if self.status not in STATUSES:
            raise ValueError("provider qualification status is unsupported")
        if not isinstance(self.snapshot_id, str) or not self.snapshot_id:
            raise ValueError("provider qualification requires a snapshot id")
        if len(self.reason) > 256 or not self.reason:
            raise ValueError("provider qualification reason must be bounded non-empty text")


@dataclass(frozen=True)
class PortfolioQualification:
    """Bound qualification for every known provider plus the snapshot id."""

    entries: tuple[ProviderQualification, ...]
    snapshot_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot_id, str) or not self.snapshot_id:
            raise ValueError("portfolio qualification requires a snapshot id")

    @property
    def selector_qualification(self) -> dict[str, str]:
        """Fail-closed mapping for the advisory selector.

        Only ``qualified`` survives; ``unknown`` and ``unqualified`` both
        map to ``unqualified`` so the selector never hears an unvetted
        provider.
        """
        return {
            entry.provider: (
                QUALIFIED if entry.status == QUALIFIED else UNQUALIFIED
            )
            for entry in self.entries
            if entry.provider in {"laya", "jev"}
        }


def provider_key_for_backend(backend_id: Any) -> str | None:
    """Map a runtime backend id to the selector provider key, if recognized."""
    text = str(backend_id or "")
    if "laya" in text:
        return "laya"
    if "jev" in text:
        return "jev"
    return None


def snapshot_id_for(*, provider: str, question_id: str,
                    calibration_digest: str, model_revision: str) -> str:
    """Bind (provider, question family, calibration digest, model revision)."""
    payload = {
        "version": SNAPSHOT_VERSION,
        "provider": provider,
        "question_id": question_id,
        "calibration_digest": calibration_digest,
        "model_revision": model_revision,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"),
                   ensure_ascii=False).encode("utf-8")).hexdigest()


def _runtime_text(runtime: Any, *names: str) -> str:
    for name in names:
        try:
            value = getattr(runtime, name, "")
        except Exception:
            continue
        if isinstance(value, str) and value:
            return value
    return ""


def _backend_text(runtime: Any, *names: str) -> str:
    try:
        backend = getattr(runtime, "backend", None)
    except Exception:
        backend = None
    if backend is None:
        return ""
    for name in names:
        try:
            value = getattr(backend, name, "")
        except Exception:
            continue
        if isinstance(value, str) and value:
            return value
    capability = getattr(backend, "capability", None)
    if callable(capability):
        try:
            offered = capability()
        except Exception:
            offered = None
        if isinstance(offered, Mapping):
            for name in names:
                value = offered.get(name, "")
                if isinstance(value, str) and value:
                    return value
        else:
            for name in names:
                try:
                    value = getattr(offered, name, "")
                except Exception:
                    continue
                if isinstance(value, str) and value:
                    return value
    return ""


def _runtime_model_revision(runtime: Any) -> str:
    return _runtime_text(runtime, "model_revision") or _backend_text(
        runtime, "model_revision")


def _runtime_model_id(runtime: Any) -> str:
    return _runtime_text(runtime, "model_id") or _backend_text(runtime, "model_id")


def resolve_provider_qualification(runtime: Any, question_id: str) -> ProviderQualification:
    """Resolve one (provider, question-family) qualification entry.

    Fail-closed rules: an unrecognized backend, a missing calibration
    digest, a missing record for the family, or a record that does not pass
    its own gate (inactive, drifted, revision/identity mismatch) never
    qualifies. Artifact existence alone -- a non-empty calibration hash --
    is never sufficient.
    """
    backend_id = _runtime_text(runtime, "backend_id")
    provider = provider_key_for_backend(backend_id)
    provider_label = provider or "unknown"
    try:
        bundle = getattr(runtime, "calibration_bundle", None)
    except Exception:
        bundle = None
    try:
        digest = getattr(bundle, "digest", "") or ""
    except Exception:
        digest = ""
    if not isinstance(digest, str):
        digest = ""
    revision = _runtime_model_revision(runtime)
    model_id = _runtime_model_id(runtime)
    snapshot_id = snapshot_id_for(
        provider=provider_label, question_id=str(question_id),
        calibration_digest=digest, model_revision=revision)

    def entry(status: str, reason: str) -> ProviderQualification:
        return ProviderQualification(
            provider=provider_label, question_id=str(question_id),
            status=status, snapshot_id=snapshot_id,
            calibration_digest=digest, model_revision=revision,
            reason=reason)

    if provider is None:
        return entry(UNKNOWN, "unrecognized provider backend")
    if not digest:
        return entry(UNQUALIFIED, "calibration digest is missing")
    records = None
    try:
        records = getattr(bundle, "records", None)
    except Exception:
        records = None
    record = None
    if isinstance(records, Mapping):
        try:
            record = records.get(question_id)
        except Exception:
            record = None
    if record is None:
        return entry(UNKNOWN, "no calibration record for question family")
    try:
        status = getattr(record, "status", "")
        drift = getattr(record, "drift_status", "")
        passed = bool(getattr(record, "qualification_passed", False))
        record_revision = getattr(record, "model_revision", "")
        record_backend = getattr(record, "backend_id", "")
        record_model = getattr(record, "model_id", "")
    except Exception:
        return entry(UNQUALIFIED, "calibration record is unreadable")
    if status != "active":
        return entry(UNQUALIFIED, "calibration record is not active")
    if drift != "clear":
        return entry(UNQUALIFIED, "calibration record shows drift")
    if not passed:
        return entry(UNQUALIFIED, "calibration record fails its qualification gate")
    if not revision or record_revision != revision:
        return entry(UNQUALIFIED, "calibration model revision mismatch")
    if record_backend != backend_id:
        return entry(UNQUALIFIED, "calibration backend identity mismatch")
    if model_id and record_model != model_id:
        return entry(UNQUALIFIED, "calibration model identity mismatch")
    return entry(QUALIFIED, "active passing calibration for question family")


def resolve_portfolio_qualification(runtimes: Any,
                                    question_id: Any) -> PortfolioQualification:
    """Resolve every runtime for one question family and bind the snapshot."""
    try:
        candidates = tuple(runtimes or ())
    except TypeError:
        candidates = ()
    entries = tuple(
        resolve_provider_qualification(runtime, str(question_id or ""))
        for runtime in candidates)
    combined = hashlib.sha256(
        json.dumps({
            "version": SNAPSHOT_VERSION,
            "question_id": str(question_id or ""),
            "snapshots": sorted(item.snapshot_id for item in entries),
        }, sort_keys=True, separators=(",", ":"),
                   ensure_ascii=False).encode("utf-8")).hexdigest()
    return PortfolioQualification(entries=entries, snapshot_id=combined)


__all__ = [
    "QUALIFIED",
    "UNQUALIFIED",
    "UNKNOWN",
    "STATUSES",
    "SNAPSHOT_VERSION",
    "ProviderQualification",
    "PortfolioQualification",
    "provider_key_for_backend",
    "snapshot_id_for",
    "resolve_provider_qualification",
    "resolve_portfolio_qualification",
]
