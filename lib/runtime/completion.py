"""Compatibility imports for :mod:`cognitive_runtime_core.completion`."""
from cognitive_runtime_core.completion import (
    CompletionAggregation,
    CompletionAuthorityReceipt,
    CompletionDecision,
    ValidatorCompletionEvidence,
    aggregate_completion,
    aggregate_completion_decision,
)

__all__ = ["CompletionAggregation", "CompletionAuthorityReceipt",
           "CompletionDecision", "ValidatorCompletionEvidence",
           "aggregate_completion", "aggregate_completion_decision"]
