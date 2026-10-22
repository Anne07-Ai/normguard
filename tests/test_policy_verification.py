import base64
import copy
import hashlib
import unittest
from datetime import UTC, datetime, timedelta

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from normguard.contracts import PolicyClass, PolicyRule
from normguard.policy_verification import (
    ALGORITHM,
    ActivationState,
    PolicyVerificationError,
    RollbackAuthorization,
    TrustStore,
    VerificationCode,
    verify_policy_bundle,
)
from normguard.safety_contracts import (
    PolicyBundle,
    PolicyPayload,
    PolicyProvenance,
    ReviewerApproval,
    SchemaCompatibility,
    SignatureEnvelope,
    canonical_json_bytes,
)

NOW = datetime(2026, 10, 22, 10, 0, tzinfo=UTC)


def _payload(version: str = "1.4.0") -> PolicyPayload:
    return PolicyPayload(
        policy_id="retail-en",
        policy_version=version,
        compatibility=SchemaCompatibility(1, 1),
        provenance=PolicyProvenance("review-register", "2026.10", "2026-10-22T09:00:00Z"),
        rules=(PolicyRule("sku", PolicyClass.EXACT_PRESERVE, "exact", "SKU-42"),),
    )


def _bundle(private_key: ec.EllipticCurvePrivateKey, version: str = "1.4.0") -> PolicyBundle:
    payload = _payload(version)
    signature = private_key.sign(
        canonical_json_bytes(payload.to_dict()),
        ec.ECDSA(hashes.SHA256()),
    )
    approvals = tuple(
        ReviewerApproval(item, "reviewer", "2026-10-22T09:30:00Z", payload.sha256)
        for item in ("reviewer-one", "reviewer-two")
    )
    return PolicyBundle(
        payload,
        payload.sha256,
        approvals,
        "1.4.0",
        SignatureEnvelope(ALGORITHM, "policy-key", base64.b64encode(signature).decode()),
    )


def _key_row(
    private_key: ec.EllipticCurvePrivateKey,
    *,
    key_id: str = "policy-key",
    status: str = "active",
    policies: list[str] | None = None,
) -> dict[str, object]:
    der = private_key.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return {
        "key_id": key_id,
        "algorithm": ALGORITHM,
        "public_key_spki_der_base64": base64.b64encode(der).decode(),
        "public_key_sha256": hashlib.sha256(der).hexdigest(),
        "allowed_policy_ids": policies or ["retail-en"],
        "status": status,
        "not_before": "2026-10-22T00:00:00Z",
        "not_after": "2027-10-22T00:00:00Z",
    }


def _trust_store(
    private_key: ec.EllipticCurvePrivateKey,
    **overrides: object,
) -> TrustStore:
    row = _key_row(private_key)
    row.update(overrides)
    return TrustStore.from_dict({"schema_version": 1, "keys": [row]})


def _activation(minimum: str = "1.4.0") -> ActivationState:
    return ActivationState.from_dict(
        {
            "schema_version": 1,
            "policy_id": "retail-en",
            "minimum_version": minimum,
            "updated_at": "2026-10-22T09:45:00Z",
            "change_id": "CHG-12345",
            "allow_prerelease": False,
        }
    )


def _rollback(
    private_key: ec.EllipticCurvePrivateKey,
    bundle: PolicyBundle,
) -> RollbackAuthorization:
    signed = {
        "schema_version": 1,
        "policy_id": "retail-en",
        "target_version": bundle.payload.policy_version,
        "target_payload_sha256": bundle.payload_sha256,
        "change_id": "INC-42",
        "reason_code": "availability-recovery",
        "issued_at": "2026-10-22T09:50:00Z",
        "expires_at": "2026-10-22T10:20:00Z",
        "approver_ids": ["operator-one", "operator-two"],
        "algorithm": ALGORITHM,
        "key_id": "rollback-key",
    }
    signature = private_key.sign(
        canonical_json_bytes(signed),
        ec.ECDSA(hashes.SHA256()),
    )
    return RollbackAuthorization.from_dict(
        {**signed, "signature": base64.b64encode(signature).decode()}
    )


class PolicyVerificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.bundle = _bundle(self.key)
        self.store = _trust_store(self.key)

    def verify(self, **kwargs: object):
        return verify_policy_bundle(
            self.bundle,
            self.store,
            _activation(),
            now=NOW,
            **kwargs,
        )

    def test_valid_bundle_verifies_offline_and_result_is_immutable(self) -> None:
        verified = self.verify()
        self.assertEqual(verified.payload_sha256, self.bundle.payload_sha256)
        self.assertEqual(verified.signing_key_id, "policy-key")
        self.assertFalse(verified.rollback_authorized)
        with self.assertRaises(AttributeError):
            verified.signing_key_id = "replacement"  # type: ignore[misc]

    def test_unsigned_and_signature_mutation_fail_closed(self) -> None:
        unsigned = PolicyBundle(
            self.bundle.payload,
            self.bundle.payload_sha256,
            self.bundle.approvals,
            self.bundle.activation_floor,
            None,
        )
        with self.assertRaises(PolicyVerificationError) as caught:
            verify_policy_bundle(unsigned, self.store, _activation(), now=NOW)
        self.assertEqual(caught.exception.code, VerificationCode.SIGNATURE_REQUIRED)

        signature = self.bundle.signature
        assert signature is not None
        raw = bytearray(base64.b64decode(signature.signature))
        raw[-1] ^= 1
        mutated = PolicyBundle(
            self.bundle.payload,
            self.bundle.payload_sha256,
            self.bundle.approvals,
            self.bundle.activation_floor,
            SignatureEnvelope(
                ALGORITHM,
                signature.key_id,
                base64.b64encode(raw).decode(),
            ),
        )
        with self.assertRaises(PolicyVerificationError) as caught:
            verify_policy_bundle(mutated, self.store, _activation(), now=NOW)
        self.assertEqual(caught.exception.code, VerificationCode.INVALID_SIGNATURE)

    def test_unknown_algorithm_key_and_policy_scope_are_typed(self) -> None:
        signature = self.bundle.signature
        assert signature is not None
        cases = (
            (
                SignatureEnvelope("RSA", signature.key_id, signature.signature),
                self.store,
                VerificationCode.UNSUPPORTED_ALGORITHM,
            ),
            (
                SignatureEnvelope(ALGORITHM, "unknown-key", signature.signature),
                self.store,
                VerificationCode.UNKNOWN_KEY,
            ),
            (
                signature,
                _trust_store(self.key, allowed_policy_ids=["finance-en"]),
                VerificationCode.POLICY_NOT_ALLOWED,
            ),
        )
        for envelope, store, expected in cases:
            changed = PolicyBundle(
                self.bundle.payload,
                self.bundle.payload_sha256,
                self.bundle.approvals,
                self.bundle.activation_floor,
                envelope,
            )
            with self.subTest(expected=expected):
                with self.assertRaises(PolicyVerificationError) as caught:
                    verify_policy_bundle(changed, store, _activation(), now=NOW)
                self.assertEqual(caught.exception.code, expected)

    def test_disabled_revoked_and_outside_validity_keys_fail_closed(self) -> None:
        cases = (
            (_trust_store(self.key, status="disabled"), VerificationCode.KEY_NOT_ACTIVE),
            (_trust_store(self.key, status="revoked"), VerificationCode.KEY_REVOKED),
            (
                _trust_store(self.key, not_before="2026-10-23T00:00:00Z"),
                VerificationCode.KEY_OUTSIDE_VALIDITY,
            ),
            (
                _trust_store(self.key, not_after="2026-10-22T09:00:00Z"),
                VerificationCode.KEY_OUTSIDE_VALIDITY,
            ),
        )
        for store, expected in cases:
            with self.subTest(expected=expected):
                with self.assertRaises(PolicyVerificationError) as caught:
                    verify_policy_bundle(self.bundle, store, _activation(), now=NOW)
                self.assertEqual(caught.exception.code, expected)

    def test_trust_store_rejects_duplicate_fingerprint_wrong_curve_and_bad_base64(self) -> None:
        row = _key_row(self.key)
        duplicate = copy.deepcopy(row)
        duplicate["key_id"] = "second-key"
        with self.assertRaisesRegex(ValueError, "unique"):
            TrustStore.from_dict({"schema_version": 1, "keys": [row, duplicate]})

        wrong_curve = ec.generate_private_key(ec.SECP384R1())
        with self.assertRaisesRegex(ValueError, "P-256"):
            TrustStore.from_dict(
                {"schema_version": 1, "keys": [_key_row(wrong_curve)]}
            )

        row["public_key_spki_der_base64"] = "***"
        with self.assertRaisesRegex(ValueError, "invalid"):
            TrustStore.from_dict({"schema_version": 1, "keys": [row]})

    def test_error_text_does_not_expose_policy_signature_or_key_material(self) -> None:
        signature = self.bundle.signature
        assert signature is not None
        changed = PolicyBundle(
            self.bundle.payload,
            self.bundle.payload_sha256,
            self.bundle.approvals,
            self.bundle.activation_floor,
            SignatureEnvelope(ALGORITHM, "unknown-key", signature.signature),
        )
        with self.assertRaises(PolicyVerificationError) as caught:
            verify_policy_bundle(changed, self.store, _activation(), now=NOW)
        message = str(caught.exception)
        self.assertNotIn("SKU-42", message)
        self.assertNotIn(signature.signature, message)
        self.assertNotIn(self.store.keys[0].public_key_spki_der_base64, message)

    def test_below_floor_requires_valid_exact_target_rollback(self) -> None:
        old_bundle = _bundle(self.key, "1.3.0")
        rollback_key = ec.generate_private_key(ec.SECP256R1())
        authorization = _rollback(rollback_key, old_bundle)
        rollback_store = TrustStore.from_dict(
            {
                "schema_version": 1,
                "keys": [_key_row(rollback_key, key_id="rollback-key")],
            }
        )
        verified = verify_policy_bundle(
            old_bundle,
            self.store,
            _activation(),
            now=NOW,
            rollback_authorization=authorization,
            rollback_trust_store=rollback_store,
        )
        self.assertTrue(verified.rollback_authorized)

        with self.assertRaises(PolicyVerificationError) as caught:
            verify_policy_bundle(old_bundle, self.store, _activation(), now=NOW)
        self.assertEqual(caught.exception.code, VerificationCode.ROLLBACK_REJECTED)

    def test_rollback_is_two_person_exact_target_and_time_bounded(self) -> None:
        old_bundle = _bundle(self.key, "1.3.0")
        rollback_key = ec.generate_private_key(ec.SECP256R1())
        authorization = _rollback(rollback_key, old_bundle)
        rollback_store = TrustStore.from_dict(
            {
                "schema_version": 1,
                "keys": [_key_row(rollback_key, key_id="rollback-key")],
            }
        )
        with self.assertRaises(PolicyVerificationError) as caught:
            verify_policy_bundle(
                old_bundle,
                self.store,
                _activation(),
                now=NOW + timedelta(hours=1),
                rollback_authorization=authorization,
                rollback_trust_store=rollback_store,
            )
        self.assertEqual(
            caught.exception.code,
            VerificationCode.ROLLBACK_AUTHORIZATION_INVALID,
        )

        value = {
            **authorization.signed_dict(),
            "signature": authorization.signature,
            "approver_ids": ["operator-one", "operator-one"],
        }
        with self.assertRaisesRegex(ValueError, "distinct"):
            RollbackAuthorization.from_dict(value)

    def test_prerelease_requires_explicit_activation_permission(self) -> None:
        bundle = _bundle(self.key, "1.4.1-rc.1")
        with self.assertRaises(PolicyVerificationError) as caught:
            verify_policy_bundle(bundle, self.store, _activation(), now=NOW)
        self.assertEqual(caught.exception.code, VerificationCode.ROLLBACK_REJECTED)


if __name__ == "__main__":
    unittest.main()
