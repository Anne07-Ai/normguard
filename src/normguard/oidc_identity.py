"""Fail-closed OIDC JWT verification for the hosted service identity boundary."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from .hosted_authorization import PrincipalKind, VerifiedPrincipal
from .safety_contracts import canonical_json_bytes

OIDC_IDENTITY_SCHEMA_VERSION = 1
_KID = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,126}[A-Za-z0-9])?$")
_JWT_SEGMENT = re.compile(r"^[A-Za-z0-9_-]+$")
_ALLOWED_HEADER_KEYS = frozenset({"alg", "kid", "typ"})


class OidcIdentityCode(StrEnum):
    VERIFIED = "verified"
    MALFORMED = "malformed"
    UNTRUSTED_KEY = "untrusted_key"
    INVALID_SIGNATURE = "invalid_signature"
    INVALID_CLAIMS = "invalid_claims"
    EXPIRED = "expired"
    NOT_YET_VALID = "not_yet_valid"


def _b64url_decode(value: str) -> bytes:
    if not value or _JWT_SEGMENT.fullmatch(value) is None:
        raise ValueError("invalid base64url segment")
    padding_size = (-len(value)) % 4
    decoded = base64.urlsafe_b64decode(value + ("=" * padding_size))
    if base64.urlsafe_b64encode(decoded).rstrip(b"=").decode() != value:
        raise ValueError("non-canonical base64url segment")
    return decoded


def _json_object(value: bytes) -> dict[str, object]:
    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("duplicate JSON member")
            result[key] = item
        return result

    parsed = json.loads(value, object_pairs_hook=unique_object)
    if not isinstance(parsed, dict):
        raise ValueError("JWT section must be an object")
    return parsed


def _bounded_text(value: object, name: str, *, maximum: int = 2_048) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError(f"{name} must be bounded text")
    return value


def _timestamp(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer timestamp")
    return value


def _reference(label: str, *values: str) -> str:
    payload = "\x00".join(values).encode()
    return f"{label}-{hashlib.sha256(payload).hexdigest()}"


@dataclass(frozen=True, slots=True)
class TrustedRsaKey:
    kid: str
    public_key_pem: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.kid, str) or _KID.fullmatch(self.kid) is None:
            raise ValueError("kid must be a bounded identifier")
        if not isinstance(self.public_key_pem, bytes) or len(self.public_key_pem) > 16_384:
            raise ValueError("public_key_pem must be bounded bytes")
        key = serialization.load_pem_public_key(self.public_key_pem)
        if not isinstance(key, rsa.RSAPublicKey) or key.key_size < 2048:
            raise ValueError("trusted key must be RSA with at least 2048 bits")

    @property
    def fingerprint(self) -> str:
        key = serialization.load_pem_public_key(self.public_key_pem)
        der = key.public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        return hashlib.sha256(der).hexdigest()

    def load(self) -> rsa.RSAPublicKey:
        key = serialization.load_pem_public_key(self.public_key_pem)
        assert isinstance(key, rsa.RSAPublicKey)
        return key


@dataclass(frozen=True, slots=True)
class OidcIdentityConfiguration:
    issuer: str
    audience: str
    keys: tuple[TrustedRsaKey, ...]
    key_set_expires_at: datetime
    clock_skew_seconds: int = 60
    maximum_token_bytes: int = 8_192

    def __post_init__(self) -> None:
        _bounded_text(self.issuer, "issuer")
        _bounded_text(self.audience, "audience")
        if not self.keys or len({key.kid for key in self.keys}) != len(self.keys):
            raise ValueError("trusted key IDs must be non-empty and unique")
        if (
            not isinstance(self.key_set_expires_at, datetime)
            or self.key_set_expires_at.tzinfo is None
        ):
            raise ValueError("key_set_expires_at must be timezone aware")
        if (
            isinstance(self.clock_skew_seconds, bool)
            or not isinstance(self.clock_skew_seconds, int)
            or not 0 <= self.clock_skew_seconds <= 300
        ):
            raise ValueError("clock_skew_seconds must be in [0, 300]")
        if (
            isinstance(self.maximum_token_bytes, bool)
            or not isinstance(self.maximum_token_bytes, int)
            or not 1_024 <= self.maximum_token_bytes <= 65_536
        ):
            raise ValueError("maximum_token_bytes must be in [1024, 65536]")

    @property
    def sha256(self) -> str:
        public = {
            "issuer_sha256": hashlib.sha256(self.issuer.encode()).hexdigest(),
            "audience_sha256": hashlib.sha256(self.audience.encode()).hexdigest(),
            "algorithm": "RS256",
            "key_fingerprints": sorted(key.fingerprint for key in self.keys),
            "key_set_expires_at": self.key_set_expires_at.isoformat(),
            "clock_skew_seconds": self.clock_skew_seconds,
            "maximum_token_bytes": self.maximum_token_bytes,
        }
        return hashlib.sha256(canonical_json_bytes(public)).hexdigest()


@dataclass(frozen=True, slots=True)
class OidcIdentityResult:
    code: OidcIdentityCode
    principal: VerifiedPrincipal | None = None
    schema_version: int = OIDC_IDENTITY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != OIDC_IDENTITY_SCHEMA_VERSION:
            raise ValueError("unsupported OIDC identity schema_version")
        if (self.code is OidcIdentityCode.VERIFIED) != (self.principal is not None):
            raise ValueError("OIDC identity result is inconsistent")

    def evidence(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "verified": self.principal is not None,
            "code": self.code.value,
        }


class OidcIdentityVerifier:
    def __init__(self, configuration: OidcIdentityConfiguration) -> None:
        self._configuration = configuration
        self._keys = {key.kid: key for key in configuration.keys}

    @staticmethod
    def _deny(code: OidcIdentityCode) -> OidcIdentityResult:
        return OidcIdentityResult(code)

    def verify(
        self, token: str, *, at: datetime | None = None
    ) -> OidcIdentityResult:
        moment = at or datetime.now(UTC)
        if not isinstance(moment, datetime) or moment.tzinfo is None:
            raise ValueError("at must be timezone aware")
        if moment >= self._configuration.key_set_expires_at:
            return self._deny(OidcIdentityCode.UNTRUSTED_KEY)
        if (
            not isinstance(token, str)
            or not token
            or len(token.encode()) > self._configuration.maximum_token_bytes
        ):
            return self._deny(OidcIdentityCode.MALFORMED)
        parts = token.split(".")
        if len(parts) != 3:
            return self._deny(OidcIdentityCode.MALFORMED)
        try:
            header = _json_object(_b64url_decode(parts[0]))
            claims = _json_object(_b64url_decode(parts[1]))
            signature = _b64url_decode(parts[2])
        except Exception:
            return self._deny(OidcIdentityCode.MALFORMED)
        if set(header) - _ALLOWED_HEADER_KEYS or header.get("alg") != "RS256":
            return self._deny(OidcIdentityCode.MALFORMED)
        if header.get("typ", "JWT") != "JWT":
            return self._deny(OidcIdentityCode.MALFORMED)
        kid = header.get("kid")
        if not isinstance(kid, str) or _KID.fullmatch(kid) is None:
            return self._deny(OidcIdentityCode.MALFORMED)
        trusted_key = self._keys.get(kid)
        if trusted_key is None:
            return self._deny(OidcIdentityCode.UNTRUSTED_KEY)
        try:
            trusted_key.load().verify(
                signature,
                f"{parts[0]}.{parts[1]}".encode(),
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
        except InvalidSignature:
            return self._deny(OidcIdentityCode.INVALID_SIGNATURE)
        except Exception:
            return self._deny(OidcIdentityCode.UNTRUSTED_KEY)

        try:
            issuer = _bounded_text(claims.get("iss"), "iss")
            subject = _bounded_text(claims.get("sub"), "sub")
            issued_at = _timestamp(claims.get("iat"), "iat")
            expires_at = _timestamp(claims.get("exp"), "exp")
            not_before = _timestamp(claims.get("nbf", issued_at), "nbf")
            audience_claim = claims.get("aud")
            if isinstance(audience_claim, str):
                audiences = (audience_claim,)
            elif (
                isinstance(audience_claim, list)
                and audience_claim
                and len(audience_claim) <= 16
                and all(isinstance(item, str) and item for item in audience_claim)
            ):
                audiences = tuple(audience_claim)
            else:
                raise ValueError("aud must be bounded")
            if len(set(audiences)) != len(audiences):
                raise ValueError("aud must not contain duplicates")
        except (TypeError, ValueError):
            return self._deny(OidcIdentityCode.INVALID_CLAIMS)
        if issuer != self._configuration.issuer or self._configuration.audience not in audiences:
            return self._deny(OidcIdentityCode.INVALID_CLAIMS)

        now = int(moment.timestamp())
        skew = self._configuration.clock_skew_seconds
        if expires_at <= issued_at or now >= expires_at:
            return self._deny(OidcIdentityCode.EXPIRED)
        if issued_at > now + skew or not_before > now + skew:
            return self._deny(OidcIdentityCode.NOT_YET_VALID)

        principal = VerifiedPrincipal(
            principal_id=_reference("oidc", issuer, subject),
            issuer=_reference("issuer", issuer),
            subject=_reference("subject", issuer, subject),
            audience=_reference("audience", self._configuration.audience),
            kind=PrincipalKind.SERVICE,
            authenticated_at=datetime.fromtimestamp(min(issued_at, now), UTC),
            expires_at=datetime.fromtimestamp(expires_at, UTC),
        )
        return OidcIdentityResult(OidcIdentityCode.VERIFIED, principal)
