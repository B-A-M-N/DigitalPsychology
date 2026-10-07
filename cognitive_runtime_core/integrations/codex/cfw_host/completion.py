"""Validator execution and evidence-bound completion gate."""
from __future__ import annotations

import hashlib
import inspect
import json
import marshal
import os
import secrets
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import jsonschema
from cognitive_runtime_core.completion import (
    CompletionAuthorityReceipt,
    aggregate_completion,
    aggregate_completion_decision,
)
from cognitive_runtime_core.harness import CompletionProposal
from runtime_contract.validators import evaluate_validator
from runtime_session.lifecycle import GateDecision

from .protocol import (
    MAX_TOOL_OUTPUT_BYTES,
    CodexExecutionPhase,
    _goal_contract_data,
    _json_bytes,
    _safe_error,
    _safe_value,
)
from cognitive_runtime_core.validators import HostValidatorBinding

# External host and executor boundaries are deliberately fail-closed on any error.
# ruff: noqa: BLE001

class CodexCompletionMixin:
    _ARTIFACT_IGNORED_DIRS = frozenset({
        ".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
        ".tox", ".venv", "venv", "node_modules",
    })
    _ARTIFACT_MAX_FILES = 4096
    _ARTIFACT_MAX_BYTES = 128 * 1024 * 1024

    def _validation_artifact_digest(self) -> str | None:
        """Hash the task workspace while excluding repository and runtime caches."""
        root = Path(self.cwd).resolve()
        if not root.is_dir():
            return None
        digest = hashlib.sha256()
        file_count = 0
        total_bytes = 0
        try:
            for raw_dir, dirnames, filenames in os.walk(root, followlinks=False):
                relative_dir = Path(raw_dir).relative_to(root)
                dirnames[:] = sorted(
                    name for name in dirnames
                    if name not in self._ARTIFACT_IGNORED_DIRS
                    and not (relative_dir == Path("xdg-runtime")
                             and name == "cognitiveframeworks"))
                for name in sorted(filenames):
                    path = Path(raw_dir) / name
                    if path.is_symlink():
                        return None
                    before = path.stat(follow_symlinks=False)
                    if not stat.S_ISREG(before.st_mode):
                        continue
                    file_count += 1
                    total_bytes += before.st_size
                    if (file_count > self._ARTIFACT_MAX_FILES
                            or total_bytes > self._ARTIFACT_MAX_BYTES):
                        return None
                    content = path.read_bytes()
                    after = path.stat(follow_symlinks=False)
                    if (before.st_size != after.st_size
                            or before.st_mtime_ns != after.st_mtime_ns
                            or len(content) != after.st_size):
                        return None
                    digest.update(path.relative_to(root).as_posix().encode("utf-8"))
                    digest.update(b"\0")
                    digest.update(str(stat.S_IMODE(after.st_mode)).encode("ascii"))
                    digest.update(b"\0")
                    digest.update(content)
                    digest.update(b"\0")
        except OSError:
            return None
        return digest.hexdigest()

    def _validator_contract_hash(self, binding: HostValidatorBinding) -> str | None:
        action = self._action_bindings_by_id.get(binding.action_binding_id)
        executor = getattr(action, "executor", None)
        code = getattr(executor, "__code__", None)
        if action is None or code is None:
            return None
        try:
            executor_code_hash = hashlib.sha256(marshal.dumps(code)).hexdigest()
            goal_hash = str(self._task_handle.bundle.pinned.get(
                "goal_contract_hash") or "")
            contract = {
                "goal_contract_hash": goal_hash,
                "validator_id": binding.validator_id,
                "action_binding_id": binding.action_binding_id,
                "arguments": binding.arguments_value(),
                "result_schema": binding.result_schema_value(),
                "criterion_ids": list(binding.criterion_ids),
                "covered_boundary_ids": list(binding.covered_boundary_ids),
                "action_name": action.name,
                "action_schema": json.loads(action._schema_json),
                "action_tool_type": action.descriptor.tool_type,
                "action_goal_relation": action.descriptor.goal_relation,
                "executor_module": getattr(executor, "__module__", ""),
                "executor_qualname": getattr(executor, "__qualname__", ""),
                "executor_code_hash": executor_code_hash,
            }
            return hashlib.sha256(_json_bytes(contract)).hexdigest()
        except (AttributeError, TypeError, ValueError, OSError):
            return None

    def _current_validation_evidence(self, evidence_ref: str) -> bool:
        check = getattr(self._host, "evidence_is_current", None)
        if not callable(check):
            return False
        try:
            return check(evidence_ref) is True
        except Exception:
            return False

    def _reusable_validation_result(
            self, binding: HostValidatorBinding,
            artifact_digest: str | None) -> dict[str, Any] | None:
        contract_hash = self._validator_contract_hash(binding)
        if contract_hash is None:
            return None
        cached = self.host_runtime_bindings.validator_reuse_cache.reusable(
            binding, artifact_digest=artifact_digest,
            execution_contract_digest=contract_hash,
            evidence_is_current=self._current_validation_evidence,
            validate_result=lambda result: evaluate_validator(
                "completion-boundary-v1", result).passed)
        if cached is None:
            return None
        return {
            "passed": cached.passed,
            "validator_id": cached.validator_id,
            "criterion_ids": cached.criterion_ids,
            "invocation_id": cached.invocation_id,
            "evidence_ref": cached.evidence_ref,
            "result": dict(cached.result),
            "result_digest": cached.result_digest,
            "reuse_basis_digest": cached.reuse_basis_digest,
            "reused": True,
            "channel": "host_completion_reused",
            "elapsed_ms": 0.0,
        }

    def _remember_validation_result(
            self, binding: HostValidatorBinding, *,
            artifact_digest_before: str | None, invocation_id: str,
            result: Mapping[str, Any], result_digest: str,
            evidence_ref: str, passed: bool) -> None:
        contract_hash = self._validator_contract_hash(binding)
        if contract_hash is None:
            return
        self.host_runtime_bindings.validator_reuse_cache.remember(
            binding, artifact_digest_before=artifact_digest_before,
            artifact_digest_after=self._validation_artifact_digest(),
            execution_contract_digest=contract_hash,
            invocation_id=invocation_id, result=result,
            result_digest=result_digest, evidence_ref=evidence_ref,
            passed=bool(passed),
            evidence_is_current=self._current_validation_evidence)

    @staticmethod
    def _validator_summaries(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{
            "validator_id": str(item.get("validator_id") or "")[:96],
            "criterion_ids": list(item.get("criterion_ids") or ())[:64],
            "passed": item.get("passed") is True,
            "reused": item.get("reused") is True,
            "evidence_ref": str(item.get("evidence_ref") or "")[:256],
            "result_digest": str(item.get("result_digest") or "")[:64],
            "reuse_basis_digest": str(item.get("reuse_basis_digest") or "")[:64],
        } for item in results[:64]]

    async def _run_validator(self, binding: HostValidatorBinding) -> dict[str, Any]:
        """Execute one fixed host validator through the same action boundary."""
        self._validator_activity_observed = True
        self._advance_phase(CodexExecutionPhase.VALIDATION_OBSERVED)
        artifact_digest_before = self._validation_artifact_digest()
        reusable = self._reusable_validation_result(binding, artifact_digest_before)
        if reusable is not None:
            if self.codex_harness_adapter is not None:
                try:
                    boundary = await self.codex_harness_adapter.validation_completed(
                        validator_id=binding.validator_id,
                        criterion_ids=binding.criterion_ids,
                        evidence_refs=((str(reusable["evidence_ref"]),)
                                       if reusable.get("evidence_ref") else ()),
                        invocation_id=str(reusable.get("invocation_id") or ""),
                        passed=reusable.get("passed") is True)
                    if self._neutral_boundary_rejected(boundary):
                        return {"passed": False, "blocked": True,
                                "decision": "runtime_validation_blocked",
                                "validator_id": binding.validator_id}
                except Exception:
                    self._cognitive_fault = "neutral_validator_boundary_failed"
                    return {"passed": False, "blocked": True,
                            "decision": "host_boundary_failed",
                            "validator_id": binding.validator_id}
            return reusable
        action_binding = self._action_bindings_by_id[binding.action_binding_id]
        invocation_id = "codex-validator:" + hashlib.sha256(
            f"{self.request.task_id}:{binding.validator_id}:"
            f"{secrets.token_urlsafe(24)}".encode()).hexdigest()
        arguments = binding.arguments_value()
        subject_ref = self.request.subject_ref or self.request.task_id
        action = {"tool_binding_id": binding.action_binding_id,
                  "arguments": arguments, "subject_ref": subject_ref}
        prepared = self._host.prepare_tool(action)
        if prepared.decision != GateDecision.ALLOW:
            return {"passed": False, "blocked": True,
                    "decision": prepared.decision.value,
                    "validator_id": binding.validator_id}
        action_request_id = prepared.action_request_id
        if not isinstance(action_request_id, str) or not action_request_id:
            self._cognitive_fault = "validator_action_correlation_missing"
            return {"passed": False, "blocked": True,
                    "decision": "host_correlation_missing",
                    "validator_id": binding.validator_id}

        result_class = "failure"
        safe_result: dict[str, Any] = {
            "status": "failed", "failure_class": "validator_execution_error"}
        try:
            raw_result = action_binding.executor(arguments)
            if inspect.isawaitable(raw_result):
                raw_result = await raw_result
            if not isinstance(raw_result, Mapping):
                raise TypeError("validator executor must return a typed object")
            candidate = _safe_value(dict(raw_result))
            if (not isinstance(candidate, Mapping)
                    or len(_json_bytes(candidate)) > MAX_TOOL_OUTPUT_BYTES):
                raise ValueError("validator result exceeds the bounded object limit")
            safe_result = dict(candidate)
            schema = binding.result_schema_value()
            jsonschema.validators.validator_for(schema)(schema).validate(safe_result)
            if not evaluate_validator("completion-boundary-v1", safe_result).passed:
                raise ValueError("validator result did not pass the registered completion check")
            result_class = "success"
        except Exception as exc:
            if (isinstance(exc, (TypeError, ValueError, jsonschema.ValidationError))
                    and "raw_result" in locals()
                    and isinstance(raw_result, Mapping)):
                candidate = _safe_value(dict(raw_result))
                if (isinstance(candidate, Mapping)
                        and len(_json_bytes(candidate)) <= MAX_TOOL_OUTPUT_BYTES):
                    safe_result = dict(candidate)
            failure_class = ("validator_result_invalid" if isinstance(
                exc, (TypeError, ValueError, jsonschema.ValidationError))
                else "validator_execution_error")
            if not isinstance(safe_result.get("status"), str):
                safe_result = {"status": "failed", "failure_class": failure_class}

        result_digest = hashlib.sha256(_json_bytes(safe_result)).hexdigest()
        try:
            evidence_ref = self._host.after_tool(
                tool_type=action_binding.descriptor.tool_type,
                result_class=result_class, subject_ref=subject_ref,
                invocation_id=invocation_id, action_request_id=action_request_id,
                result_digest=result_digest, actual_result=safe_result,
                covered_boundary_ids=binding.covered_boundary_ids)
            self._host.complete_action()
        except Exception:
            self._cognitive_fault = "validator_evidence_failed"
            return {"passed": False, "blocked": True,
                    "decision": "host_evidence_failed",
                    "validator_id": binding.validator_id}

        self._remember_validation_result(
            binding, artifact_digest_before=artifact_digest_before,
            invocation_id=invocation_id, result=safe_result,
            result_digest=result_digest, evidence_ref=str(evidence_ref or ""),
            passed=result_class == "success")
        cached_result = self._reusable_validation_result(
            binding, artifact_digest_before)
        reuse_basis_digest = (str(cached_result.get("reuse_basis_digest") or "")
                              if cached_result is not None else "")
        if self.codex_harness_adapter is not None:
            try:
                boundary = await self.codex_harness_adapter.validation_completed(
                    validator_id=binding.validator_id,
                    criterion_ids=binding.criterion_ids,
                    evidence_refs=((str(evidence_ref),) if evidence_ref else ()),
                    invocation_id=invocation_id,
                    passed=result_class == "success")
                if self._neutral_boundary_rejected(boundary):
                    return {"passed": False, "blocked": True,
                            "decision": "runtime_validation_blocked",
                            "validator_id": binding.validator_id}
            except Exception:
                self._cognitive_fault = "neutral_validator_boundary_failed"
                return {"passed": False, "blocked": True,
                        "decision": "host_boundary_failed",
                        "validator_id": binding.validator_id}
        return {"passed": result_class == "success",
                "validator_id": binding.validator_id,
                "criterion_ids": binding.criterion_ids,
                "invocation_id": invocation_id,
                "evidence_ref": str(evidence_ref or ""),
                "result_digest": result_digest,
                "reuse_basis_digest": reuse_basis_digest,
                "result": safe_result}
    async def validate_completion(self, *,
                                  subject_ref: str | None = None) -> dict[str, Any]:
        if self._active_turn_id is not None or self._turn_completion is not None:
            return {"status": "incomplete", "decision": "turn_in_progress",
                    "task_id": self.request.task_id}
        async with self._completion_lock:
            if self._active_turn_id is not None or self._turn_completion is not None:
                return {"status": "incomplete", "decision": "turn_in_progress",
                        "task_id": self.request.task_id}
            async with self._action_lock:
                return await self._validate_completion_locked(subject_ref=subject_ref)
    async def _validate_completion_locked(self, *,
                                          subject_ref: str | None = None
                                          ) -> dict[str, Any]:
        """Run the host validator set and request CFW's evidence-bound permit."""
        self._completion_checked = True
        self._advance_phase(CodexExecutionPhase.COMPLETION_CHECKED)
        if self._host is None:
            raise RuntimeError("adapter must be open before completion validation")
        if self._cognitive_fault is not None:
            return {"status": "incomplete", "decision": "reconciliation_required",
                    "task_id": self.request.task_id}
        if not self.validator_bindings:
            return {"status": "incomplete", "decision": "host_validators_missing",
                    "task_id": self.request.task_id}

        results: list[dict[str, Any]] = []
        criterion_evidence_refs: dict[str, list[str]] = {}
        for validator_binding in self.validator_bindings:
            run = await self._run_validator(validator_binding)
            results.append(run)
            if run.get("blocked"):
                return {"status": "incomplete", "decision": run.get("decision"),
                        "task_id": self.request.task_id,
                        "validator_id": validator_binding.validator_id,
                        "validators": self._validator_summaries(results)}
            evidence_ref = run.get("evidence_ref")
            if isinstance(evidence_ref, str) and evidence_ref:
                for criterion_id in validator_binding.criterion_ids:
                    criterion_evidence_refs.setdefault(criterion_id, []).append(evidence_ref)
        required_criteria = {
            str(item.get("criterion_id"))
            for item in _goal_contract_data(self.request).get("acceptance_criteria", ())
            if isinstance(item, Mapping) and item.get("required", True)}
        validation = aggregate_completion(
            required_criterion_ids=tuple(sorted(required_criteria)),
            expected_validator_ids=tuple(
                binding.validator_id for binding in self.validator_bindings),
            validator_outcomes=tuple({
                "validator_id": item.get("validator_id"),
                "criterion_ids": tuple(item.get("criterion_ids") or ()),
                "passed": item.get("passed") is True,
                "evidence_ref": item.get("evidence_ref") or None,
                "result_digest": item.get("result_digest") or None,
                "invocation_id": item.get("invocation_id") or None,
            } for item in results))
        if not validation.ready:
            return {"status": "incomplete", "decision": "validator_failed",
                    "task_id": self.request.task_id,
                    "required_criteria": len(required_criteria),
                    "validated_criteria": validation.validated_criterion_count,
                    "validators": self._validator_summaries(results)}

        final_run = results[-1]
        final_result = final_run.get("result")
        if (not isinstance(final_result, Mapping)
                or not evaluate_validator("completion-boundary-v1",
                                          final_result).passed):
            return {"status": "incomplete", "decision": "completion_validator_failed",
                    "task_id": self.request.task_id,
                    "validator_id": final_run.get("validator_id"),
                    "validators": self._validator_summaries(results)}

        evidence = dict(validation.criterion_evidence_refs)
        runtime_receipts: list[CompletionAuthorityReceipt] = []
        if self.codex_harness_adapter is not None:
            goal_data = _goal_contract_data(self.request)
            proposal = CompletionProposal(
                proposal_id="codex-completion-" + hashlib.sha256(
                    self.request.task_id.encode("utf-8")).hexdigest()[:32],
                claim=str(goal_data.get("objective") or "complete host task"),
                required_criterion_ids=tuple(sorted(required_criteria)),
                expected_validator_ids=tuple(
                    binding.validator_id for binding in self.validator_bindings),
                validator_outcomes=tuple({
                    "validator_id": item["validator_id"],
                    "criterion_ids": tuple(item["criterion_ids"]),
                    "passed": item.get("passed") is True,
                    "evidence_ref": item.get("evidence_ref") or None,
                    "result_digest": item.get("result_digest") or None,
                    "invocation_id": item.get("invocation_id") or None,
                } for item in results),
                criterion_evidence_refs=evidence)
            try:
                boundary = await self.codex_harness_adapter.completion_proposed(proposal)
            except Exception:
                self._cognitive_fault = "neutral_completion_boundary_failed"
                return {"status": "incomplete", "decision": "host_boundary_failed",
                        "task_id": self.request.task_id,
                        "validators": self._validator_summaries(results)}
            if self._neutral_boundary_rejected(boundary):
                return {"status": "incomplete", "decision": "runtime_completion_blocked",
                        "task_id": self.request.task_id,
                        "validators": self._validator_summaries(results)}
            raw_receipts = boundary.get("authority_receipts", ())
            expected_runtime_authorities = set(
                self.required_completion_authority_ids) - {
                    "cfw-runtime", "csw-workflows"}
            if not isinstance(raw_receipts, (tuple, list)) or len(raw_receipts) > 32:
                return {"status": "incomplete", "decision": "runtime_completion_receipts_invalid",
                        "task_id": self.request.task_id,
                        "validators": self._validator_summaries(results)}
            try:
                runtime_receipts = [CompletionAuthorityReceipt.from_mapping(item)
                                    for item in raw_receipts]
            except (TypeError, ValueError):
                return {"status": "incomplete", "decision": "runtime_completion_receipts_invalid",
                        "task_id": self.request.task_id,
                        "validators": self._validator_summaries(results)}
            runtime_receipt_ids = {item.authority_id for item in runtime_receipts}
            if (len(runtime_receipt_ids) != len(runtime_receipts)
                    or not runtime_receipt_ids.issubset(expected_runtime_authorities)):
                return {"status": "incomplete", "decision": "runtime_completion_receipts_invalid",
                        "task_id": self.request.task_id,
                        "validators": self._validator_summaries(results)}
        try:
            decision = self._host.validate_completion_boundary(
                subject_ref=subject_ref or self.request.subject_ref or self.request.task_id,
                validator_id="completion-boundary-v1",
                invocation_id=str(final_run["invocation_id"]),
                result=dict(final_result), criterion_ids=tuple(evidence),
                criterion_evidence_refs=evidence)
        except Exception as exc:
            return {"status": "incomplete", "decision": "evidence_validation_failed",
                    "error_class": type(exc).__name__,
                    "task_id": self.request.task_id,
                    "validators": self._validator_summaries(results)}
        receipts = [CompletionAuthorityReceipt(
            authority_id="cfw-runtime",
            allowed=decision == GateDecision.ALLOW,
            reason_codes=(() if decision == GateDecision.ALLOW
                          else ("cfw_completion_denied",)),
        )]
        required_authority_ids = list(self.required_completion_authority_ids)
        receipts.extend(runtime_receipts)
        if "csw-workflows" in required_authority_ids:
            try:
                csw_receipt = self._host.completion_statework_receipt()
                if not isinstance(csw_receipt, Mapping):
                    raise TypeError("CSW completion receipt is unavailable")
                typed_csw_receipt = CompletionAuthorityReceipt.from_mapping(csw_receipt)
                if typed_csw_receipt.authority_id != "csw-workflows":
                    raise ValueError("CSW completion receipt authority is invalid")
            except Exception:
                return {"status": "incomplete", "decision": "csw_completion_receipt_missing",
                        "task_id": self.request.task_id,
                        "validators": self._validator_summaries(results)}
            receipts.append(typed_csw_receipt)
        completion_decision = aggregate_completion_decision(
            required_authority_ids, receipts, validation=validation)
        if not completion_decision.allowed:
            return {"status": "incomplete", "decision": "completion_authority_denied",
                    "task_id": self.request.task_id,
                    "completion_decision": completion_decision.to_dict(),
                    "validators": self._validator_summaries(results)}
        try:
            finished = self._host.finish_task({
                "status": "completed", "completed": True,
                "validator_id": "completion-boundary-v1",
            })
        except Exception as exc:
            finalization_receipts = [item for item in receipts
                                     if item.authority_id != "cfw-runtime"]
            finalization_receipts.append(CompletionAuthorityReceipt(
                authority_id="cfw-runtime", allowed=False,
                reason_codes=("finalization_failed",)))
            finalization_decision = aggregate_completion_decision(
                required_authority_ids, finalization_receipts,
                validation=validation)
            return {"status": "incomplete", "decision": "finalization_failed",
                    "error_class": type(exc).__name__,
                    "error_message": _safe_error({"message": str(exc)}).get(
                        "message"),
                    "task_id": self.request.task_id,
                    "completion_decision": finalization_decision.to_dict(),
                    "validators": self._validator_summaries(results)}
        completed = bool(finished.get("completed", False))
        if not completed:
            finalization_receipts = [item for item in receipts
                                     if item.authority_id != "cfw-runtime"]
            finalization_receipts.append(CompletionAuthorityReceipt(
                authority_id="cfw-runtime", allowed=False,
                reason_codes=("finalization_failed",)))
            completion_decision = aggregate_completion_decision(
                required_authority_ids, finalization_receipts,
                validation=validation)
        return {"status": "completed" if completed else "incomplete",
                "decision": "allow" if completed else "finalization_failed",
                "task_id": self.request.task_id,
                "thread_id": self._thread_id,
                "completed": completed,
                "completion_decision": completion_decision.to_dict(),
                "validated_criteria": len(required_criteria),
                "validators": self._validator_summaries(results)}
