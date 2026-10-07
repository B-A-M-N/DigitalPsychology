"""Harness-neutral aggregation of host validator evidence for completion."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any


MAX_REQUIRED_CRITERIA = 256
MAX_VALIDATORS = 64
MAX_COMPLETION_REF_LENGTH = 256
MAX_COMPLETION_AUTHORITIES = 32
MAX_COMPLETION_REASON_CODES = 128
MAX_AUTHORITY_REASON_CODES = 32


def _unique_ids(values: Sequence[str], name: str, maximum: int) -> tuple[str, ...]:
    if (not isinstance(values, (tuple, list)) or len(values) > maximum
            or any(not isinstance(value, str) or not value.strip() or len(value) > 128
                   for value in values)
            or len(values) != len(set(values))):
        raise ValueError(f"{name} must be unique bounded identifiers")
    return tuple(values)


@dataclass(frozen=True)
class ValidatorCompletionEvidence:
    """Bounded host outcome from one registered validator invocation."""

    validator_id: str
    criterion_ids: tuple[str, ...]
    passed: bool
    evidence_ref: str | None = None
    result_digest: str | None = None
    invocation_id: str | None = None

    def __post_init__(self) -> None:
        if (not isinstance(self.validator_id, str) or not self.validator_id.strip()
                or len(self.validator_id) > 128):
            raise ValueError("validator_id must be bounded non-empty text")
        object.__setattr__(self, "criterion_ids", _unique_ids(
            self.criterion_ids, "validator criterion_ids", MAX_REQUIRED_CRITERIA))
        if type(self.passed) is not bool:
            raise ValueError("validator passed must be boolean")
        if self.evidence_ref is not None and (
                not isinstance(self.evidence_ref, str) or not self.evidence_ref.strip()
                or len(self.evidence_ref) > MAX_COMPLETION_REF_LENGTH):
            raise ValueError("validator evidence_ref must be bounded non-empty text")
        if self.result_digest is not None and (
                not isinstance(self.result_digest, str) or len(self.result_digest) != 64
                or any(char not in "0123456789abcdef" for char in self.result_digest)):
            raise ValueError("validator result_digest must be a lowercase SHA-256 digest")
        if self.invocation_id is not None and (
                not isinstance(self.invocation_id, str) or not self.invocation_id.strip()
                or len(self.invocation_id) > 256):
            raise ValueError("validator invocation_id must be bounded non-empty text")
        if self.passed and not all((self.evidence_ref, self.result_digest,
                                    self.invocation_id, self.criterion_ids)):
            raise ValueError("passing validator evidence requires refs, digest, invocation, and criteria")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ValidatorCompletionEvidence":
        if not isinstance(value, Mapping):
            raise TypeError("validator outcome must be a mapping")
        return cls(
            validator_id=value.get("validator_id"),
            criterion_ids=tuple(value.get("criterion_ids") or ()),
            passed=value.get("passed") is True,
            evidence_ref=value.get("evidence_ref") or None,
            result_digest=value.get("result_digest") or None,
            invocation_id=value.get("invocation_id") or None,
        )


@dataclass(frozen=True)
class CompletionAggregation:
    """Decision-neutral summary; a harness still asks its authority to finalize."""

    ready: bool
    reason: str
    required_criterion_ids: tuple[str, ...]
    missing_criterion_ids: tuple[str, ...]
    expected_validator_ids: tuple[str, ...]
    missing_validator_ids: tuple[str, ...]
    failed_validator_ids: tuple[str, ...]
    criterion_evidence_refs: Mapping[str, tuple[str, ...]]

    def __post_init__(self) -> None:
        if type(self.ready) is not bool or not isinstance(self.reason, str):
            raise ValueError("completion aggregation status is invalid")
        object.__setattr__(self, "required_criterion_ids", _unique_ids(
            self.required_criterion_ids, "required criterion IDs", MAX_REQUIRED_CRITERIA))
        object.__setattr__(self, "missing_criterion_ids", _unique_ids(
            self.missing_criterion_ids, "missing criterion IDs", MAX_REQUIRED_CRITERIA))
        object.__setattr__(self, "expected_validator_ids", _unique_ids(
            self.expected_validator_ids, "expected validator IDs", MAX_VALIDATORS))
        object.__setattr__(self, "missing_validator_ids", _unique_ids(
            self.missing_validator_ids, "missing validator IDs", MAX_VALIDATORS))
        object.__setattr__(self, "failed_validator_ids", _unique_ids(
            self.failed_validator_ids, "failed validator IDs", MAX_VALIDATORS))
        refs: dict[str, tuple[str, ...]] = {}
        for criterion_id, raw_refs in self.criterion_evidence_refs.items():
            if criterion_id not in self.required_criterion_ids:
                raise ValueError("completion evidence escapes the required criterion set")
            values = tuple(raw_refs)
            if (not values or len(values) > MAX_VALIDATORS
                    or any(not isinstance(ref, str) or not ref.strip()
                           or len(ref) > MAX_COMPLETION_REF_LENGTH for ref in values)
                    or len(values) != len(set(values))):
                raise ValueError("completion evidence refs must be unique bounded text")
            refs[criterion_id] = values
        object.__setattr__(self, "criterion_evidence_refs", MappingProxyType(refs))

    @property
    def validated_criterion_count(self) -> int:
        return len(set(self.required_criterion_ids) - set(self.missing_criterion_ids))

    def to_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "reason": self.reason,
            "required_criterion_ids": list(self.required_criterion_ids),
            "missing_criterion_ids": list(self.missing_criterion_ids),
            "expected_validator_ids": list(self.expected_validator_ids),
            "missing_validator_ids": list(self.missing_validator_ids),
            "failed_validator_ids": list(self.failed_validator_ids),
            "criterion_evidence_refs": {
                key: list(value) for key, value in self.criterion_evidence_refs.items()},
        }


@dataclass(frozen=True)
class CompletionAuthorityReceipt:
    """One subsystem's host-attested completion allow or veto."""

    authority_id: str
    allowed: bool
    revision: str | None = None
    evidence_refs: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (not isinstance(self.authority_id, str) or not self.authority_id.strip()
                or len(self.authority_id) > 128):
            raise ValueError("completion authority_id must be bounded non-empty text")
        if type(self.allowed) is not bool:
            raise ValueError("completion authority allowed must be boolean")
        if self.revision is not None and (
                not isinstance(self.revision, str) or not self.revision.strip()
                or len(self.revision) > 256):
            raise ValueError("completion authority revision must be bounded non-empty text")
        refs = tuple(self.evidence_refs)
        if (len(refs) > MAX_VALIDATORS
                or any(not isinstance(ref, str) or not ref.strip()
                       or len(ref) > MAX_COMPLETION_REF_LENGTH for ref in refs)
                or len(refs) != len(set(refs))):
            raise ValueError("completion authority evidence refs must be unique bounded text")
        reasons = tuple(self.reason_codes)
        if (len(reasons) > MAX_AUTHORITY_REASON_CODES
                or any(not isinstance(reason, str) or not reason.strip()
                       or len(reason) > 128 for reason in reasons)
                or len(reasons) != len(set(reasons))):
            raise ValueError("completion authority reason codes must be unique bounded text")
        object.__setattr__(self, "evidence_refs", refs)
        object.__setattr__(self, "reason_codes", reasons)

    def to_dict(self) -> dict[str, Any]:
        return {"authority_id": self.authority_id, "allowed": self.allowed,
                "revision": self.revision,
                "evidence_refs": list(self.evidence_refs),
                "reason_codes": list(self.reason_codes)}

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "CompletionAuthorityReceipt":
        required = {"authority_id", "allowed"}
        optional = {"revision", "evidence_refs", "reason_codes"}
        if (not isinstance(value, Mapping) or not required.issubset(value)
                or not set(value).issubset(required | optional)):
            raise ValueError("completion authority receipt fields are invalid")
        evidence_refs = value.get("evidence_refs", ())
        reason_codes = value.get("reason_codes", ())
        if not isinstance(evidence_refs, (tuple, list)) or not isinstance(
                reason_codes, (tuple, list)):
            raise ValueError("completion authority receipt sequences are invalid")
        return cls(
            authority_id=value["authority_id"], allowed=value["allowed"],
            revision=value.get("revision"),
            evidence_refs=tuple(evidence_refs),
            reason_codes=tuple(reason_codes),
        )


@dataclass(frozen=True)
class CompletionDecision:
    """Host-runtime completion result after authority and evidence aggregation."""

    allowed: bool
    authority_receipts: tuple[CompletionAuthorityReceipt, ...]
    evidence_refs: tuple[str, ...]
    reason_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.allowed) is not bool:
            raise ValueError("completion decision allowed must be boolean")
        receipts = tuple(self.authority_receipts)
        if (len(receipts) > MAX_COMPLETION_AUTHORITIES
                or any(not isinstance(item, CompletionAuthorityReceipt)
                       for item in receipts)
                or len({item.authority_id for item in receipts}) != len(receipts)):
            raise ValueError("completion decision authority receipts are invalid")
        refs = tuple(self.evidence_refs)
        if (len(refs) > (MAX_COMPLETION_AUTHORITIES + 1) * MAX_VALIDATORS
                or any(not isinstance(ref, str) or not ref.strip()
                       or len(ref) > MAX_COMPLETION_REF_LENGTH for ref in refs)
                or len(refs) != len(set(refs))):
            raise ValueError("completion decision evidence refs must be unique bounded text")
        reasons = tuple(self.reason_codes)
        if (len(reasons) > MAX_COMPLETION_REASON_CODES
                or any(not isinstance(reason, str) or not reason.strip()
                       or len(reason) > 128 for reason in reasons)
                or len(reasons) != len(set(reasons))):
            raise ValueError("completion decision reason codes must be unique bounded text")
        object.__setattr__(self, "authority_receipts", receipts)
        object.__setattr__(self, "evidence_refs", refs)
        object.__setattr__(self, "reason_codes", reasons)
        if self.allowed and any(not item.allowed for item in receipts):
            raise ValueError("an allowed completion decision cannot include a veto receipt")
        if self.allowed and reasons:
            raise ValueError("an allowed completion decision cannot contain reason codes")
        if not self.allowed and not reasons:
            raise ValueError("a denied completion decision requires reason codes")

    def to_dict(self) -> dict[str, Any]:
        return {"allowed": self.allowed,
                "authority_receipts": [item.to_dict()
                                       for item in self.authority_receipts],
                "evidence_refs": list(self.evidence_refs),
                "reason_codes": list(self.reason_codes)}


def aggregate_completion_decision(
        required_authority_ids: Sequence[str],
        authority_receipts: Sequence[CompletionAuthorityReceipt], *,
        validation: CompletionAggregation) -> CompletionDecision:
    """Fail closed across host-selected authorities and validator evidence.

    This function does not decide which authorities apply. The connected host
    supplies that requirement set, collects their receipts, and remains
    responsible for checking receipt freshness and identity binding.
    """
    required = _unique_ids(required_authority_ids, "required authority IDs",
                           MAX_COMPLETION_AUTHORITIES)
    if not isinstance(validation, CompletionAggregation):
        raise TypeError("completion decision requires a validator evidence aggregation")
    if len(authority_receipts) > MAX_COMPLETION_AUTHORITIES:
        raise ValueError("completion authority receipt set exceeds its bound")
    receipts = tuple(authority_receipts)
    if any(not isinstance(item, CompletionAuthorityReceipt) for item in receipts):
        raise TypeError("completion authority receipts must be typed receipts")
    by_id = {item.authority_id: item for item in receipts}
    if len(by_id) != len(receipts):
        raise ValueError("completion authority receipt set contains duplicates")

    reasons: list[str] = []
    if not required:
        reasons.append("authority_set_empty")
    for authority_id in required:
        if authority_id not in by_id:
            reasons.append(f"authority_receipt_missing:{authority_id}")
    for receipt in receipts:
        if not receipt.allowed:
            reasons.extend(receipt.reason_codes or
                           (f"authority_denied:{receipt.authority_id}",))
    if not validation.ready:
        reasons.append(f"validator_evidence:{validation.reason}")
        reasons.extend(f"validator_missing:{item}"
                       for item in validation.missing_validator_ids)
        reasons.extend(f"validator_failed:{item}"
                       for item in validation.failed_validator_ids)
        reasons.extend(f"criterion_evidence_missing:{item}"
                       for item in validation.missing_criterion_ids)

    refs: list[str] = []
    for receipt in receipts:
        refs.extend(receipt.evidence_refs)
    for criterion_refs in validation.criterion_evidence_refs.values():
        refs.extend(criterion_refs)
    unique_refs = tuple(dict.fromkeys(refs))
    unique_reasons_list = list(dict.fromkeys(reasons))
    if len(unique_reasons_list) > MAX_COMPLETION_REASON_CODES:
        unique_reasons_list = unique_reasons_list[:MAX_COMPLETION_REASON_CODES - 1]
        unique_reasons_list.append("reason_codes_truncated")
    unique_reasons = tuple(unique_reasons_list)
    return CompletionDecision(
        allowed=not unique_reasons,
        authority_receipts=tuple(sorted(receipts, key=lambda item: item.authority_id)),
        evidence_refs=unique_refs,
        reason_codes=unique_reasons,
    )


def aggregate_completion(required_criterion_ids: Sequence[str],
                         expected_validator_ids: Sequence[str],
                         validator_outcomes: Sequence[
                             ValidatorCompletionEvidence | Mapping[str, Any]
                         ]) -> CompletionAggregation:
    """Aggregate required criteria and validator outcomes without granting permits.

    Every expected validator must report and pass, and every required criterion
    must have at least one passing evidence reference. The caller remains
    responsible for checking evidence freshness and requesting final approval
    from its authoritative host.
    """
    required = _unique_ids(required_criterion_ids, "required criterion IDs",
                           MAX_REQUIRED_CRITERIA)
    expected = _unique_ids(expected_validator_ids, "expected validator IDs", MAX_VALIDATORS)
    if len(validator_outcomes) > MAX_VALIDATORS:
        raise ValueError("validator outcome set exceeds its bound")
    outcomes = tuple(item if isinstance(item, ValidatorCompletionEvidence)
                     else ValidatorCompletionEvidence.from_mapping(item)
                     for item in validator_outcomes)
    by_id: dict[str, ValidatorCompletionEvidence] = {}
    for outcome in outcomes:
        if outcome.validator_id not in expected:
            raise ValueError("validator outcome is not in the expected validator set")
        if outcome.validator_id in by_id:
            raise ValueError("validator outcome set contains a duplicate validator")
        by_id[outcome.validator_id] = outcome

    missing_validators = tuple(item for item in expected if item not in by_id)
    failed_validators = tuple(item for item in expected
                              if item in by_id and not by_id[item].passed)
    refs: dict[str, list[str]] = {criterion_id: [] for criterion_id in required}
    for outcome in outcomes:
        if not outcome.passed or outcome.evidence_ref is None:
            continue
        for criterion_id in outcome.criterion_ids:
            if criterion_id in refs:
                refs[criterion_id].append(outcome.evidence_ref)
    evidence = {criterion_id: tuple(dict.fromkeys(values))
                for criterion_id, values in refs.items() if values}
    missing_criteria = tuple(item for item in required if item not in evidence)
    if not required:
        reason = "no_required_criteria"
    elif failed_validators:
        reason = "validator_failed"
    elif missing_validators:
        reason = "validator_outcome_missing"
    elif missing_criteria:
        reason = "required_criteria_missing"
    else:
        reason = "ready_for_host_finalization"
    ready = bool(required) and not (
        failed_validators or missing_validators or missing_criteria)
    return CompletionAggregation(
        ready=ready, reason=reason,
        required_criterion_ids=required,
        missing_criterion_ids=missing_criteria,
        expected_validator_ids=expected,
        missing_validator_ids=missing_validators,
        failed_validator_ids=failed_validators,
        criterion_evidence_refs=evidence,
    )


__all__ = ["CompletionAggregation", "CompletionAuthorityReceipt",
           "CompletionDecision", "ValidatorCompletionEvidence",
           "aggregate_completion", "aggregate_completion_decision"]
