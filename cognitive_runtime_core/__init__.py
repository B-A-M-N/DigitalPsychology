"""Harness-neutral runtime contracts shared by cognitive applications.

Application behavior and product-specific host integrations live outside this
package. Existing product-specific import paths remain available during
migration to this namespace.
"""

__version__ = "1.5.0"

from .actions import HostActionBinding, HostActionDescriptor, HostActionExecutor
from .bindings import HostRuntimeBindings
from .completion import (CompletionAggregation, CompletionAuthorityReceipt,
                         CompletionDecision, ValidatorCompletionEvidence,
                         aggregate_completion, aggregate_completion_decision)
from .errors import ActuationFailure
from .evaluation import (EvaluationCohortIdentity, IncomparableCohortError)
from .harness import (ActionProposal, CompletionProposal,
                      CallbackCognitiveHarnessLifecycle,
                      CognitiveHarnessLifecycle,
                      CognitiveRuntimeHarnessSession,
                      HarnessAdapterIdentity, HarnessBoundary,
                      HarnessConformanceLevel,
                      HarnessBoundaryEvent, HarnessCapabilities,
                      HarnessDispatchSummary, HarnessRequirement,
                      HarnessRequirementError, ModelBoundaryProjection,
                      RuntimeObservationProjection,
                      TaskContract, dispatch_harness_events)
from .validator_reuse import ReusableValidation, ValidatorReuseCache
from .transactions import (PreparedTransactionOutcome, RuntimeTransactionOutcome,
                           RuntimeTransactionStage, TransactionStage,
                           coordinate_runtime_transaction,
                           execute_prepared_transaction)
from .validators import HostValidatorBinding

__all__ = [
    "__version__", "ActionProposal", "CompletionAggregation", "CompletionAuthorityReceipt",
    "ActuationFailure", "CompletionDecision", "CompletionProposal",
    "EvaluationCohortIdentity", "IncomparableCohortError",
    "CallbackCognitiveHarnessLifecycle", "CognitiveHarnessLifecycle",
    "CognitiveRuntimeHarnessSession",
    "HarnessAdapterIdentity", "HarnessBoundary", "HarnessBoundaryEvent",
    "HarnessCapabilities", "HarnessConformanceLevel", "HarnessDispatchSummary",
    "HarnessRequirement", "HarnessRequirementError", "HostActionBinding",
    "HostActionDescriptor", "HostActionExecutor", "HostRuntimeBindings",
    "HostValidatorBinding", "ModelBoundaryProjection", "ReusableValidation",
    "RuntimeObservationProjection",
    "PreparedTransactionOutcome", "RuntimeTransactionOutcome",
    "RuntimeTransactionStage", "TaskContract", "TransactionStage",
    "ValidatorCompletionEvidence", "ValidatorReuseCache",
    "aggregate_completion", "aggregate_completion_decision",
    "coordinate_runtime_transaction", "dispatch_harness_events",
    "execute_prepared_transaction",
]
