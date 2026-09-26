import unittest

from normguard import (
    Decision,
    FailureCode,
    ObservedSpan,
    PolicyClass,
    PolicyRule,
    RunManifest,
    SafetyCase,
    Severity,
    SpanAction,
    TerminologySafetyAuditor,
)


class TerminologySafetyAuditorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.auditor = TerminologySafetyAuditor(
            [
                PolicyRule("sku", PolicyClass.EXACT_PRESERVE, "exact", "SKU-AX2048"),
                PolicyRule(
                    "alias",
                    PolicyClass.APPROVED_ALIAS,
                    "exact",
                    "trainers",
                    approved_output="sneakers",
                ),
                PolicyRule(
                    "review",
                    PolicyClass.REVIEW_OR_ABSTAIN,
                    "exact",
                    "limited-edition",
                ),
            ]
        )

    def test_valid_protected_evidence_passes_deterministically(self) -> None:
        case = SafetyCase(
            "retail-1",
            "SKU-AX2048 trainers limited-edition",
            "SKU-AX2048 sneakers limited-edition",
            (
                ObservedSpan(
                    "SKU-AX2048", 0, 10, PolicyClass.EXACT_PRESERVE,
                    SpanAction.PRESERVED, "SKU-AX2048", "sku",
                ),
                ObservedSpan(
                    "trainers", 11, 19, PolicyClass.APPROVED_ALIAS,
                    SpanAction.TRANSFORMED, "sneakers", "alias",
                ),
                ObservedSpan(
                    "limited-edition", 20, 35, PolicyClass.REVIEW_OR_ABSTAIN,
                    SpanAction.REVIEW, "limited-edition", "review",
                ),
            ),
        )
        first = self.auditor.audit(configuration_id="C4", cases=[case])
        self.assertEqual(first, self.auditor.audit(configuration_id="C4", cases=[case]))
        self.assertEqual(first.decision, Decision.PASS)
        self.assertEqual(first.metrics["protected_term_violation_rate"], 0.0)
        self.assertEqual(first.metrics["exact_identifier_retention"], 1.0)
        self.assertEqual(first.metrics["s0_event_count"], 0)
        run = RunManifest(
            protocol_id="NG-POC-001",
            protocol_version="0.1.0",
            dataset_name="synthetic",
            dataset_sha256="a" * 64,
            split_manifest_sha256="b" * 64,
            configuration_id="C4",
            configuration_sha256="c" * 64,
            seed=7,
        )
        bundle = first.to_evidence_bundle(run)
        self.assertEqual(bundle.decision, Decision.PASS)
        self.assertEqual(bundle.metrics["exact_identifier_retention"], 1.0)

    def test_changed_p0_is_a_blocking_identifier_failure(self) -> None:
        case = SafetyCase(
            "retail-2",
            "SKU-AX2048",
            "sku ax2048",
            (
                ObservedSpan(
                    "SKU-AX2048", 0, 10, PolicyClass.EXACT_PRESERVE,
                    SpanAction.TRANSFORMED, "sku ax2048", "sku", {"engine": "test"},
                ),
            ),
        )
        result = self.auditor.audit(configuration_id="C4", cases=[case])
        self.assertEqual(result.decision, Decision.FAIL)
        self.assertTrue(result.has_hard_gate_failure)
        finding = result.findings[0]
        self.assertEqual(finding.failure.code, FailureCode.IDENTIFIER_CORRUPTION)
        self.assertEqual(finding.failure.severity, Severity.BLOCKER)
        self.assertEqual((finding.start, finding.end), (0, 10))
        self.assertEqual(finding.engine_metadata["engine"], "test")

    def test_wrong_p1_and_transformed_p3_are_detected(self) -> None:
        case = SafetyCase(
            "retail-3",
            "trainers limited-edition",
            "trainer limited edition",
            (
                ObservedSpan(
                    "trainers", 0, 8, PolicyClass.APPROVED_ALIAS,
                    SpanAction.TRANSFORMED, "trainer", "alias",
                ),
                ObservedSpan(
                    "limited-edition", 9, 24, PolicyClass.REVIEW_OR_ABSTAIN,
                    SpanAction.TRANSFORMED, "limited edition", "review",
                ),
            ),
        )
        result = self.auditor.audit(configuration_id="C4", cases=[case])
        self.assertEqual(result.failure_counts, {"F05": 2})
        self.assertEqual(result.severity_counts, {"S1": 2})

    def test_boundary_corruption_and_policy_drift_fail_closed(self) -> None:
        case = SafetyCase(
            "retail-4",
            "SKU-AX2048",
            "SKU-AX2048",
            (
                ObservedSpan(
                    "SKU-AX2048", 1, 11, PolicyClass.EXACT_PRESERVE,
                    SpanAction.PRESERVED, "SKU-AX2048", "unknown",
                ),
            ),
        )
        result = self.auditor.audit(configuration_id="C4", cases=[case])
        self.assertEqual(result.failure_counts, {"F07": 1, "F10": 1})
        self.assertEqual(result.metrics["s0_event_count"], 2)

    def test_zero_denominators_are_not_applicable(self) -> None:
        auditor = TerminologySafetyAuditor(
            [PolicyRule("p2", PolicyClass.CONTEXT_NORMALISABLE, "exact", "shoes")]
        )
        case = SafetyCase(
            "retail-5",
            "shoes",
            "shoe",
            (
                ObservedSpan(
                    "shoes", 0, 5, PolicyClass.CONTEXT_NORMALISABLE,
                    SpanAction.TRANSFORMED, "shoe", "p2",
                ),
            ),
        )
        result = auditor.audit(configuration_id="C4", cases=[case])
        self.assertEqual(result.decision, Decision.INCONCLUSIVE)
        self.assertIsNone(result.metrics["protected_term_violation_rate"])
        self.assertIsNone(result.metrics["exact_identifier_retention"])


if __name__ == "__main__":
    unittest.main()
