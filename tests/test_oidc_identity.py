import base64
import json
import unittest
from datetime import UTC, datetime, timedelta

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from normguard.oidc_identity import (
    OidcIdentityCode,
    OidcIdentityConfiguration,
    OidcIdentityVerifier,
    TrustedRsaKey,
)

NOW = datetime(2026, 11, 20, 15, tzinfo=UTC)
NOW_TS = int(NOW.timestamp())


def b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


class OidcIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_pem = cls.private_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        cls.configuration = OidcIdentityConfiguration(
            issuer="https://identity.example.test/",
            audience="normguard-production",
            keys=(TrustedRsaKey("key-1", public_pem),),
            key_set_expires_at=NOW + timedelta(days=1),
            clock_skew_seconds=30,
        )
        cls.verifier = OidcIdentityVerifier(cls.configuration)

    def token(
        self,
        *,
        claims: dict[str, object] | None = None,
        header: dict[str, object] | None = None,
        key=None,
    ) -> str:
        actual_header = header or {"alg": "RS256", "kid": "key-1", "typ": "JWT"}
        actual_claims = claims or {
            "iss": self.configuration.issuer,
            "sub": "service-account-1",
            "aud": self.configuration.audience,
            "iat": NOW_TS - 10,
            "nbf": NOW_TS - 10,
            "exp": NOW_TS + 300,
        }
        encoded_header = b64url(
            json.dumps(actual_header, separators=(",", ":"), sort_keys=True).encode()
        )
        encoded_claims = b64url(
            json.dumps(actual_claims, separators=(",", ":"), sort_keys=True).encode()
        )
        signing_input = f"{encoded_header}.{encoded_claims}".encode()
        signature = (key or self.private_key).sign(
            signing_input, padding.PKCS1v15(), hashes.SHA256()
        )
        return f"{signing_input.decode()}.{b64url(signature)}"

    def test_valid_signature_derives_only_pseudonymous_verified_principal(self) -> None:
        result = self.verifier.verify(self.token(), at=NOW)
        self.assertEqual(result.code, OidcIdentityCode.VERIFIED)
        self.assertIsNotNone(result.principal)
        serialized = repr(result.principal)
        self.assertNotIn("service-account-1", serialized)
        self.assertNotIn("identity.example.test", serialized)
        self.assertNotIn("normguard-production", serialized)
        self.assertEqual(
            result.evidence(),
            {"schema_version": 1, "verified": True, "code": "verified"},
        )

    def test_tenant_claims_are_ignored_and_cannot_create_authority(self) -> None:
        base = {
            "iss": self.configuration.issuer,
            "sub": "service-account-1",
            "aud": self.configuration.audience,
            "iat": NOW_TS - 10,
            "exp": NOW_TS + 300,
        }
        first = self.verifier.verify(
            self.token(claims={**base, "tenant": "tenant-a"}), at=NOW
        )
        second = self.verifier.verify(
            self.token(claims={**base, "tenant": "tenant-b"}), at=NOW
        )
        self.assertEqual(first.code, OidcIdentityCode.VERIFIED)
        self.assertEqual(first.principal, second.principal)

    def test_forged_signature_unknown_key_and_algorithm_confusion_fail_closed(self) -> None:
        forged = self.verifier.verify(self.token(key=self.other_key), at=NOW)
        unknown = self.verifier.verify(
            self.token(header={"alg": "RS256", "kid": "unknown"}), at=NOW
        )
        confused = self.verifier.verify(
            self.token(header={"alg": "HS256", "kid": "key-1"}), at=NOW
        )
        self.assertEqual(forged.code, OidcIdentityCode.INVALID_SIGNATURE)
        self.assertEqual(unknown.code, OidcIdentityCode.UNTRUSTED_KEY)
        self.assertEqual(confused.code, OidcIdentityCode.MALFORMED)
        for result in (forged, unknown, confused):
            self.assertIsNone(result.principal)
            self.assertNotIn("service-account-1", repr(result.evidence()))
            self.assertEqual(
                set(result.evidence()), {"schema_version", "verified", "code"}
            )

    def test_wrong_issuer_audience_and_malformed_audience_are_rejected(self) -> None:
        base = {
            "iss": self.configuration.issuer,
            "sub": "service-account-1",
            "aud": self.configuration.audience,
            "iat": NOW_TS - 10,
            "exp": NOW_TS + 300,
        }
        cases = (
            {**base, "iss": "https://attacker.test/"},
            {**base, "aud": "other-service"},
            {**base, "aud": [self.configuration.audience] * 2},
            {**base, "aud": []},
        )
        for claims in cases:
            with self.subTest(claims=claims):
                self.assertEqual(
                    self.verifier.verify(self.token(claims=claims), at=NOW).code,
                    OidcIdentityCode.INVALID_CLAIMS,
                )

    def test_expiry_future_and_clock_skew_boundaries_are_enforced(self) -> None:
        base = {
            "iss": self.configuration.issuer,
            "sub": "service-account-1",
            "aud": self.configuration.audience,
            "iat": NOW_TS - 100,
            "exp": NOW_TS + 300,
        }
        expired = self.verifier.verify(
            self.token(claims={**base, "exp": NOW_TS - 31}), at=NOW
        )
        future_iat = self.verifier.verify(
            self.token(claims={**base, "iat": NOW_TS + 31}), at=NOW
        )
        future_nbf = self.verifier.verify(
            self.token(claims={**base, "nbf": NOW_TS + 31}), at=NOW
        )
        within_skew = self.verifier.verify(
            self.token(claims={**base, "nbf": NOW_TS + 30}), at=NOW
        )
        self.assertEqual(expired.code, OidcIdentityCode.EXPIRED)
        self.assertEqual(future_iat.code, OidcIdentityCode.NOT_YET_VALID)
        self.assertEqual(future_nbf.code, OidcIdentityCode.NOT_YET_VALID)
        self.assertEqual(within_skew.code, OidcIdentityCode.VERIFIED)

    def test_malformed_duplicate_and_oversized_tokens_are_rejected(self) -> None:
        duplicate_header = b64url(b'{"alg":"RS256","alg":"RS256","kid":"key-1"}')
        claims = self.token().split(".")[1]
        invalid = f"{duplicate_header}.{claims}.AA"
        cases = ("", "a.b", "a.b.c.d", invalid, "a" * 9_000)
        for token in cases:
            with self.subTest(token_length=len(token)):
                self.assertEqual(
                    self.verifier.verify(token, at=NOW).code,
                    OidcIdentityCode.MALFORMED,
                )

    def test_configuration_digest_is_public_stable_and_target_bound(self) -> None:
        self.assertEqual(self.configuration.sha256, self.configuration.sha256)
        changed = OidcIdentityConfiguration(
            issuer=self.configuration.issuer,
            audience="different-audience",
            keys=self.configuration.keys,
            key_set_expires_at=self.configuration.key_set_expires_at,
            clock_skew_seconds=30,
        )
        self.assertNotEqual(self.configuration.sha256, changed.sha256)
        self.assertNotIn(self.configuration.issuer, self.configuration.sha256)

    def test_stale_trusted_key_snapshot_fails_closed(self) -> None:
        stale = OidcIdentityVerifier(
            OidcIdentityConfiguration(
                issuer=self.configuration.issuer,
                audience=self.configuration.audience,
                keys=self.configuration.keys,
                key_set_expires_at=NOW,
            )
        )
        result = stale.verify(self.token(), at=NOW)
        self.assertEqual(result.code, OidcIdentityCode.UNTRUSTED_KEY)
        self.assertIsNone(result.principal)

    def test_invalid_configuration_and_naive_verification_time_fail_closed(self) -> None:
        with self.assertRaises(ValueError):
            OidcIdentityConfiguration(
                issuer=self.configuration.issuer,
                audience=self.configuration.audience,
                keys=self.configuration.keys * 2,
                key_set_expires_at=self.configuration.key_set_expires_at,
            )
        with self.assertRaises(ValueError):
            self.verifier.verify(self.token(), at=datetime(2026, 11, 20, 15))


if __name__ == "__main__":
    unittest.main()
