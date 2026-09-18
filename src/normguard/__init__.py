"""NormGuard: assurance contracts for text-normalisation experiments."""

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
    "Decision",
    "EvidenceBundle",
    "Failure",
    "FailureCode",
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
