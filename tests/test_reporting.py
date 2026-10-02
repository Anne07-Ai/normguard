import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from normguard import (
    Decision,
    DecisionEvidence,
    EvidenceReporter,
    PairedComparison,
    QueryEvaluation,
    RetrievalEvaluation,
    RunManifest,
    SafetyAuditResult,
)


def _manifest(configuration_id: str) -> RunManifest:
    return RunManifest(
        protocol_id="NG-POC-001",
        protocol_version="0.1.0",
        dataset_name="synthetic",
        dataset_sha256="a" * 64,
        split_manifest_sha256="b" * 64,
        configuration_id=configuration_id,
        configuration_sha256=("c" if configuration_id == "C0" else "d") * 64,
        seed=7,
    )


def _retrieval(configuration_id: str, score: float) -> RetrievalEvaluation:
    return RetrievalEvaluation(
        configuration_id,
        "development",
        0.9,
        0.75,
        (QueryEvaluation("q1", "shoes", "shoe", (), {"ndcg_at_10": score}),),
        {"ndcg_at_10": score},
    )


def _safety(decision: Decision = Decision.PASS) -> SafetyAuditResult:
    return SafetyAuditResult(
        "C4",
        decision,
        ("synthetic safety evidence",),
        {
            "protected_terms": 1,
            "protected_term_violation_rate": 0.0,
            "exact_identifier_retention": 1.0,
            "harmful_transformation_rate": 0.0,
            "review_or_abstention_rate": 0.0,
            "s0_event_count": 0,
        },
        {},
        {},
        {"P0": 1},
        (),
    )


class EvidenceReporterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.reporter = EvidenceReporter()
        self.baseline = _retrieval("C0", 0.50)
        self.candidate = _retrieval("C4", 0.51)
        self.comparison = PairedComparison(
            "C0", "C4", ("q1",), {"ndcg_at_10": (0.01,)}, {"ndcg_at_10": 0.01}
        )
        self.evidence = DecisionEvidence(0.002, 5.0, 8.0)

    def build(self, **changes: object):
        inputs = {
            "baseline_manifest": _manifest("C0"),
            "candidate_manifest": _manifest("C4"),
            "baseline": self.baseline,
            "candidate": self.candidate,
            "comparison": self.comparison,
            "safety": _safety(),
            "decision_evidence": self.evidence,
        }
        inputs.update(changes)
        return self.reporter.build(**inputs)

    def test_pass_outputs_are_byte_reproducible_and_verifiable(self) -> None:
        report = self.build()
        self.assertEqual(report.decision, Decision.PASS)
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            first_hashes = self.reporter.write(report, Path(first))
            second_hashes = self.reporter.write(report, Path(second))
            self.assertEqual(first_hashes, second_hashes)
            for name in first_hashes:
                self.assertEqual(
                    (Path(first) / name).read_bytes(),
                    (Path(second) / name).read_bytes(),
                )
            evidence = Path(first) / "evidence.json"
            self.assertTrue(self.reporter.verify_json(evidence))
            payload = json.loads(evidence.read_text())
            payload["report"]["decision"] = "FAIL"
            evidence.write_text(json.dumps(payload))
            self.assertFalse(self.reporter.verify_json(evidence))

    def test_safety_failure_overrides_retrieval_gain(self) -> None:
        safety = replace(_safety(Decision.FAIL), severity_counts={"S0": 1})
        self.assertEqual(self.build(safety=safety).decision, Decision.FAIL)
        self.assertEqual(self.reporter.ci_exit_code(Decision.FAIL), 1)

    def test_missing_mandatory_evidence_is_inconclusive(self) -> None:
        evidence = replace(self.evidence, ndcg_at_10_ci95_lower=None)
        report = self.build(decision_evidence=evidence)
        self.assertEqual(report.decision, Decision.INCONCLUSIVE)
        self.assertEqual(self.reporter.ci_exit_code(report.decision), 2)

    def test_mismatched_retrieval_parameters_fail_closed(self) -> None:
        candidate = replace(self.candidate, k1=1.2)
        with self.assertRaisesRegex(ValueError, "parameters must be identical"):
            self.build(candidate=candidate)


if __name__ == "__main__":
    unittest.main()
