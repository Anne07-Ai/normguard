"""Execute the first synthetic NG-PILOT-001 controlled pilot."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from normguard import (
    DataClassification,
    OperationalDrill,
    PilotCase,
    PilotConfig,
    SDKError,
    build_safety_core,
    run_controlled_pilot,
    verify_pilot_envelope,
    write_pilot_artifacts,
)
from normguard.contracts import PolicyClass, PolicyRule
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

SYNTHETIC_PILOT_VERSION = "synthetic-controlled-v1"
VERIFICATION_TIME = datetime(2026, 11, 9, 7, 0, tzinfo=UTC)
MAX_WHEEL_BYTES = 10_000_000


def _synthetic_documents() -> tuple[bytes, bytes, bytes]:
    private_key = ec.generate_private_key(ec.SECP256R1())
    payload = PolicyPayload(
        policy_id="synthetic-controlled-pilot",
        policy_version="1.0.0",
        compatibility=SchemaCompatibility(1, 1),
        provenance=PolicyProvenance(
            "synthetic-pilot",
            SYNTHETIC_PILOT_VERSION,
            "2026-11-09T06:30:00Z",
        ),
        rules=(
            PolicyRule(
                "synthetic-sku",
                PolicyClass.EXACT_PRESERVE,
                "exact",
                "SYNTH-SKU-42",
                source="synthetic-controlled-pilot",
            ),
            PolicyRule(
                "synthetic-alias",
                PolicyClass.APPROVED_ALIAS,
                "exact",
                "SYNTHETIC LIMITED",
                approved_output="Synthetic",
                source="synthetic-controlled-pilot",
            ),
            PolicyRule(
                "synthetic-review",
                PolicyClass.REVIEW_OR_ABSTAIN,
                "exact",
                "AMBIGUOUS-SYNTH",
                source="synthetic-controlled-pilot",
            ),
        ),
    )
    signature = private_key.sign(
        canonical_json_bytes(payload.to_dict()),
        ec.ECDSA(hashes.SHA256()),
    )
    approvals = tuple(
        ReviewerApproval(
            reviewer,
            "synthetic-reviewer",
            "2026-11-09T06:40:00Z",
            payload.sha256,
        )
        for reviewer in ("synthetic-reviewer-one", "synthetic-reviewer-two")
    )
    bundle = PolicyBundle(
        payload,
        payload.sha256,
        approvals,
        "1.0.0",
        SignatureEnvelope(
            "ECDSA_P256_SHA256",
            "ephemeral-synthetic-key",
            base64.b64encode(signature).decode(),
        ),
    )
    public_der = private_key.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    trust_store = {
        "schema_version": 1,
        "keys": [
            {
                "key_id": "ephemeral-synthetic-key",
                "algorithm": "ECDSA_P256_SHA256",
                "public_key_spki_der_base64": base64.b64encode(public_der).decode(),
                "public_key_sha256": hashlib.sha256(public_der).hexdigest(),
                "allowed_policy_ids": ["synthetic-controlled-pilot"],
                "status": "active",
                "not_before": "2026-11-09T00:00:00Z",
                "not_after": "2027-11-09T00:00:00Z",
            }
        ],
    }
    activation = {
        "schema_version": 1,
        "policy_id": "synthetic-controlled-pilot",
        "minimum_version": "1.0.0",
        "updated_at": "2026-11-09T06:45:00Z",
        "change_id": "SYNTHETIC-PILOT-001",
        "allow_prerelease": False,
    }
    return (
        bundle.canonical_bytes(),
        canonical_json_bytes(trust_store),
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


def _drill_digest(value: dict[str, object]) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _run_operational_drills(
    policy_bundle: bytes,
    trust_store: bytes,
    activation_state: bytes,
) -> tuple[OperationalDrill, ...]:
    known_good = build_safety_core(
        policy_bundle,
        trust_store,
        activation_state,
        verification_time=VERIFICATION_TIME,
    )
    before = known_good.normalise(_cases()[0].text)

    activation = json.loads(activation_state)
    activation["minimum_version"] = "9999.0.0"
    rollback_code = None
    try:
        build_safety_core(
            policy_bundle,
            trust_store,
            canonical_json_bytes(activation),
            verification_time=VERIFICATION_TIME,
        )
    except SDKError as exc:
        rollback_code = exc.code
    after_rollback = known_good.normalise(_cases()[0].text)
    rollback_result = {
        "schema_version": 1,
        "scenario": "rollback-candidate-rejected",
        "candidate_rejected": rollback_code is not None,
        "last_known_good_unchanged": after_rollback == before,
        "bounded_error_code": rollback_code,
    }

    revoked_trust = json.loads(trust_store)
    revoked_trust["keys"][0]["status"] = "revoked"
    revoked_code = None
    try:
        build_safety_core(
            policy_bundle,
            canonical_json_bytes(revoked_trust),
            activation_state,
            verification_time=VERIFICATION_TIME,
        )
    except SDKError as exc:
        revoked_code = exc.code
    after_revocation = known_good.normalise(_cases()[0].text)
    key_result = {
        "schema_version": 1,
        "scenario": "revoked-key-candidate-rejected",
        "candidate_rejected": revoked_code is not None,
        "last_known_good_unchanged": after_revocation == before,
        "bounded_error_code": revoked_code,
    }
    return (
        OperationalDrill(
            "rollback",
            rollback_result["candidate_rejected"] is True
            and rollback_result["last_known_good_unchanged"] is True,
            _drill_digest(rollback_result),
        ),
        OperationalDrill(
            "key-compromise",
            key_result["candidate_rejected"] is True
            and key_result["last_known_good_unchanged"] is True,
            _drill_digest(key_result),
        ),
    )


def _artifact_sha256(wheel: Path) -> str:
    if wheel.is_symlink() or not wheel.is_file():
        raise ValueError("wheel must be a regular file")
    size = wheel.stat().st_size
    if size < 1 or size > MAX_WHEEL_BYTES:
        raise ValueError("wheel size is outside the controlled boundary")
    digest = hashlib.sha256()
    with wheel.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def run_synthetic_pilot(
    *,
    wheel: Path,
    source_commit: str,
    output_dir: Path,
    monotonic_ns: Callable[[], int] = time.perf_counter_ns,
) -> dict[str, object]:
    artifact_sha256 = _artifact_sha256(wheel)
    policy_bundle, trust_store, activation_state = _synthetic_documents()
    drills = _run_operational_drills(
        policy_bundle,
        trust_store,
        activation_state,
    )
    envelope = run_controlled_pilot(
        policy_bundle=policy_bundle,
        trust_store=trust_store,
        activation_state=activation_state,
        verification_time=VERIFICATION_TIME,
        cases=_cases(),
        config=PilotConfig(
            source_commit=source_commit,
            artifact_sha256=artifact_sha256,
            data_classification=DataClassification.SYNTHETIC,
            repeat_count=5,
            latency_p95_budget_ms=100.0,
            minimum_throughput_per_second=10.0,
        ),
        drills=drills,
        monotonic_ns=monotonic_ns,
    )
    verify_pilot_envelope(envelope)
    write_pilot_artifacts(envelope, output_dir)
    record = envelope["record"]
    assert isinstance(record, dict)
    if record["decision"] != "PASS":
        raise RuntimeError("controlled pilot returned STOP")
    return envelope


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the synthetic NormGuard controlled pilot"
    )
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        envelope = run_synthetic_pilot(
            wheel=args.wheel,
            source_commit=args.source_commit,
            output_dir=args.output_dir,
        )
    except (OSError, RuntimeError, SDKError, ValueError) as exc:
        print(f"controlled pilot failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    record = envelope["record"]
    assert isinstance(record, dict)
    identities = record["identities"]
    assert isinstance(identities, dict)
    print(
        json.dumps(
            {
                "decision": record["decision"],
                "protocol": record["protocol"],
                "source_commit": identities["source_commit"],
                "artifact_sha256": identities["artifact_sha256"],
                "evidence_sha256": envelope["record_sha256"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
