"""Deterministic experiment evidence, reports, and CI decisions."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .contracts import Decision, RunManifest
from .retrieval import PairedComparison, RetrievalEvaluation
from .safety import SafetyAuditResult

REPORT_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class DecisionEvidence:
    """Pre-registered statistical and operational evidence."""

    ndcg_at_10_ci95_lower: float | None
    baseline_p95_latency_ms: float | None
    candidate_p95_latency_ms: float | None


@dataclass(frozen=True, slots=True)
class ExperimentReport:
    schema_version: int
    protocol_id: str
    protocol_version: str
    partition: str
    baseline_manifest: RunManifest
    candidate_manifest: RunManifest
    baseline: RetrievalEvaluation
    candidate: RetrievalEvaluation
    comparison: PairedComparison
    safety: SafetyAuditResult
    decision_evidence: DecisionEvidence
    decision: Decision
    decision_reasons: tuple[str, ...]
    limitations: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class EvidenceReporter:
    """Validate evidence and write canonical JSON, Markdown, and SVG atomically."""

    def build(
        self,
        *,
        baseline_manifest: RunManifest,
        candidate_manifest: RunManifest,
        baseline: RetrievalEvaluation,
        candidate: RetrievalEvaluation,
        comparison: PairedComparison,
        safety: SafetyAuditResult,
        decision_evidence: DecisionEvidence,
    ) -> ExperimentReport:
        self._validate(
            baseline_manifest,
            candidate_manifest,
            baseline,
            candidate,
            comparison,
            safety,
        )
        decision, reasons = self._decide(comparison, safety, decision_evidence)
        final_test = baseline_manifest.final_test or candidate_manifest.final_test
        limitations = (
            "This report is evidence for one declared experiment, not production approval.",
            (
                "Final-test evidence must not be reused for tuning or configuration changes."
                if final_test
                else "Calibration/development evidence does not authorise final-test access."
            ),
        )
        return ExperimentReport(
            schema_version=REPORT_SCHEMA_VERSION,
            protocol_id=baseline_manifest.protocol_id,
            protocol_version=baseline_manifest.protocol_version,
            partition=baseline.partition,
            baseline_manifest=baseline_manifest,
            candidate_manifest=candidate_manifest,
            baseline=baseline,
            candidate=candidate,
            comparison=comparison,
            safety=safety,
            decision_evidence=decision_evidence,
            decision=decision,
            decision_reasons=reasons,
            limitations=limitations,
        )

    def write(self, report: ExperimentReport, output_directory: Path) -> dict[str, str]:
        output_directory.mkdir(parents=True, exist_ok=True)
        report_bytes = _canonical_json(report.to_dict())
        digest = hashlib.sha256(report_bytes).hexdigest()
        envelope = _canonical_json(
            {"report": report.to_dict(), "report_sha256": digest}
        )
        outputs = {
            "evidence.json": envelope,
            "report.md": self.render_markdown(report, digest).encode(),
            "report.svg": self.render_svg(report).encode(),
        }
        for name, content in outputs.items():
            _atomic_write(output_directory / name, content)
        return {name: hashlib.sha256(content).hexdigest() for name, content in outputs.items()}

    @staticmethod
    def verify_json(path: Path) -> bool:
        envelope = json.loads(path.read_text(encoding="utf-8"))
        expected = envelope.get("report_sha256")
        report = envelope.get("report")
        return isinstance(expected, str) and expected == hashlib.sha256(
            _canonical_json(report)
        ).hexdigest()

    @staticmethod
    def ci_exit_code(decision: Decision) -> int:
        return {Decision.PASS: 0, Decision.REVIEW: 0, Decision.FAIL: 1,
                Decision.INCONCLUSIVE: 2}[decision]

    @staticmethod
    def render_markdown(report: ExperimentReport, digest: str) -> str:
        delta = report.comparison.mean_deltas["ndcg_at_10"]
        safety = report.safety.metrics
        reasons = "\n".join(f"- {reason}" for reason in report.decision_reasons)
        limitations = "\n".join(f"- {item}" for item in report.limitations)
        return (
            "# NormGuard experiment report\n\n"
            f"**Decision:** `{report.decision.value}`  \n"
            f"**Protocol:** `{report.protocol_id}` v{report.protocol_version}  \n"
            f"**Partition:** `{report.partition}`  \n"
            f"**Evidence SHA-256:** `{digest}`\n\n"
            "## Primary evidence\n\n"
            "| Measure | Value |\n|---|---:|\n"
            f"| C0 nDCG@10 | {report.baseline.aggregate_metrics['ndcg_at_10']:.6f} |\n"
            f"| C4 nDCG@10 | {report.candidate.aggregate_metrics['ndcg_at_10']:.6f} |\n"
            f"| Paired delta | {delta:+.6f} |\n"
            f"| CI95 lower bound | {_format(report.decision_evidence.ndcg_at_10_ci95_lower)} |\n"
            "| Exact identifier retention | "
            f"{_format(safety.get('exact_identifier_retention'))} |\n"
            "| Protected-term violation rate | "
            f"{_format(safety.get('protected_term_violation_rate'))} |\n"
            f"| S0 events | {_format(safety.get('s0_event_count'))} |\n\n"
            f"## Decision reasons\n\n{reasons}\n\n"
            f"## Limitations\n\n{limitations}\n"
        )

    @staticmethod
    def render_svg(report: ExperimentReport) -> str:
        baseline = report.baseline.aggregate_metrics["ndcg_at_10"]
        candidate = report.candidate.aggregate_metrics["ndcg_at_10"]
        scale = 420
        return (
            '<svg xmlns="http://www.w3.org/2000/svg" width="720" height="300" '
            'viewBox="0 0 720 300" role="img" aria-labelledby="title desc">\n'
            '<title id="title">NormGuard nDCG at 10 comparison</title>\n'
            f'<desc id="desc">C0 {baseline:.6f}; C4 {candidate:.6f}; '
            f'decision {report.decision.value}</desc>\n'
            '<rect width="720" height="300" fill="#f8fafc"/>\n'
            '<text x="36" y="40" font-family="sans-serif" font-size="22" fill="#0f172a">'
            'nDCG@10 comparison</text>\n'
            f'<rect x="140" y="80" width="{baseline * scale:.2f}" height="48" fill="#2563eb"/>\n'
            f'<rect x="140" y="160" width="{candidate * scale:.2f}" height="48" fill="#f59e0b"/>\n'
            '<text x="36" y="111" font-family="sans-serif" font-size="17">C0</text>\n'
            '<text x="36" y="191" font-family="sans-serif" font-size="17">C4</text>\n'
            f'<text x="580" y="111" font-family="monospace" font-size="16">{baseline:.6f}</text>\n'
            f'<text x="580" y="191" font-family="monospace" font-size="16">{candidate:.6f}</text>\n'
            f'<text x="36" y="260" font-family="sans-serif" font-size="16">Decision: '
            f'{report.decision.value}; n={len(report.comparison.query_ids)} queries</text>\n'
            '</svg>\n'
        )

    @staticmethod
    def _validate(
        baseline_manifest: RunManifest,
        candidate_manifest: RunManifest,
        baseline: RetrievalEvaluation,
        candidate: RetrievalEvaluation,
        comparison: PairedComparison,
        safety: SafetyAuditResult,
    ) -> None:
        if baseline_manifest.protocol_id != candidate_manifest.protocol_id or (
            baseline_manifest.protocol_version != candidate_manifest.protocol_version
        ):
            raise ValueError("manifest protocol mismatch")
        if baseline_manifest.dataset_sha256 != candidate_manifest.dataset_sha256 or (
            baseline_manifest.split_manifest_sha256
            != candidate_manifest.split_manifest_sha256
        ):
            raise ValueError("dataset or split manifest mismatch")
        if baseline_manifest.configuration_id != baseline.configuration_id:
            raise ValueError("baseline configuration mismatch")
        if candidate_manifest.configuration_id != candidate.configuration_id:
            raise ValueError("candidate configuration mismatch")
        if safety.configuration_id != candidate.configuration_id:
            raise ValueError("safety configuration mismatch")
        if baseline.partition != candidate.partition:
            raise ValueError("retrieval partition mismatch")
        if (baseline.k1, baseline.b) != (candidate.k1, candidate.b):
            raise ValueError("retrieval parameters must be identical")
        if comparison.baseline_configuration_id != baseline.configuration_id or (
            comparison.candidate_configuration_id != candidate.configuration_id
        ):
            raise ValueError("paired comparison configuration mismatch")
        if "ndcg_at_10" not in baseline.aggregate_metrics or (
            "ndcg_at_10" not in candidate.aggregate_metrics
            or "ndcg_at_10" not in comparison.mean_deltas
        ):
            raise ValueError("nDCG@10 evidence is required")

    @staticmethod
    def _decide(
        comparison: PairedComparison,
        safety: SafetyAuditResult,
        evidence: DecisionEvidence,
    ) -> tuple[Decision, tuple[str, ...]]:
        if safety.has_hard_gate_failure or safety.decision is Decision.FAIL:
            return Decision.FAIL, ("terminology-safety hard gate failed",)
        mandatory = (
            safety.metrics.get("exact_identifier_retention"),
            safety.metrics.get("protected_term_violation_rate"),
            safety.metrics.get("s0_event_count"),
            evidence.ndcg_at_10_ci95_lower,
            evidence.baseline_p95_latency_ms,
            evidence.candidate_p95_latency_ms,
        )
        if any(value is None for value in mandatory):
            return Decision.INCONCLUSIVE, (
                "mandatory statistical or operational evidence is missing",
            )
        if (
            safety.metrics["exact_identifier_retention"] != 1.0
            or safety.metrics["protected_term_violation_rate"] != 0.0
            or safety.metrics["s0_event_count"] != 0
        ):
            return Decision.FAIL, ("H3 terminology-safety threshold failed",)
        delta = comparison.mean_deltas["ndcg_at_10"]
        assert evidence.baseline_p95_latency_ms is not None
        assert evidence.candidate_p95_latency_ms is not None
        latency_ok = (
            evidence.candidate_p95_latency_ms <= 2 * evidence.baseline_p95_latency_ms
            and evidence.candidate_p95_latency_ms - evidence.baseline_p95_latency_ms <= 10
        )
        if evidence.ndcg_at_10_ci95_lower > 0 and delta >= 0.005 and latency_ok:
            return Decision.PASS, ("H1-H4 pre-registered gates passed",)
        return Decision.REVIEW, ("safety passed but utility or latency evidence is mixed",)


def _canonical_json(value: Any) -> bytes:
    serialized = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return f"{serialized}\n".encode()


def _atomic_write(path: Path, content: bytes) -> None:
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _format(value: float | int | None) -> str:
    if value is None:
        return "not applicable"
    if isinstance(value, float):
        return f"{value:.6f}"
    return str(value)
