"""Causal receipt comparing an advisory Laya decision with its counterfactual."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from types import MappingProxyType
from typing import Any, Mapping


@dataclass(frozen=True)
class SemanticInfluenceReceipt:
    receipt_id: str
    provider_decision_id: str
    question_ids: tuple[str, ...]
    selected_answers: Mapping[str, str]
    calibrated_confidences: Mapping[str, float | None]
    counterfactual_candidate_id: str | None
    selected_candidate_id: str | None
    changed_selection: bool
    changed_mode: bool
    changed_frameworks: tuple[str, ...]
    changed_statework_preference: bool

    def __post_init__(self) -> None:
        if not isinstance(self.receipt_id, str) or not self.receipt_id.startswith("sir-") \
                or len(self.receipt_id) != 28:
            raise ValueError("semantic influence receipt ID is malformed")
        if not isinstance(self.provider_decision_id, str) or not self.provider_decision_id \
                or len(self.provider_decision_id) > 256:
            raise ValueError("semantic provider decision ID must be bounded text")
        questions = tuple(self.question_ids)
        if (not questions or len(questions) > 8 or len(questions) != len(set(questions))
                or any(not isinstance(item, str) or not item or len(item) > 64
                       for item in questions)):
            raise ValueError("semantic influence questions are malformed")
        answers = dict(self.selected_answers)
        confidences = dict(self.calibrated_confidences)
        if set(answers) != set(questions) or set(confidences) != set(questions):
            raise ValueError("semantic influence answers must cover requested questions")
        if any(not isinstance(value, str) or not value or len(value) > 128
               for value in answers.values()):
            raise ValueError("semantic influence answers must be bounded text")
        for value in confidences.values():
            if value is not None and (not isinstance(value, (int, float))
                                      or not 0.0 <= float(value) <= 1.0):
                raise ValueError("calibrated confidence must be null or between zero and one")
        changed = tuple(self.changed_frameworks)
        if (len(changed) > 16 or len(changed) != len(set(changed))
                or any(not isinstance(item, str) or not item or len(item) > 64
                       for item in changed)):
            raise ValueError("changed framework list is malformed")
        for name in ("changed_selection", "changed_mode", "changed_statework_preference"):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be boolean")
        if self.counterfactual_candidate_id is not None and (
                not isinstance(self.counterfactual_candidate_id, str)
                or not self.counterfactual_candidate_id
                or len(self.counterfactual_candidate_id) > 128):
            raise ValueError("counterfactual candidate ID is malformed")
        if self.selected_candidate_id is not None and (
                not isinstance(self.selected_candidate_id, str)
                or not self.selected_candidate_id or len(self.selected_candidate_id) > 128):
            raise ValueError("selected candidate ID is malformed")
        object.__setattr__(self, "question_ids", questions)
        object.__setattr__(self, "selected_answers", MappingProxyType(answers))
        object.__setattr__(self, "calibrated_confidences", MappingProxyType(confidences))
        object.__setattr__(self, "changed_frameworks", changed)

    @property
    def influenced(self) -> bool:
        return bool(self.changed_selection or self.changed_mode or self.changed_frameworks
                    or self.changed_statework_preference)

    def to_dict(self) -> dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "provider_decision_id": self.provider_decision_id,
            "question_ids": list(self.question_ids),
            "selected_answers": dict(self.selected_answers),
            "calibrated_confidences": dict(self.calibrated_confidences),
            "counterfactual_candidate_id": self.counterfactual_candidate_id,
            "selected_candidate_id": self.selected_candidate_id,
            "changed_selection": self.changed_selection,
            "changed_mode": self.changed_mode,
            "changed_frameworks": list(self.changed_frameworks),
            "changed_statework_preference": self.changed_statework_preference,
        }


def compare_semantic_selection(judgment: Any, counterfactual: Any,
                               actual: Any) -> SemanticInfluenceReceipt | None:
    """Build a receipt from an advisory judgment and two pure DP selections."""
    provider_decision_id = getattr(judgment, "request_id", None)
    answers = getattr(judgment, "answers", None)
    if not isinstance(provider_decision_id, str) or not answers:
        return None
    question_ids = tuple(str(answer.question_id) for answer in answers)
    selected_answers = {str(answer.question_id): str(answer.selected) for answer in answers}
    confidences = {
        str(answer.question_id): (
            None if answer.calibrated_confidence is None
            else float(answer.calibrated_confidence))
        for answer in answers
    }
    before_id = counterfactual.selected_candidate_id
    after_id = actual.selected_candidate_id
    before_modes = dict(counterfactual.state.framework_modes)
    after_modes = dict(actual.state.framework_modes)
    changed_frameworks = tuple(sorted(
        name for name in set(before_modes) | set(after_modes)
        if before_modes.get(name) != after_modes.get(name)))
    payload = {
        "provider_decision_id": provider_decision_id,
        "question_ids": list(question_ids),
        "selected_answers": selected_answers,
        "calibrated_confidences": confidences,
        "counterfactual_candidate_id": before_id,
        "selected_candidate_id": after_id,
        "changed_selection": before_id != after_id,
        "changed_mode": counterfactual.state.cognitive_mode != actual.state.cognitive_mode,
        "changed_frameworks": list(changed_frameworks),
        "changed_statework_preference": (
            tuple(counterfactual.preferred_stateworks)
            != tuple(actual.preferred_stateworks)),
    }
    receipt_id = "sir-" + hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False).encode("utf-8")).hexdigest()[:24]
    return SemanticInfluenceReceipt(receipt_id=receipt_id, **payload)


__all__ = ["SemanticInfluenceReceipt", "compare_semantic_selection"]
