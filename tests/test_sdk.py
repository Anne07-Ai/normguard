import base64
import hashlib
import json
import unittest
from datetime import UTC, datetime

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from normguard.contracts import PolicyClass, PolicyRule
from normguard.runtime import RuntimeLimits
from normguard.safety_contracts import (
    PolicyBundle,
    PolicyPayload,
    PolicyProvenance,
    ReviewerApproval,
    SchemaCompatibility,
    SignatureEnvelope,
    canonical_json_bytes,
)
from normguard.sdk import (
    SDK_SCHEMA_VERSION,
    SDK_VERSION,
    SafetyCore,
    SDKDocumentLimits,
    SDKError,
    build_safety_core,
    verify_policy,
)

NOW = datetime(2026, 10, 22, 16, 30, tzinfo=UTC)


def _json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _documents() -> tuple[bytes, bytes, bytes]:
    private_key = ec.generate_private_key(ec.SECP256R1())
    payload = PolicyPayload(
        policy_id="retail-en",
        policy_version="1.0.0",
        compatibility=SchemaCompatibility(1, 1),
        provenance=PolicyProvenance(
            "review-register",
            "2026.10",
            "2026-10-22T15:00:00Z",
        ),
        rules=(
            PolicyRule("sku", PolicyClass.EXACT_PRESERVE, "exact", "SKU-42"),
            PolicyRule(
                "alias",
                PolicyClass.APPROVED_ALIAS,
                "exact",
                "ACME LTD",
                approved_output="Acme",
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
            "reviewer",
            "2026-10-22T15:30:00Z",
            payload.sha256,
        )
        for reviewer in ("reviewer-one", "reviewer-two")
    )
    bundle = PolicyBundle(
        payload,
        payload.sha256,
        approvals,
        "1.0.0",
        SignatureEnvelope(
            "ECDSA_P256_SHA256",
            "policy-key",
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
                "key_id": "policy-key",
                "algorithm": "ECDSA_P256_SHA256",
                "public_key_spki_der_base64": base64.b64encode(public_der).decode(),
                "public_key_sha256": hashlib.sha256(public_der).hexdigest(),
                "allowed_policy_ids": ["retail-en"],
                "status": "active",
                "not_before": "2026-10-22T00:00:00Z",
                "not_after": "2027-10-22T00:00:00Z",
            }
        ],
    }
    activation = {
        "schema_version": 1,
        "policy_id": "retail-en",
        "minimum_version": "1.0.0",
        "updated_at": "2026-10-22T16:00:00Z",
        "change_id": "CHG-123",
        "allow_prerelease": False,
    }
    return bundle.canonical_bytes(), _json_bytes(trust_store), _json_bytes(activation)


class SDKDocumentBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bundle, self.trust, self.activation = _documents()

    def test_sdk_version_is_explicit(self) -> None:
        self.assertEqual(SDK_VERSION, "0.1.0")
        self.assertEqual(SDK_SCHEMA_VERSION, 1)

    def test_document_limits_are_positive_and_immutable(self) -> None:
        with self.assertRaises(ValueError):
            SDKDocumentLimits(max_policy_bundle_bytes=0)
        limits = SDKDocumentLimits()
        with self.assertRaises(AttributeError):
            limits.max_policy_bundle_bytes = 1  # type: ignore[misc]

    def test_oversized_document_fails_before_parsing(self) -> None:
        limits = SDKDocumentLimits(max_policy_bundle_bytes=len(self.bundle) - 1)
        with self.assertRaises(SDKError) as caught:
            verify_policy(
                self.bundle,
                self.trust,
                self.activation,
                verification_time=NOW,
                document_limits=limits,
            )
        self.assertEqual(caught.exception.code, "DOCUMENT_TOO_LARGE")
        self.assertEqual(caught.exception.document, "policy_bundle")

    def test_invalid_utf8_duplicate_keys_nonfinite_and_nonobject_fail_closed(self) -> None:
        invalid_documents = (
            b"\xff",
            b'{"schema_version":1,"schema_version":1,"keys":[]}',
            b'{"schema_version":NaN,"keys":[]}',
            b"[]",
        )
        for document in invalid_documents:
            with (
                self.subTest(document=document),
                self.assertRaises(SDKError) as caught,
            ):
                verify_policy(
                    self.bundle,
                    document,
                    self.activation,
                    verification_time=NOW,
                )
            self.assertEqual(caught.exception.code, "INVALID_DOCUMENT")
            self.assertEqual(caught.exception.document, "trust_store")

    def test_unknown_nested_field_fails_closed(self) -> None:
        value = json.loads(self.bundle)
        value["payload"]["rules"][0]["untrusted"] = True
        with self.assertRaises(SDKError) as caught:
            verify_policy(
                _json_bytes(value),
                self.trust,
                self.activation,
                verification_time=NOW,
            )
        self.assertEqual(caught.exception.code, "INVALID_DOCUMENT")

    def test_bytes_are_required_at_the_untrusted_boundary(self) -> None:
        with self.assertRaises(SDKError) as caught:
            verify_policy(
                self.bundle.decode(),  # type: ignore[arg-type]
                self.trust,
                self.activation,
                verification_time=NOW,
            )
        self.assertEqual(caught.exception.code, "INVALID_DOCUMENT")
        self.assertEqual(caught.exception.document, "policy_bundle")


class SafetyCoreSDKTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bundle, self.trust, self.activation = _documents()

    def test_valid_documents_build_core_without_internal_imports(self) -> None:
        core = build_safety_core(
            self.bundle,
            self.trust,
            self.activation,
            verification_time=NOW,
        )
        result = core.normalise("SKU-42 ACME LTD")
        self.assertEqual(result.output_text, "SKU-42 Acme")
        self.assertEqual(core.verified_policy.payload.policy_id, "retail-en")

    def test_repeated_calls_have_identical_semantic_evidence(self) -> None:
        core = build_safety_core(
            self.bundle,
            self.trust,
            self.activation,
            verification_time=NOW,
        )
        self.assertEqual(
            core.normalise("SKU-42 ACME LTD"),
            core.normalise("SKU-42 ACME LTD"),
        )

    def test_verification_code_is_preserved_without_sensitive_content(self) -> None:
        value = json.loads(self.bundle)
        value["signature"]["key_id"] = "missing-key"
        changed = _json_bytes(value)
        with self.assertRaises(SDKError) as caught:
            verify_policy(
                changed,
                self.trust,
                self.activation,
                verification_time=NOW,
            )
        error = caught.exception
        self.assertEqual(error.code, "UNKNOWN_KEY")
        self.assertEqual(error.policy_id, "retail-en")
        self.assertNotIn("SKU-42", str(error))
        self.assertNotIn(value["signature"]["signature"], str(error))

    def test_invalid_verification_time_is_typed(self) -> None:
        with self.assertRaises(SDKError) as caught:
            verify_policy(
                self.bundle,
                self.trust,
                self.activation,
                verification_time=datetime(2026, 10, 22, 16, 30),
            )
        self.assertEqual(caught.exception.code, "INVALID_VERIFICATION_TIME")

    def test_runtime_failure_code_and_limit_are_preserved(self) -> None:
        core = build_safety_core(
            self.bundle,
            self.trust,
            self.activation,
            verification_time=NOW,
            runtime_limits=RuntimeLimits(max_input_bytes=2),
        )
        with self.assertRaises(SDKError) as caught:
            core.normalise("three bytes")
        self.assertEqual(caught.exception.code, "LIMIT_EXCEEDED")
        self.assertEqual(caught.exception.limit, "input_bytes")
        self.assertNotIn("three bytes", str(caught.exception))

    def test_safety_core_cannot_accept_raw_payload(self) -> None:
        verified = verify_policy(
            self.bundle,
            self.trust,
            self.activation,
            verification_time=NOW,
        )
        SafetyCore(verified)
        with self.assertRaises(TypeError):
            SafetyCore(verified.payload)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
