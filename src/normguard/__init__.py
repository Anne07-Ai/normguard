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

__all__ = [
    "AdapterMetadata",
    "C0Adapter",
    "C4SpacyAdapter",
    "Decision",
    "EvidenceBundle",
    "Failure",
    "FailureCode",
    "NormalisationAdapter",
    "NormalisationResult",
    "PolicyClass",
    "PolicyRule",
    "ProtectedNormaliser",
    "RunManifest",
    "Severity",
    "SpanAction",
    "SpanTrace",
]

__version__ = "0.1.0"
