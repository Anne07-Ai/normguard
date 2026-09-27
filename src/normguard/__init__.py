"""NormGuard: assurance contracts for text-normalisation experiments."""

from .adapters import AdapterMetadata, C0Adapter, C4SpacyAdapter, NormalisationAdapter
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
from .retrieval import (
    BM25EvaluationHarness,
    PairedComparison,
    QueryEvaluation,
    RankedHit,
    RelevanceJudgement,
    RetrievalDocument,
    RetrievalEvaluation,
    RetrievalQuery,
    compare_paired,
)
from .safety import (
    AuditFinding,
    ObservedSpan,
    SafetyAuditResult,
    SafetyCase,
    TerminologySafetyAuditor,
)

__all__ = [
    "AdapterMetadata",
    "AuditFinding",
    "BM25EvaluationHarness",
    "C0Adapter",
    "C4SpacyAdapter",
    "Decision",
    "EvidenceBundle",
    "Failure",
    "FailureCode",
    "NormalisationAdapter",
    "NormalisationResult",
    "ObservedSpan",
    "PairedComparison",
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
    "SafetyAuditResult",
    "SafetyCase",
    "Severity",
    "SpanAction",
    "SpanTrace",
    "TerminologySafetyAuditor",
    "compare_paired",
]

__version__ = "0.1.0"
