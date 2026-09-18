"""Versioned contracts for NormGuard policies, traces, and evidence.

These models intentionally use only the Python standard library. Retrieval and NLP
adapters may depend on third-party packages, but evidence remains portable.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any, Mapping, Sequence


CONTRACT_VERSION = "0.1.0"


class PolicyClass(StrEnum):
    """How a span is allowed to be transformed."""

    EXACT_PRESERVE = "P0"
    APPROVED_ALIAS = "P1"
    CONTEXT_NORMALISABLE = "P2"
    REVIEW_OR_ABSTAIN = "P3"


class SpanAction(StrEnum):
    """Observed action taken for a span."""

    PRESERVED = "preserved"
    TRANSFORMED = "transformed"
    REVIEW = "review"
    ABSTAINED = "abstained"


class FailureCode(StrEnum):
    OVER_NORMALISATION = "F01"
    UNDER_NORMALISATION = "F02"
    INCORRECT_LEMMA = "F03"
    IDENTIFIER_CORRUPTION = "F04"
    NAMED_ENTITY_CORRUPTION = "F05"
    NEGATION_OR_POLARITY_DAMAGE = "F06"
    BOUNDARY_OR_OFFSET_CORRUPTION = "F07"
    QUERY_INDEX_ASYMMETRY = "F08"
    LANGUAGE_OR_LOCALE_MISMATCH = "F09"
    POLICY_DRIFT = "F10"


class Severity(StrEnum):
    BLOCKER = "S0"
    CRITICAL = "S1"
    MAJOR = "S2"
    MINOR = "S3"


class Decision(StrEnum):
    PASS = "PASS"
    REVIEW = "REVIEW"
    FAIL = "FAIL"
    INCONCLUSIVE = "INCONCLUSIVE"


@dataclass(frozen=True, slots=True)
class PolicyRule:
    """One versioned rule in a protected-term policy."""

    rule_id: str
    policy_class: PolicyClass
    match_type: str
    pattern: str
    approved_output: str | None = None
    source: str | None = None

    def __post_init__(self) -> None:
        if not self.rule_id.strip():
            raise ValueError("rule_id must not be empty")
        if self.match_type not in {"exact", "regex"}:
            raise ValueError("match_type must be 'exact' or 'regex'")
        if not self.pattern:
            raise ValueError("pattern must not be empty")
        if self.policy_class is PolicyClass.APPROVED_ALIAS and self.approved_output is None:
            raise ValueError("P1 rules require approved_output")
        if self.policy_class is not PolicyClass.APPROVED_ALIAS and self.approved_output is not None:
            raise ValueError("approved_output is only valid for P1 rules")


@dataclass(frozen=True, slots=True)
class SpanTrace:
    """Auditable transformation trace for a source span."""

    text: str
    start: int
    end: int
    policy_class: PolicyClass
    action: SpanAction
    emitted_text: str
    rule_id: str | None = None
    normalised_surface: str | None = None
    part_of_speech: str | None = None
    candidate_lemma: str | None = None
    engine_metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.start < 0 or self.end <= self.start:
            raise ValueError("span offsets must satisfy 0 <= start < end")
        if len(self.text) != self.end - self.start:
            raise ValueError("span length must match end - start")
        if self.policy_class is PolicyClass.EXACT_PRESERVE:
            if self.action is not SpanAction.PRESERVED or self.emitted_text != self.text:
                raise ValueError("P0 spans must be preserved exactly")
        if self.policy_class is PolicyClass.REVIEW_OR_ABSTAIN:
            if self.action not in {
                SpanAction.PRESERVED,
                SpanAction.REVIEW,
                SpanAction.ABSTAINED,
            }:
                raise ValueError("P3 spans cannot be transformed directly")


@dataclass(frozen=True, slots=True)
class Failure:
    """One diagnosed terminology or pipeline failure."""

    code: FailureCode
    severity: Severity
    case_id: str
    message: str
    span_index: int | None = None

    def __post_init__(self) -> None:
        if not self.case_id.strip():
            raise ValueError("case_id must not be empty")
        if not self.message.strip():
            raise ValueError("failure message must not be empty")
        if self.span_index is not None and self.span_index < 0:
            raise ValueError("span_index must be non-negative")


@dataclass(frozen=True, slots=True)
class RunManifest:
    """Minimum provenance required for an experiment run."""

    protocol_id: str
    protocol_version: str
    dataset_name: str
    dataset_sha256: str
    split_manifest_sha256: str
    configuration_id: str
    configuration_sha256: str
    seed: int
    final_test: bool = False
    environment: Mapping[str, str] = field(default_factory=dict)
    dependency_versions: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in (
            "protocol_id",
            "protocol_version",
            "dataset_name",
            "configuration_id",
        ):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must not be empty")
        for name in (
            "dataset_sha256",
            "split_manifest_sha256",
            "configuration_sha256",
        ):
            value = getattr(self, name)
            if len(value) != 64 or any(char not in "0123456789abcdef" for char in value.lower()):
                raise ValueError(f"{name} must be a 64-character SHA-256 hex digest")
        if self.seed < 0:
            raise ValueError("seed must be non-negative")


@dataclass(frozen=True, slots=True)
class EvidenceBundle:
    """Portable result contract used by reports and CI decisions."""

    run: RunManifest
    decision: Decision
    metrics: Mapping[str, float | int | None]
    spans: Sequence[SpanTrace] = ()
    failures: Sequence[Failure] = ()
    contract_version: str = CONTRACT_VERSION
    decision_reasons: Sequence[str] = ()

    def __post_init__(self) -> None:
        if self.contract_version != CONTRACT_VERSION:
            raise ValueError(f"unsupported contract_version: {self.contract_version}")
        severe = {
            Severity.BLOCKER,
            Severity.CRITICAL,
        }
        if self.decision is Decision.PASS and any(
            failure.severity in severe for failure in self.failures
        ):
            raise ValueError("PASS cannot contain S0 or S1 failures")
        if self.decision is Decision.PASS and not self.decision_reasons:
            raise ValueError("PASS requires explicit decision_reasons")
        if self.decision is Decision.INCONCLUSIVE and not self.decision_reasons:
            raise ValueError("INCONCLUSIVE requires explicit decision_reasons")

    @property
    def has_hard_gate_failure(self) -> bool:
        return any(
            failure.severity in {Severity.BLOCKER, Severity.CRITICAL}
            for failure in self.failures
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable representation with enum values."""

        return _enum_values(asdict(self))


def _enum_values(value: Any) -> Any:
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, dict):
        return {key: _enum_values(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_enum_values(item) for item in value]
    return value
