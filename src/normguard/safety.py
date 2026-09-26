"""Terminology-safety audit and hard gates for normalisation evidence."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
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


@dataclass(frozen=True, slots=True)
class ObservedSpan:
    """Raw engine observation, intentionally able to represent invalid evidence."""

    text: str
    start: int
    end: int
    policy_class: PolicyClass
    action: SpanAction
    emitted_text: str
    rule_id: str | None = None
    engine_metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_trace(cls, trace: SpanTrace) -> ObservedSpan:
        return cls(
            text=trace.text,
            start=trace.start,
            end=trace.end,
            policy_class=trace.policy_class,
            action=trace.action,
            emitted_text=trace.emitted_text,
            rule_id=trace.rule_id,
            engine_metadata=trace.engine_metadata,
        )


@dataclass(frozen=True, slots=True)
class SafetyCase:
    case_id: str
    source_text: str
    output_text: str
    spans: tuple[ObservedSpan, ...]

    def __post_init__(self) -> None:
        if not self.case_id.strip():
            raise ValueError("case_id must not be empty")


@dataclass(frozen=True, slots=True)
class AuditFinding:
    failure: Failure
    text: str
    start: int
    end: int
    rule_id: str | None
    policy_class: PolicyClass
    engine_metadata: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class SafetyAuditResult:
    configuration_id: str
    decision: Decision
    decision_reasons: tuple[str, ...]
    metrics: Mapping[str, float | int | None]
    failure_counts: Mapping[str, int]
    severity_counts: Mapping[str, int]
    policy_counts: Mapping[str, int]
    findings: tuple[AuditFinding, ...]

    @property
    def has_hard_gate_failure(self) -> bool:
        return any(
            finding.failure.severity in {Severity.BLOCKER, Severity.CRITICAL}
            for finding in self.findings
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_evidence_bundle(self, run: RunManifest) -> EvidenceBundle:
        if run.configuration_id != self.configuration_id:
            raise ValueError("run and audit configuration IDs must match")
        return EvidenceBundle(
            run=run,
            decision=self.decision,
            metrics=self.metrics,
            failures=tuple(finding.failure for finding in self.findings),
            decision_reasons=self.decision_reasons,
        )


class TerminologySafetyAuditor:
    """Audit raw observations against a versioned set of P0-P3 rules."""

    def __init__(self, rules: Sequence[PolicyRule]) -> None:
        self._rules = {rule.rule_id: rule for rule in rules}
        if len(self._rules) != len(rules):
            raise ValueError("duplicate policy rule_id")

    def audit(
        self,
        *,
        configuration_id: str,
        cases: Sequence[SafetyCase],
    ) -> SafetyAuditResult:
        if not configuration_id.strip() or not cases:
            raise ValueError("configuration_id and cases are required")
        if len({case.case_id for case in cases}) != len(cases):
            raise ValueError("duplicate case_id")

        findings: list[AuditFinding] = []
        protected = identifiers = retained = 0
        transformed = reviews = total_spans = 0
        violation_spans: set[tuple[str, int]] = set()
        harmful_spans: set[tuple[str, int]] = set()
        policy_counts: Counter[str] = Counter()

        for case in sorted(cases, key=lambda item: item.case_id):
            previous_end = -1
            for span_index, span in enumerate(case.spans):
                span_key = (case.case_id, span_index)
                total_spans += 1
                policy_counts[span.policy_class.value] += 1
                transformed += span.action is SpanAction.TRANSFORMED
                reviews += span.action in {SpanAction.REVIEW, SpanAction.ABSTAINED}
                if span.policy_class is not PolicyClass.CONTEXT_NORMALISABLE:
                    protected += 1

                boundary_failure = (
                    span.start < 0
                    or span.end <= span.start
                    or span.end > len(case.source_text)
                    or span.start < previous_end
                    or case.source_text[span.start : span.end] != span.text
                )
                if boundary_failure:
                    findings.append(
                        self._finding(
                            case,
                            span,
                            span_index,
                            FailureCode.BOUNDARY_OR_OFFSET_CORRUPTION,
                            Severity.BLOCKER,
                            "observed span does not align with the source boundary",
                        )
                    )
                    harmful_spans.add(span_key)
                previous_end = max(previous_end, span.end)

                rule = self._rules.get(span.rule_id or "")
                if rule is None or rule.policy_class is not span.policy_class:
                    findings.append(
                        self._finding(
                            case,
                            span,
                            span_index,
                            FailureCode.POLICY_DRIFT,
                            Severity.BLOCKER,
                            "observed span has no matching versioned policy rule",
                        )
                    )
                    harmful_spans.add(span_key)
                    continue

                if span.policy_class is PolicyClass.EXACT_PRESERVE:
                    identifiers += 1
                    exact = (
                        not boundary_failure
                        and
                        span.emitted_text == span.text
                        and span.action is SpanAction.PRESERVED
                    )
                    retained += exact
                    if not exact:
                        violation_spans.add(span_key)
                        harmful_spans.add(span_key)
                        findings.append(
                            self._finding(
                                case,
                                span,
                                span_index,
                                FailureCode.IDENTIFIER_CORRUPTION,
                                Severity.BLOCKER,
                                "P0 identifier was not preserved exactly",
                            )
                        )
                elif span.policy_class is PolicyClass.APPROVED_ALIAS:
                    if (
                        span.emitted_text != rule.approved_output
                        or span.action is not SpanAction.TRANSFORMED
                    ):
                        violation_spans.add(span_key)
                        harmful_spans.add(span_key)
                        findings.append(
                            self._finding(
                                case,
                                span,
                                span_index,
                                FailureCode.NAMED_ENTITY_CORRUPTION,
                                Severity.CRITICAL,
                                "P1 emitted text is not the approved alias",
                            )
                        )
                elif span.policy_class is PolicyClass.REVIEW_OR_ABSTAIN and (
                    span.emitted_text != span.text
                    or span.action is SpanAction.TRANSFORMED
                ):
                    violation_spans.add(span_key)
                    harmful_spans.add(span_key)
                    findings.append(
                        self._finding(
                            case,
                            span,
                            span_index,
                            FailureCode.NAMED_ENTITY_CORRUPTION,
                            Severity.CRITICAL,
                            "P3 span was transformed instead of preserved for review",
                        )
                    )

        metrics: dict[str, float | int | None] = {
            "protected_terms": protected,
            "protected_term_violation_rate": _ratio(len(violation_spans), protected),
            "exact_identifier_retention": _ratio(retained, identifiers),
            "harmful_transformation_rate": _ratio(len(harmful_spans), transformed),
            "review_or_abstention_rate": _ratio(reviews, total_spans),
            "s0_event_count": sum(
                finding.failure.severity is Severity.BLOCKER for finding in findings
            ),
        }
        failure_counts = Counter(finding.failure.code.value for finding in findings)
        severity_counts = Counter(finding.failure.severity.value for finding in findings)
        decision, reasons = self._decision(findings, protected)
        return SafetyAuditResult(
            configuration_id=configuration_id,
            decision=decision,
            decision_reasons=reasons,
            metrics=metrics,
            failure_counts=dict(sorted(failure_counts.items())),
            severity_counts=dict(sorted(severity_counts.items())),
            policy_counts=dict(sorted(policy_counts.items())),
            findings=tuple(findings),
        )

    @staticmethod
    def _finding(
        case: SafetyCase,
        span: ObservedSpan,
        span_index: int,
        code: FailureCode,
        severity: Severity,
        message: str,
    ) -> AuditFinding:
        return AuditFinding(
            failure=Failure(code, severity, case.case_id, message, span_index),
            text=span.text,
            start=span.start,
            end=span.end,
            rule_id=span.rule_id,
            policy_class=span.policy_class,
            engine_metadata=span.engine_metadata,
        )

    @staticmethod
    def _decision(
        findings: Sequence[AuditFinding], protected: int
    ) -> tuple[Decision, tuple[str, ...]]:
        if any(
            finding.failure.severity in {Severity.BLOCKER, Severity.CRITICAL}
            for finding in findings
        ):
            return Decision.FAIL, ("terminology-safety hard gate failed",)
        if protected == 0:
            return Decision.INCONCLUSIVE, ("no protected-term evidence was observed",)
        if findings:
            return Decision.REVIEW, ("non-critical terminology findings require review",)
        return Decision.PASS, ("all observed terminology-safety gates passed",)


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None
