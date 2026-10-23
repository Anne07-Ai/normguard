"""Lightweight public facade for the NormGuard Safety Core.

Research symbols remain available lazily for compatibility when the ``research``
extra is installed. Importing :mod:`normguard` itself never imports that stack.
"""

from importlib import import_module
from typing import Any

from .contracts import (
    Decision,
    EvidenceBundle,
    Failure,
    FailureCode,
    PolicyClass,
    PolicyRule,
    RunManifest,
    Severity,
    SpanAction,
    SpanTrace,
)
from .normalisation import NormalisationResult, ProtectedNormaliser
from .runtime import (
    BoundedSafetyRuntime,
    RuntimeCode,
    RuntimeFailure,
    RuntimeLimit,
    RuntimeLimits,
    RuntimeResult,
)
from .sdk import (
    SDK_SCHEMA_VERSION,
    SDK_VERSION,
    SafetyCore,
    SDKDocumentLimits,
    SDKError,
    build_safety_core,
    verify_policy,
)

_LAZY_EXPORTS = {
    "AdapterMetadata": (".adapters", "AdapterMetadata"),
    "AuditFinding": (".safety", "AuditFinding"),
    "BM25EvaluationHarness": (".retrieval", "BM25EvaluationHarness"),
    "C0Adapter": (".adapters", "C0Adapter"),
    "C4SpacyAdapter": (".adapters", "C4SpacyAdapter"),
    "DecisionEvidence": (".reporting", "DecisionEvidence"),
    "EsciPartition": (".esci_experiment", "EsciPartition"),
    "EvidenceReporter": (".reporting", "EvidenceReporter"),
    "ExperimentReport": (".reporting", "ExperimentReport"),
    "LoadedPolicy": (".policy_io", "LoadedPolicy"),
    "NormalisationAdapter": (".adapters", "NormalisationAdapter"),
    "ObservedSpan": (".safety", "ObservedSpan"),
    "PairedComparison": (".retrieval", "PairedComparison"),
    "PairedStatistics": (".experiment", "PairedStatistics"),
    "QueryEvaluation": (".retrieval", "QueryEvaluation"),
    "RankedHit": (".retrieval", "RankedHit"),
    "RelevanceJudgement": (".retrieval", "RelevanceJudgement"),
    "RetrievalDocument": (".retrieval", "RetrievalDocument"),
    "RetrievalEvaluation": (".retrieval", "RetrievalEvaluation"),
    "RetrievalQuery": (".retrieval", "RetrievalQuery"),
    "SafetyAuditResult": (".safety", "SafetyAuditResult"),
    "SafetyCase": (".safety", "SafetyCase"),
    "TerminologySafetyAuditor": (".safety", "TerminologySafetyAuditor"),
    "compare_paired": (".retrieval", "compare_paired"),
    "load_partition": (".esci_experiment", "load_partition"),
    "load_policy": (".policy_io", "load_policy"),
    "paired_statistics": (".experiment", "paired_statistics"),
    "require_development_approval": (".policy_io", "require_development_approval"),
    "select_bm25_parameters": (".experiment", "select_bm25_parameters"),
}


def __getattr__(name: str) -> Any:
    """Load legacy research exports only when consumers explicitly request them."""
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute = target
    value = getattr(import_module(module_name, __name__), attribute)
    globals()[name] = value
    return value

__all__ = [
    "SDK_SCHEMA_VERSION",
    "SDK_VERSION",
    "AdapterMetadata",
    "AuditFinding",
    "BM25EvaluationHarness",
    "BoundedSafetyRuntime",
    "C0Adapter",
    "C4SpacyAdapter",
    "Decision",
    "DecisionEvidence",
    "EsciPartition",
    "EvidenceBundle",
    "EvidenceReporter",
    "ExperimentReport",
    "Failure",
    "FailureCode",
    "LoadedPolicy",
    "NormalisationAdapter",
    "NormalisationResult",
    "ObservedSpan",
    "PairedComparison",
    "PairedStatistics",
    "PolicyClass",
    "PolicyRule",
    "ProtectedNormaliser",
    "QueryEvaluation",
    "RankedHit",
    "RelevanceJudgement",
    "RetrievalDocument",
    "RetrievalEvaluation",
    "RetrievalQuery",
    "RunManifest",
    "RuntimeCode",
    "RuntimeFailure",
    "RuntimeLimit",
    "RuntimeLimits",
    "RuntimeResult",
    "SDKDocumentLimits",
    "SDKError",
    "SafetyAuditResult",
    "SafetyCase",
    "SafetyCore",
    "Severity",
    "SpanAction",
    "SpanTrace",
    "TerminologySafetyAuditor",
    "build_safety_core",
    "compare_paired",
    "load_partition",
    "load_policy",
    "paired_statistics",
    "require_development_approval",
    "select_bm25_parameters",
    "verify_policy",
]

__version__ = "0.1.0"
