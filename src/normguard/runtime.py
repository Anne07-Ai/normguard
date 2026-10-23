"""Bounded, deterministic runtime for already verified policy payloads."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .normalisation import ProtectedNormaliser
from .policy_verification import VerifiedPolicy
from .safety_contracts import (
    DecisionEvidence,
    EvidenceSpan,
    EvidenceStatus,
    canonical_json_bytes,
)

RUNTIME_VERSION = "0.1.0"


class RuntimeCode(StrEnum):
    INVALID_INPUT = "INVALID_INPUT"
    LIMIT_EXCEEDED = "LIMIT_EXCEEDED"
    INTERNAL_FAILURE = "INTERNAL_FAILURE"


class RuntimeLimit(StrEnum):
    INPUT_BYTES = "input_bytes"
    RULE_COUNT = "rule_count"
    PATTERN_CHARACTERS = "pattern_characters"
    MATCH_COUNT = "match_count"
    OUTPUT_BYTES = "output_bytes"
    EVIDENCE_SPANS = "evidence_spans"
    SAFETY_EVENTS = "safety_events"


class RuntimeFailure(Exception):
    """Terminal failure that never embeds raw input or output."""

    def __init__(
        self,
        code: RuntimeCode,
        *,
        limit: RuntimeLimit | None = None,
        policy_id: str | None = None,
    ) -> None:
        self.code = code
        self.limit = limit
        self.policy_id = policy_id
        fields = [
            f"limit={limit.value}" if limit is not None else None,
            f"policy_id={policy_id}" if policy_id is not None else None,
        ]
        suffix = " " + " ".join(item for item in fields if item) if any(fields) else ""
        super().__init__(f"{code.value}{suffix}")


@dataclass(frozen=True, slots=True)
class RuntimeLimits:
    max_input_bytes: int = 65_536
    max_rules: int = 10_000
    max_pattern_characters: int = 1_024
    max_matches: int = 10_000
    max_output_bytes: int = 131_072
    max_evidence_spans: int = 20_000
    max_safety_events: int = 1_000

    def __post_init__(self) -> None:
        for name, value in self.to_dict().items():
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")

    def to_dict(self) -> dict[str, int]:
        return {
            "max_input_bytes": self.max_input_bytes,
            "max_rules": self.max_rules,
            "max_pattern_characters": self.max_pattern_characters,
            "max_matches": self.max_matches,
            "max_output_bytes": self.max_output_bytes,
            "max_evidence_spans": self.max_evidence_spans,
            "max_safety_events": self.max_safety_events,
        }


@dataclass(frozen=True, slots=True)
class RuntimeResult:
    output_text: str
    status: EvidenceStatus
    evidence: DecisionEvidence


class BoundedSafetyRuntime:
    """Immutable facade over deterministic policy matching.

    Construction validates policy-owned resources. Calls validate request-owned
    resources before processing and return no partial result on failure.
    """

    __slots__ = ("_frozen", "_limits", "_normaliser", "_verified_policy")

    def __init__(
        self,
        verified_policy: VerifiedPolicy,
        *,
        limits: RuntimeLimits | None = None,
    ) -> None:
        if not isinstance(verified_policy, VerifiedPolicy):
            raise TypeError("verified_policy must be a VerifiedPolicy")
        selected_limits = limits or RuntimeLimits()
        rules = verified_policy.payload.rules
        self._check_limit(
            len(rules),
            selected_limits.max_rules,
            RuntimeLimit.RULE_COUNT,
            verified_policy.payload.policy_id,
        )
        longest_pattern = max((len(rule.pattern) for rule in rules), default=0)
        self._check_limit(
            longest_pattern,
            selected_limits.max_pattern_characters,
            RuntimeLimit.PATTERN_CHARACTERS,
            verified_policy.payload.policy_id,
        )
        object.__setattr__(self, "_verified_policy", verified_policy)
        object.__setattr__(self, "_limits", selected_limits)
        object.__setattr__(self, "_normaliser", ProtectedNormaliser(rules))
        object.__setattr__(self, "_frozen", True)

    def __setattr__(self, name: str, value: Any) -> None:
        if getattr(self, "_frozen", False):
            raise AttributeError("BoundedSafetyRuntime is immutable")
        object.__setattr__(self, name, value)

    @property
    def limits(self) -> RuntimeLimits:
        return self._limits

    @property
    def verified_policy(self) -> VerifiedPolicy:
        return self._verified_policy

    @staticmethod
    def _check_limit(
        observed: int,
        maximum: int,
        limit: RuntimeLimit,
        policy_id: str,
    ) -> None:
        if observed > maximum:
            raise RuntimeFailure(
                RuntimeCode.LIMIT_EXCEEDED,
                limit=limit,
                policy_id=policy_id,
            )

    def normalise(self, text: str) -> RuntimeResult:
        policy = self._verified_policy
        policy_id = policy.payload.policy_id
        if not isinstance(text, str):
            raise RuntimeFailure(RuntimeCode.INVALID_INPUT, policy_id=policy_id)

        try:
            input_bytes = text.encode("utf-8")
        except UnicodeEncodeError:
            raise RuntimeFailure(RuntimeCode.INVALID_INPUT, policy_id=policy_id) from None
        self._check_limit(
            len(input_bytes),
            self._limits.max_input_bytes,
            RuntimeLimit.INPUT_BYTES,
            policy_id,
        )
        input_sha256 = hashlib.sha256(input_bytes).hexdigest()

        try:
            normalised = self._normaliser.normalise(text)
        except Exception as exc:
            raise RuntimeFailure(RuntimeCode.INTERNAL_FAILURE, policy_id=policy_id) from exc

        protected_matches = sum(span.rule_id is not None for span in normalised.spans)
        self._check_limit(
            protected_matches,
            self._limits.max_matches,
            RuntimeLimit.MATCH_COUNT,
            policy_id,
        )
        self._check_limit(
            len(normalised.output_text.encode("utf-8")),
            self._limits.max_output_bytes,
            RuntimeLimit.OUTPUT_BYTES,
            policy_id,
        )
        self._check_limit(
            len(normalised.spans),
            self._limits.max_evidence_spans,
            RuntimeLimit.EVIDENCE_SPANS,
            policy_id,
        )

        safety_events = tuple(
            f"review-required:{span.rule_id}"
            for span in normalised.spans
            if span.rule_id is not None and span.action.value == "review"
        )
        self._check_limit(
            len(safety_events),
            self._limits.max_safety_events,
            RuntimeLimit.SAFETY_EVENTS,
            policy_id,
        )
        status = (
            EvidenceStatus.REVIEW_REQUIRED
            if normalised.review_required
            else EvidenceStatus.SUCCESS
        )
        decision_id = self._decision_id(input_sha256)
        evidence = DecisionEvidence(
            decision_id=decision_id,
            runtime_version=RUNTIME_VERSION,
            policy_id=policy_id,
            policy_version=policy.payload.policy_version,
            policy_sha256=policy.payload_sha256,
            input_sha256=input_sha256,
            input_bytes=len(input_bytes),
            status=status,
            spans=tuple(
                EvidenceSpan(
                    rule_id=span.rule_id or "unprotected",
                    start=span.start,
                    end=span.end,
                    policy_class=span.policy_class,
                    action=span.action.value,
                )
                for span in normalised.spans
            ),
            safety_events=safety_events,
            duration_ns=0,
        )
        return RuntimeResult(normalised.output_text, status, evidence)

    def _decision_id(self, input_sha256: str) -> str:
        semantic_inputs = {
            "runtime_version": RUNTIME_VERSION,
            "policy_sha256": self._verified_policy.payload_sha256,
            "input_sha256": input_sha256,
            "limits": self._limits.to_dict(),
        }
        digest = hashlib.sha256(canonical_json_bytes(semantic_inputs)).hexdigest()
        return f"decision-{digest}"
