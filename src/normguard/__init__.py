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
from .esci_experiment import EsciPartition, load_partition
from .experiment import PairedStatistics, paired_statistics, select_bm25_parameters
from .normalisation import NormalisationResult, ProtectedNormaliser
from .policy_io import LoadedPolicy, load_policy, require_development_approval
from .reporting import DecisionEvidence, EvidenceReporter, ExperimentReport
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
    "SafetyAuditResult",
    "SafetyCase",
    "Severity",
    "SpanAction",
    "SpanTrace",
    "TerminologySafetyAuditor",
    "compare_paired",
    "load_partition",
    "load_policy",
    "paired_statistics",
    "require_development_approval",
    "select_bm25_parameters",
]

__version__ = "0.1.0"
