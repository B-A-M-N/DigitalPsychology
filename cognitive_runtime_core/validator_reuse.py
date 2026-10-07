"""Harness-neutral reuse of fresh, evidence-bound validator results.

The runtime stores only bounded validation results. Hosts supply the workspace
digest and evidence freshness check because those are properties of the
connected harness and evidence authority, not of the behavioral controller.
"""
from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
from typing import Any

import jsonschema


MAX_CACHED_VALIDATORS = 128
MAX_VALIDATION_RESULT_BYTES = 262_144
MAX_CRITERION_IDS = 64
MAX_EVIDENCE_REF_LENGTH = 256


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _digest(value: Any, name: str) -> str:
    if (not isinstance(value, str) or len(value) != 64
            or any(char not in "0123456789abcdef" for char in value)):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return value


def _criteria(values: Sequence[str]) -> tuple[str, ...]:
    if (not isinstance(values, (tuple, list)) or not values
            or len(values) > MAX_CRITERION_IDS
            or any(not isinstance(value, str) or not value.strip() or len(value) > 128
                   for value in values)
            or len(values) != len(set(values))):
        raise ValueError("validator criterion IDs must be unique bounded text")
    return tuple(values)


@dataclass(frozen=True)
class ReusableValidation:
    """A prior host validation that still matches current artifact and evidence."""

    validator_id: str
    criterion_ids: tuple[str, ...]
    invocation_id: str
    evidence_ref: str
    passed: bool
    result: Mapping[str, Any]
    result_digest: str
    reuse_basis_digest: str
    reused: bool = True


@dataclass(frozen=True)
class _ValidationEpoch:
    artifact_digest: str
    validator_contract_digest: str
    validator_id: str
    criterion_ids: tuple[str, ...]
    evidence_ref: str
    result_digest: str
    invocation_id: str
    passed: bool
    result_json: str


class ValidatorReuseCache:
    """Reuse a result only while its code contract, artifact, and evidence match.

    ``execution_contract_digest`` should pin host-specific executable details
    such as executor code, action schema, task contract, and tool authority.
    ``evidence_is_current`` must be backed by the host's evidence authority.
    """

    def __init__(self, *, max_entries: int = MAX_CACHED_VALIDATORS) -> None:
        if type(max_entries) is not int or not 1 <= max_entries <= MAX_CACHED_VALIDATORS:
            raise ValueError("validator cache capacity is outside its bound")
        self.max_entries = max_entries
        self._epochs: OrderedDict[str, _ValidationEpoch] = OrderedDict()

    @staticmethod
    def _binding_digest(binding: Any, execution_contract_digest: str) -> str:
        execution_contract_digest = _digest(
            execution_contract_digest, "execution_contract_digest")
        arguments = binding.arguments_value()
        result_schema = binding.result_schema_value()
        contract = {
            "validator_id": binding.validator_id,
            "action_binding_id": binding.action_binding_id,
            "arguments": arguments,
            "criterion_ids": list(_criteria(binding.criterion_ids)),
            "result_schema": result_schema,
            "covered_boundary_ids": list(binding.covered_boundary_ids),
            "execution_contract_digest": execution_contract_digest,
        }
        return hashlib.sha256(_canonical_bytes(contract)).hexdigest()

    @staticmethod
    def reuse_basis_digest(*, artifact_digest: str,
                           validator_contract_digest: str,
                           criterion_ids: Sequence[str], evidence_ref: str,
                           result_digest: str) -> str:
        basis = {
            "artifact_digest": _digest(artifact_digest, "artifact_digest"),
            "validator_contract_digest": _digest(
                validator_contract_digest, "validator_contract_digest"),
            "criterion_ids": list(_criteria(criterion_ids)),
            "evidence_ref": evidence_ref,
            "result_digest": _digest(result_digest, "result_digest"),
        }
        if (not isinstance(evidence_ref, str) or not evidence_ref.strip()
                or len(evidence_ref) > MAX_EVIDENCE_REF_LENGTH):
            raise ValueError("evidence_ref must be bounded non-empty text")
        return hashlib.sha256(_canonical_bytes(basis)).hexdigest()

    def reusable(self, binding: Any, *, artifact_digest: str | None,
                 execution_contract_digest: str,
                 evidence_is_current: Callable[[str], bool],
                 validate_result: Callable[[Mapping[str, Any]], bool] | None = None
                 ) -> ReusableValidation | None:
        """Return a still-valid cache entry, or ``None`` on any uncertainty."""
        if artifact_digest is None:
            return None
        try:
            artifact_digest = _digest(artifact_digest, "artifact_digest")
            contract_digest = self._binding_digest(
                binding, execution_contract_digest)
        except (AttributeError, TypeError, ValueError):
            return None
        validator_id = getattr(binding, "validator_id", None)
        if not isinstance(validator_id, str) or not validator_id:
            return None
        epoch = self._epochs.get(validator_id)
        if (epoch is None or epoch.artifact_digest != artifact_digest
                or epoch.validator_contract_digest != contract_digest
                or epoch.criterion_ids != tuple(binding.criterion_ids)):
            return None
        try:
            if evidence_is_current(epoch.evidence_ref) is not True:
                return None
            result = json.loads(epoch.result_json)
            if (not isinstance(result, Mapping)
                    or hashlib.sha256(_canonical_bytes(result)).hexdigest()
                    != epoch.result_digest):
                return None
            schema = binding.result_schema_value()
            jsonschema.validators.validator_for(schema)(schema).validate(result)
            result_valid = (validate_result(result) is True
                            if validate_result is not None else True)
        except Exception:
            return None
        # A passing result must still satisfy its registered semantics. Failed
        # outcomes may be reused to preserve a known failure without rerunning.
        if epoch.passed and not result_valid:
            return None
        self._epochs.move_to_end(validator_id)
        return ReusableValidation(
            validator_id=epoch.validator_id,
            criterion_ids=epoch.criterion_ids,
            invocation_id=epoch.invocation_id,
            evidence_ref=epoch.evidence_ref,
            passed=epoch.passed,
            result=dict(result),
            result_digest=epoch.result_digest,
            reuse_basis_digest=self.reuse_basis_digest(
                artifact_digest=epoch.artifact_digest,
                validator_contract_digest=epoch.validator_contract_digest,
                criterion_ids=epoch.criterion_ids,
                evidence_ref=epoch.evidence_ref,
                result_digest=epoch.result_digest),
        )

    def remember(self, binding: Any, *, artifact_digest_before: str | None,
                 artifact_digest_after: str | None,
                 execution_contract_digest: str, invocation_id: str,
                 result: Mapping[str, Any], result_digest: str,
                 evidence_ref: str, passed: bool,
                 evidence_is_current: Callable[[str], bool]) -> bool:
        """Store only if the artifact stayed fixed and the evidence is current."""
        if artifact_digest_before is None or artifact_digest_after != artifact_digest_before:
            return False
        try:
            artifact_digest = _digest(artifact_digest_before, "artifact_digest")
            contract_digest = self._binding_digest(
                binding, execution_contract_digest)
            result_digest = _digest(result_digest, "result_digest")
            if (not isinstance(invocation_id, str) or not invocation_id.strip()
                    or len(invocation_id) > 256
                    or not isinstance(evidence_ref, str) or not evidence_ref.strip()
                    or len(evidence_ref) > MAX_EVIDENCE_REF_LENGTH
                    or type(passed) is not bool or not isinstance(result, Mapping)):
                return False
            body = _canonical_bytes(dict(result))
            if (len(body) > MAX_VALIDATION_RESULT_BYTES
                    or hashlib.sha256(body).hexdigest() != result_digest
                    or evidence_is_current(evidence_ref) is not True):
                return False
            # Require a JSON object that still satisfies the registered result schema.
            schema = binding.result_schema_value()
            jsonschema.validators.validator_for(schema)(schema).validate(result)
            validator_id = binding.validator_id
            if not isinstance(validator_id, str) or not validator_id:
                return False
            epoch = _ValidationEpoch(
                artifact_digest=artifact_digest,
                validator_contract_digest=contract_digest,
                validator_id=validator_id,
                criterion_ids=_criteria(binding.criterion_ids),
                evidence_ref=evidence_ref,
                result_digest=result_digest,
                invocation_id=invocation_id,
                passed=passed,
                result_json=body.decode("utf-8"),
            )
        except Exception:
            return False
        self._epochs[validator_id] = epoch
        self._epochs.move_to_end(validator_id)
        while len(self._epochs) > self.max_entries:
            self._epochs.popitem(last=False)
        return True

    def clear(self, validator_id: str | None = None) -> None:
        if validator_id is None:
            self._epochs.clear()
        else:
            self._epochs.pop(validator_id, None)


__all__ = ["ReusableValidation", "ValidatorReuseCache"]
