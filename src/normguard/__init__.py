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

__all__ = [
    "Decision",
    "EvidenceBundle",
    "Failure",
    "FailureCode",
    "PolicyClass",
    "PolicyRule",
    "RunManifest",
    "Severity",
    "SpanAction",
    "SpanTrace",
]

__version__ = "0.1.0"
