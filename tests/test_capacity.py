import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from normguard.capacity import (
    CAPACITY_PROTOCOL_VERSION,
    CapacityDecision,
    CapacityIdentity,
    CapacityProfile,
    CapacitySamples,
    CapacityScope,
    assert_capacity_passed,
    load_capacity_evidence,
    run_capacity_harness,
    smoke_profile,
    verify_capacity_envelope,
    write_capacity_evidence,
)
from normguard.safety_contracts import canonical_json_bytes


def identity() -> CapacityIdentity:
    return CapacityIdentity("a" * 40, "b" * 64, "c" * 64, "d" * 64)


def passing_samples(profile: CapacityProfile, operation: object) -> CapacitySamples:
    del operation
    return CapacitySamples(
        durations_ns=(1_000_000,) * profile.measured_requests,
        error_count=0,
        elapsed_ns=1_000_000_000,
        peak_traced_bytes=1_000,
        process_cpu_seconds=0.1,
    )


class CapacityContractTests(unittest.TestCase):
    def test_smoke_evidence_is_deterministic_private_and_verifiable(self) -> None:
        first = run_capacity_harness(
            profile=smoke_profile(),
            identity=identity(),
            operation=lambda: None,
            sample_runner=passing_samples,
        )
        second = run_capacity_harness(
            profile=smoke_profile(),
            identity=identity(),
            operation=lambda: None,
            sample_runner=passing_samples,
        )
        self.assertEqual(first, second)
        self.assertEqual(first["evidence"]["decision"], CapacityDecision.PASS.value)
        self.assertEqual(first["evidence"]["protocol"], CAPACITY_PROTOCOL_VERSION)
        self.assertNotIn("SYNTH-SKU", str(first))
        verify_capacity_envelope(first)

    def test_stop_is_derived_from_failed_checks_and_blocks_promotion(self) -> None:
        def failures(profile: CapacityProfile, operation: object) -> CapacitySamples:
            del operation
            return CapacitySamples(
                durations_ns=(200_000_000,) * profile.measured_requests,
                error_count=profile.measured_requests,
                elapsed_ns=1_000_000_000,
                peak_traced_bytes=100_000_000,
                process_cpu_seconds=100,
            )

        envelope = run_capacity_harness(
            profile=smoke_profile(),
            identity=identity(),
            operation=lambda: None,
            sample_runner=failures,
        )
        self.assertEqual(envelope["evidence"]["decision"], CapacityDecision.STOP.value)
        with self.assertRaises(RuntimeError):
            assert_capacity_passed(envelope)

    def test_tampering_unknown_fields_and_decision_drift_fail_closed(self) -> None:
        envelope = run_capacity_harness(
            profile=smoke_profile(),
            identity=identity(),
            operation=lambda: None,
            sample_runner=passing_samples,
        )
        for mutation in (
            "metric",
            "field",
            "decision",
            "nested",
            "throughput",
            "latency",
            "limitations",
        ):
            changed = copy.deepcopy(envelope)
            if mutation == "metric":
                changed["evidence"]["metrics"]["error_count"] = 9
            elif mutation == "field":
                changed["evidence"]["raw_text"] = "must not be accepted"
            elif mutation == "decision":
                changed["evidence"]["decision"] = "STOP"
                changed["evidence_sha256"] = hashlib.sha256(
                    canonical_json_bytes(changed["evidence"])
                ).hexdigest()
            elif mutation == "nested":
                changed["evidence"]["metrics"]["error_count"] = 1
                changed["evidence_sha256"] = hashlib.sha256(
                    canonical_json_bytes(changed["evidence"])
                ).hexdigest()
            elif mutation == "throughput":
                changed["evidence"]["metrics"]["throughput_per_second"] = 41
                changed["evidence_sha256"] = hashlib.sha256(
                    canonical_json_bytes(changed["evidence"])
                ).hexdigest()
            elif mutation == "latency":
                changed["evidence"]["metrics"]["latency_p50_ms"] = 2
                changed["evidence_sha256"] = hashlib.sha256(
                    canonical_json_bytes(changed["evidence"])
                ).hexdigest()
            else:
                changed["evidence"]["limitations"].append("unreviewed claim")
                changed["evidence_sha256"] = hashlib.sha256(
                    canonical_json_bytes(changed["evidence"])
                ).hexdigest()
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                verify_capacity_envelope(changed)

    def test_representative_profile_requires_sustained_bounded_workload(self) -> None:
        base = smoke_profile()
        with self.assertRaises(ValueError):
            CapacityProfile(
                **{
                    **base.to_dict(),
                    "scope": CapacityScope.REPRESENTATIVE,
                    "minimum_duration_seconds": 299,
                    "measured_requests": 1_000,
                }
            )
        with self.assertRaises(ValueError):
            CapacityProfile(
                **{
                    **base.to_dict(),
                    "scope": CapacityScope.REPRESENTATIVE,
                    "minimum_duration_seconds": 300,
                    "measured_requests": 999,
                }
            )

    def test_invalid_identities_profiles_and_sample_counts_fail_closed(self) -> None:
        with self.assertRaises(ValueError):
            CapacityIdentity("short", "b" * 64, "c" * 64, "d" * 64)
        with self.assertRaises(ValueError):
            CapacityProfile(
                **{
                    **smoke_profile().to_dict(),
                    "scope": CapacityScope.LOCAL_SMOKE,
                    "concurrency": 0,
                }
            )

        def wrong_count(profile: CapacityProfile, operation: object) -> CapacitySamples:
            del operation
            return CapacitySamples((1,), 0, 1, 1, 0)

        with self.assertRaises(ValueError):
            run_capacity_harness(
                profile=smoke_profile(),
                identity=identity(),
                operation=lambda: None,
                sample_runner=wrong_count,
            )

    def test_canonical_file_round_trip(self) -> None:
        envelope = run_capacity_harness(
            profile=smoke_profile(),
            identity=identity(),
            operation=lambda: None,
            sample_runner=passing_samples,
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capacity.json"
            write_capacity_evidence(envelope, path)
            self.assertEqual(load_capacity_evidence(path), envelope)

    def test_release_capacity_blocker_remains_open(self) -> None:
        root = Path(__file__).resolve().parents[1]
        readiness = json.loads(
            (root / "docs/releases/safety-core-phase-3-readiness.json").read_text()
        )
        blocker = next(item for item in readiness["blockers"] if item["id"] == "B-003")
        self.assertEqual(blocker["status"], "OPEN")
        self.assertEqual(blocker["required_for"], "production SLA or capacity claim")


if __name__ == "__main__":
    unittest.main()
