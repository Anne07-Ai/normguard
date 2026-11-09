import base64
import hashlib
import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from normguard.contracts import PolicyClass, PolicyRule
from normguard.pilot import (
    DataClassification,
    OperationalDrill,
    PilotCase,
    PilotConfig,
    run_controlled_pilot,
    verify_pilot_envelope,
    write_pilot_artifacts,
)
from normguard.safety_contracts import (
    EvidenceStatus,
    PolicyBundle,
    PolicyPayload,
    PolicyProvenance,
    ReviewerApproval,
    SchemaCompatibility,
    SignatureEnvelope,
    canonical_json_bytes,
)

NOW = datetime(2026, 11, 9, 6, 0, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[1]


def _documents() -> tuple[bytes, bytes, bytes]:
    key = ec.generate_private_key(ec.SECP256R1())
    payload = PolicyPayload(
        policy_id="synthetic-pilot",
        policy_version="1.0.0",
        compatibility=SchemaCompatibility(1, 1),
        provenance=PolicyProvenance(
            "synthetic-pilot", "v1", "2026-11-09T05:00:00Z"
        ),
        rules=(
            PolicyRule("sku", PolicyClass.EXACT_PRESERVE, "exact", "SYNTH-SKU-42"),
            PolicyRule(
                "alias",
                PolicyClass.APPROVED_ALIAS,
                "exact",
                "SYNTHETIC LIMITED",
                approved_output="Synthetic",
            ),
            PolicyRule(
                "review",
                PolicyClass.REVIEW_OR_ABSTAIN,
                "exact",
                "AMBIGUOUS-SYNTH",
            ),
        ),
    )
    signature = key.sign(
        canonical_json_bytes(payload.to_dict()), ec.ECDSA(hashes.SHA256())
    )
    approvals = tuple(
        ReviewerApproval(name, "reviewer", "2026-11-09T05:30:00Z", payload.sha256)
        for name in ("reviewer-one", "reviewer-two")
    )
    bundle = PolicyBundle(
        payload,
        payload.sha256,
        approvals,
        "1.0.0",
        SignatureEnvelope(
            "ECDSA_P256_SHA256",
            "synthetic-policy-key",
            base64.b64encode(signature).decode(),
        ),
    )
    public_der = key.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    trust = {
        "schema_version": 1,
        "keys": [
            {
                "key_id": "synthetic-policy-key",
                "algorithm": "ECDSA_P256_SHA256",
                "public_key_spki_der_base64": base64.b64encode(public_der).decode(),
                "public_key_sha256": hashlib.sha256(public_der).hexdigest(),
                "allowed_policy_ids": ["synthetic-pilot"],
                "status": "active",
                "not_before": "2026-11-09T00:00:00Z",
                "not_after": "2027-11-09T00:00:00Z",
            }
        ],
    }
    activation = {
        "schema_version": 1,
        "policy_id": "synthetic-pilot",
        "minimum_version": "1.0.0",
        "updated_at": "2026-11-09T05:45:00Z",
        "change_id": "CHG-SYNTH-001",
        "allow_prerelease": False,
    }
    return (
        bundle.canonical_bytes(),
        canonical_json_bytes(trust),
        canonical_json_bytes(activation),
    )


def _cases() -> tuple[PilotCase, ...]:
    return (
        PilotCase(
            "SYNTH-SKU-42 SYNTHETIC LIMITED",
            "SYNTH-SKU-42 Synthetic",
            EvidenceStatus.SUCCESS,
        ),
        PilotCase(
            "AMBIGUOUS-SYNTH",
            "AMBIGUOUS-SYNTH",
            EvidenceStatus.REVIEW_REQUIRED,
        ),
    )


def _config() -> PilotConfig:
    return PilotConfig(
        source_commit="a" * 40,
        artifact_sha256="b" * 64,
        data_classification=DataClassification.SYNTHETIC,
        latency_p95_budget_ms=2.0,
        minimum_throughput_per_second=100.0,
    )


def _drills() -> tuple[OperationalDrill, ...]:
    return (
        OperationalDrill("rollback", True, "c" * 64),
        OperationalDrill("key-compromise", True, "d" * 64),
    )


class StepClock:
    def __init__(self) -> None:
        self.value = 0

    def __call__(self) -> int:
        current = self.value
        self.value += 1_000_000
        return current


def _run(**overrides: object) -> dict[str, object]:
    bundle, trust, activation = _documents()
    values = {
        "policy_bundle": bundle,
        "trust_store": trust,
        "activation_state": activation,
        "verification_time": NOW,
        "cases": _cases(),
        "config": _config(),
        "drills": _drills(),
        "monotonic_ns": StepClock(),
    }
    values.update(overrides)
    return run_controlled_pilot(**values)  # type: ignore[arg-type]


class ControlledPilotTests(unittest.TestCase):
    def test_committed_example_is_verifiable_and_contains_no_fixture_text(self) -> None:
        evidence_path = (
            ROOT / "examples/pilots/controlled-pilot-evidence.example.json"
        )
        report_path = ROOT / "examples/pilots/controlled-pilot-report.example.md"
        envelope = json.loads(evidence_path.read_text(encoding="utf-8"))
        verify_pilot_envelope(envelope)
        encoded = evidence_path.read_bytes() + report_path.read_bytes()
        for case in _cases():
            self.assertNotIn(case.text.encode(), encoded)
            self.assertNotIn(case.expected_output.encode(), encoded)
        self.assertIn(envelope["record_sha256"], report_path.read_text())

    def test_passes_all_eight_gates_and_is_digest_verifiable(self) -> None:
        envelope = _run()
        verify_pilot_envelope(envelope)
        record = envelope["record"]
        self.assertIsInstance(record, dict)
        assert isinstance(record, dict)
        self.assertEqual(record["decision"], "PASS")
        self.assertEqual(len(record["gates"]), 8)
        self.assertEqual(
            {gate["status"] for gate in record["gates"]},  # type: ignore[index]
            {"PASS"},
        )
        self.assertEqual(record["sealed_data_status"], (
            "development-and-official-test-not-read-or-transformed"
        ))

    def test_semantic_evidence_is_reproducible_with_frozen_clock(self) -> None:
        first = _run()
        second = _run()
        first_record = first["record"]
        second_record = second["record"]
        assert isinstance(first_record, dict) and isinstance(second_record, dict)
        # Signing uses non-deterministic ECDSA, but evidence binds the same payload.
        self.assertEqual(first_record, second_record)
        self.assertEqual(first["record_sha256"], second["record_sha256"])

    def test_missing_drill_or_functional_mismatch_stops_pilot(self) -> None:
        missing_drill = _run(drills=(_drills()[0],))
        mismatch = _run(
            cases=(
                PilotCase(
                    "SYNTH-SKU-42",
                    "incorrect-output",
                    EvidenceStatus.SUCCESS,
                ),
                _cases()[1],
            )
        )
        for envelope in (missing_drill, mismatch):
            record = envelope["record"]
            assert isinstance(record, dict)
            self.assertEqual(record["decision"], "STOP")

    def test_evidence_contains_no_raw_input_or_output(self) -> None:
        envelope = _run()
        encoded = canonical_json_bytes(envelope)
        for case in _cases():
            self.assertNotIn(case.text.encode(), encoded)
            self.assertNotIn(case.expected_output.encode(), encoded)

    def test_tampering_and_unknown_envelope_fields_fail_closed(self) -> None:
        envelope = _run()
        envelope["record_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "digest mismatch"):
            verify_pilot_envelope(envelope)
        envelope = _run()
        envelope["unknown"] = True
        with self.assertRaisesRegex(ValueError, "keys differ"):
            verify_pilot_envelope(envelope)

        envelope = _run()
        record = envelope["record"]
        assert isinstance(record, dict)
        counts = record["counts"]
        assert isinstance(counts, dict)
        counts["unknown"] = 1
        envelope["record_sha256"] = hashlib.sha256(
            canonical_json_bytes(record)
        ).hexdigest()
        with self.assertRaisesRegex(ValueError, "counts differ"):
            verify_pilot_envelope(envelope)

        envelope = _run(drills=(_drills()[0],))
        record = envelope["record"]
        assert isinstance(record, dict)
        record["decision"] = "PASS"
        envelope["record_sha256"] = hashlib.sha256(
            canonical_json_bytes(record)
        ).hexdigest()
        with self.assertRaisesRegex(ValueError, "contradicts"):
            verify_pilot_envelope(envelope)

    def test_artifact_writer_emits_json_and_scope_limited_report(self) -> None:
        envelope = _run()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            write_pilot_artifacts(envelope, output)
            loaded = json.loads(
                (output / "controlled-pilot-evidence.json").read_text()
            )
            verify_pilot_envelope(loaded)
            report = (output / "controlled-pilot-report.md").read_text()
            self.assertIn("**Decision:** `PASS`", report)
            self.assertIn("no hosted multi-tenant deployment", report)


if __name__ == "__main__":
    unittest.main()
