import copy
import tempfile
import unittest
from pathlib import Path

from normguard.performance import (
    PerformanceBudget,
    assert_performance_passed,
    build_synthetic_fixture,
    load_performance_evidence,
    run_performance_contract,
    verify_performance_envelope,
    write_performance_evidence,
)


class PerformanceContractTests(unittest.TestCase):
    def test_fixture_identity_is_deterministic(self) -> None:
        first = build_synthetic_fixture(rule_count=10, match_count=3)
        second = build_synthetic_fixture(rule_count=10, match_count=3)
        self.assertEqual(first.sha256, second.sha256)
        self.assertEqual(first.text, second.text)
        self.assertEqual(
            first.verified_policy.payload_sha256,
            second.verified_policy.payload_sha256,
        )

    def test_invalid_fixture_and_budget_values_fail_closed(self) -> None:
        for invalid in (0, -1, True, 1.5):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                build_synthetic_fixture(rule_count=invalid)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            build_synthetic_fixture(rule_count=2, match_count=3)
        for invalid in (0, -1, float("inf"), float("nan"), True):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                PerformanceBudget(normalisation_p95_ms=invalid)  # type: ignore[arg-type]

    def test_small_contract_writes_verifiable_evidence(self) -> None:
        envelope = run_performance_contract(
            warmup_count=1,
            construction_samples=2,
            normalisation_samples=2,
        )
        self.assertEqual(envelope["evidence"]["status"], "PASS")
        self.assertNotIn("SYNTH-SKU", str(envelope))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.json"
            write_performance_evidence(envelope, path)
            self.assertEqual(load_performance_evidence(path), envelope)

    def test_tampering_is_rejected(self) -> None:
        envelope = run_performance_contract(
            warmup_count=1,
            construction_samples=1,
            normalisation_samples=1,
        )
        changed = copy.deepcopy(envelope)
        changed["evidence"]["metrics"]["normalisation_p95_ms"] = 0
        with self.assertRaises(ValueError):
            verify_performance_envelope(changed)

    def test_failed_budget_is_explicit_and_blocks_promotion(self) -> None:
        envelope = run_performance_contract(
            budget=PerformanceBudget(minimum_throughput_per_second=10**12),
            warmup_count=1,
            construction_samples=1,
            normalisation_samples=1,
        )
        self.assertEqual(envelope["evidence"]["status"], "FAIL")
        self.assertFalse(envelope["evidence"]["checks"]["minimum_throughput_per_second"])
        with self.assertRaises(RuntimeError):
            assert_performance_passed(envelope)

    def test_sample_counts_are_positive_integers(self) -> None:
        with self.assertRaises(ValueError):
            run_performance_contract(warmup_count=0)


if __name__ == "__main__":
    unittest.main()
