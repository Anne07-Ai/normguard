"""Offline authenticity, activation-floor, and rollback verification."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from .safety_contracts import PolicyBundle, PolicyPayload, canonical_json_bytes

ALGORITHM = "ECDSA_P256_SHA256"
TRUST_STORE_SCHEMA_VERSION = 1
ACTIVATION_STATE_SCHEMA_VERSION = 1
ROLLBACK_AUTHORIZATION_SCHEMA_VERSION = 1
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_SEMVER = re.compile(
    r"^(0|[1-9]\\d*)\\.(0|[1-9]\\d*)\\.(0|[1-9]\\d*)"
    r"(?:-([0-9A-Za-z.-]+))?(?:\\+[0-9A-Za-z.-]+)?$"
)
_ID = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,126}[a-z0-9])?$")


class VerificationCode(StrEnum):
    INVALID_BUNDLE = "INVALID_BUNDLE"
    UNSUPPORTED_SCHEMA = "UNSUPPORTED_SCHEMA"
    DIGEST_MISMATCH = "DIGEST_MISMATCH"
    SIGNATURE_REQUIRED = "SIGNATURE_REQUIRED"
    UNSUPPORTED_ALGORITHM = "UNSUPPORTED_ALGORITHM"
    UNKNOWN_KEY = "UNKNOWN_KEY"
    KEY_NOT_ACTIVE = "KEY_NOT_ACTIVE"
    KEY_REVOKED = "KEY_REVOKED"
    KEY_OUTSIDE_VALIDITY = "KEY_OUTSIDE_VALIDITY"
    POLICY_NOT_ALLOWED = "POLICY_NOT_ALLOWED"
    INVALID_SIGNATURE = "INVALID_SIGNATURE"
    APPROVAL_INVALID = "APPROVAL_INVALID"
    ROLLBACK_REJECTED = "ROLLBACK_REJECTED"
    ROLLBACK_AUTHORIZATION_INVALID = "ROLLBACK_AUTHORIZATION_INVALID"


class PolicyVerificationError(Exception):
    """Terminal, privacy-safe verification failure."""

    def __init__(
        self,
        code: VerificationCode,
        *,
        policy_id: str | None = None,
        key_id: str | None = None,
    ) -> None:
        self.code = code
        self.policy_id = policy_id
        self.key_id = key_id
        identifiers = [
            f"policy_id={policy_id}" if policy_id is not None else None,
            f"key_id={key_id}" if key_id is not None else None,
        ]
        suffix = " " + " ".join(item for item in identifiers if item) if any(identifiers) else ""
        super().__init__(f"{code.value}{suffix}")


class KeyStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"
    REVOKED = "revoked"


def _exact_keys(value: dict[str, Any], expected: set[str], name: str) -> None:
    if set(value) != expected:
        raise ValueError(f"{name} does not match the closed schema")


def _identifier(value: Any, name: str) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise ValueError(f"{name} is invalid")
    return value


def _digest(value: Any, name: str) -> str:
    if not isinstance(value, str) or _HEX64.fullmatch(value) is None:
        raise ValueError(f"{name} is invalid")
    return value


def _timestamp(value: Any, name: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(f"{name} must be UTC with Z suffix")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError(f"{name} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ValueError(f"{name} must be UTC")
    return parsed


def _canonical_base64(value: Any, name: str) -> bytes:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} is invalid")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"{name} is invalid") from exc
    if base64.b64encode(decoded).decode("ascii") != value:
        raise ValueError(f"{name} is not canonical base64")
    return decoded


def _semver(value: Any) -> tuple[int, int, int, tuple[str, ...] | None]:
    if not isinstance(value, str):
        raise ValueError("version is invalid")
    match = _SEMVER.fullmatch(value)
    if match is None:
        raise ValueError("version is invalid")
    prerelease = None if match[4] is None else tuple(match[4].split("."))
    return int(match[1]), int(match[2]), int(match[3]), prerelease


def _compare_semver(left: str, right: str) -> int:
    left_version = _semver(left)
    right_version = _semver(right)
    if left_version[:3] != right_version[:3]:
        return (left_version[:3] > right_version[:3]) - (left_version[:3] < right_version[:3])
    left_pre, right_pre = left_version[3], right_version[3]
    if left_pre is None or right_pre is None:
        return (left_pre is None) - (right_pre is None)
    for left_item, right_item in zip(left_pre, right_pre, strict=False):
        if left_item == right_item:
            continue
        left_numeric, right_numeric = left_item.isdigit(), right_item.isdigit()
        if left_numeric and right_numeric:
            return (int(left_item) > int(right_item)) - (int(left_item) < int(right_item))
        if left_numeric != right_numeric:
            return -1 if left_numeric else 1
        return (left_item > right_item) - (left_item < right_item)
    return (len(left_pre) > len(right_pre)) - (len(left_pre) < len(right_pre))


@dataclass(frozen=True, slots=True)
class TrustedKey:
    key_id: str
    algorithm: str
    public_key_spki_der_base64: str
    public_key_sha256: str
    allowed_policy_ids: tuple[str, ...]
    status: KeyStatus
    not_before: datetime
    not_after: datetime
    public_key: ec.EllipticCurvePublicKey

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> TrustedKey:
        _exact_keys(
            value,
            {
                "key_id",
                "algorithm",
                "public_key_spki_der_base64",
                "public_key_sha256",
                "allowed_policy_ids",
                "status",
                "not_before",
                "not_after",
            },
            "trusted key",
        )
        key_id = _identifier(value["key_id"], "key_id")
        if value["algorithm"] != ALGORITHM:
            raise ValueError("trusted key algorithm is unsupported")
        der = _canonical_base64(value["public_key_spki_der_base64"], "public key")
        fingerprint = _digest(value["public_key_sha256"], "public_key_sha256")
        if not hmac.compare_digest(hashlib.sha256(der).hexdigest(), fingerprint):
            raise ValueError("public key fingerprint mismatch")
        allowed = value["allowed_policy_ids"]
        if (
            not isinstance(allowed, list)
            or not allowed
            or any(not isinstance(item, str) or _ID.fullmatch(item) is None for item in allowed)
            or "*" in allowed
            or len(allowed) != len(set(allowed))
        ):
            raise ValueError("allowed_policy_ids is invalid")
        try:
            status = KeyStatus(value["status"])
            key = serialization.load_der_public_key(der)
        except (TypeError, ValueError) as exc:
            raise ValueError("trusted key material is invalid") from exc
        if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(
            key.curve, ec.SECP256R1
        ):
            raise ValueError("trusted key must use P-256")
        not_before = _timestamp(value["not_before"], "not_before")
        not_after = _timestamp(value["not_after"], "not_after")
        if not_after <= not_before:
            raise ValueError("trusted key validity interval is invalid")
        return cls(
            key_id,
            ALGORITHM,
            value["public_key_spki_der_base64"],
            fingerprint,
            tuple(allowed),
            status,
            not_before,
            not_after,
            key,
        )


@dataclass(frozen=True, slots=True)
class TrustStore:
    keys: tuple[TrustedKey, ...]
    schema_version: int = TRUST_STORE_SCHEMA_VERSION

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> TrustStore:
        _exact_keys(value, {"schema_version", "keys"}, "trust store")
        if value["schema_version"] != TRUST_STORE_SCHEMA_VERSION:
            raise ValueError("unsupported trust-store schema")
        if not isinstance(value["keys"], list) or not value["keys"]:
            raise ValueError("trust store keys must be a non-empty list")
        keys = tuple(TrustedKey.from_dict(item) for item in value["keys"])
        key_ids = [item.key_id for item in keys]
        fingerprints = [item.public_key_sha256 for item in keys]
        if len(key_ids) != len(set(key_ids)) or len(fingerprints) != len(set(fingerprints)):
            raise ValueError("trust-store keys and fingerprints must be unique")
        return cls(keys)

    def resolve(self, key_id: str) -> TrustedKey | None:
        return next((key for key in self.keys if key.key_id == key_id), None)


@dataclass(frozen=True, slots=True)
class ActivationState:
    policy_id: str
    minimum_version: str
    updated_at: datetime
    change_id: str
    allow_prerelease: bool
    schema_version: int = ACTIVATION_STATE_SCHEMA_VERSION

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ActivationState:
        _exact_keys(
            value,
            {
                "schema_version",
                "policy_id",
                "minimum_version",
                "updated_at",
                "change_id",
                "allow_prerelease",
            },
            "activation state",
        )
        if value["schema_version"] != ACTIVATION_STATE_SCHEMA_VERSION:
            raise ValueError("unsupported activation-state schema")
        _semver(value["minimum_version"])
        if not isinstance(value["change_id"], str) or not value["change_id"].strip():
            raise ValueError("change_id is invalid")
        if not isinstance(value["allow_prerelease"], bool):
            raise ValueError("allow_prerelease must be boolean")
        return cls(
            _identifier(value["policy_id"], "policy_id"),
            value["minimum_version"],
            _timestamp(value["updated_at"], "updated_at"),
            value["change_id"],
            value["allow_prerelease"],
        )


@dataclass(frozen=True, slots=True)
class RollbackAuthorization:
    policy_id: str
    target_version: str
    target_payload_sha256: str
    change_id: str
    reason_code: str
    issued_at: datetime
    expires_at: datetime
    approver_ids: tuple[str, ...]
    algorithm: str
    key_id: str
    signature: str
    schema_version: int = ROLLBACK_AUTHORIZATION_SCHEMA_VERSION

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> RollbackAuthorization:
        _exact_keys(
            value,
            {
                "schema_version",
                "policy_id",
                "target_version",
                "target_payload_sha256",
                "change_id",
                "reason_code",
                "issued_at",
                "expires_at",
                "approver_ids",
                "algorithm",
                "key_id",
                "signature",
            },
            "rollback authorization",
        )
        if value["schema_version"] != ROLLBACK_AUTHORIZATION_SCHEMA_VERSION:
            raise ValueError("unsupported rollback schema")
        _semver(value["target_version"])
        issued_at = _timestamp(value["issued_at"], "issued_at")
        expires_at = _timestamp(value["expires_at"], "expires_at")
        if expires_at <= issued_at:
            raise ValueError("rollback validity interval is invalid")
        approvers = value["approver_ids"]
        if (
            not isinstance(approvers, list)
            or len(approvers) != 2
            or len(set(approvers)) != 2
        ):
            raise ValueError("rollback requires two distinct approvers")
        approver_ids = tuple(_identifier(item, "approver_id") for item in approvers)
        for name in ("change_id", "reason_code"):
            if not isinstance(value[name], str) or not value[name].strip():
                raise ValueError(f"{name} is invalid")
        _identifier(value["key_id"], "key_id")
        _canonical_base64(value["signature"], "signature")
        return cls(
            _identifier(value["policy_id"], "policy_id"),
            value["target_version"],
            _digest(value["target_payload_sha256"], "target_payload_sha256"),
            value["change_id"],
            value["reason_code"],
            issued_at,
            expires_at,
            approver_ids,
            value["algorithm"],
            value["key_id"],
            value["signature"],
        )

    def signed_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "policy_id": self.policy_id,
            "target_version": self.target_version,
            "target_payload_sha256": self.target_payload_sha256,
            "change_id": self.change_id,
            "reason_code": self.reason_code,
            "issued_at": self.issued_at.isoformat().replace("+00:00", "Z"),
            "expires_at": self.expires_at.isoformat().replace("+00:00", "Z"),
            "approver_ids": list(self.approver_ids),
            "algorithm": self.algorithm,
            "key_id": self.key_id,
        }


@dataclass(frozen=True, slots=True)
class VerifiedPolicy:
    payload: PolicyPayload
    payload_sha256: str
    signing_key_id: str
    signing_key_sha256: str
    rollback_authorized: bool
    verification_time: datetime


def _verify_signature(
    *,
    algorithm: str,
    key_id: str,
    signature_base64: str,
    message: bytes,
    policy_id: str,
    trust_store: TrustStore,
    now: datetime,
) -> TrustedKey:
    if algorithm != ALGORITHM:
        raise PolicyVerificationError(
            VerificationCode.UNSUPPORTED_ALGORITHM, policy_id=policy_id, key_id=key_id
        )
    key = trust_store.resolve(key_id)
    if key is None:
        raise PolicyVerificationError(
            VerificationCode.UNKNOWN_KEY, policy_id=policy_id, key_id=key_id
        )
    if key.status is KeyStatus.REVOKED:
        raise PolicyVerificationError(
            VerificationCode.KEY_REVOKED, policy_id=policy_id, key_id=key_id
        )
    if key.status is not KeyStatus.ACTIVE:
        raise PolicyVerificationError(
            VerificationCode.KEY_NOT_ACTIVE, policy_id=policy_id, key_id=key_id
        )
    if now < key.not_before or now > key.not_after:
        raise PolicyVerificationError(
            VerificationCode.KEY_OUTSIDE_VALIDITY, policy_id=policy_id, key_id=key_id
        )
    if policy_id not in key.allowed_policy_ids:
        raise PolicyVerificationError(
            VerificationCode.POLICY_NOT_ALLOWED, policy_id=policy_id, key_id=key_id
        )
    try:
        signature = _canonical_base64(signature_base64, "signature")
        key.public_key.verify(signature, message, ec.ECDSA(hashes.SHA256()))
    except (InvalidSignature, ValueError):
        raise PolicyVerificationError(
            VerificationCode.INVALID_SIGNATURE, policy_id=policy_id, key_id=key_id
        ) from None
    return key


def _validate_rollback(
    authorization: RollbackAuthorization | None,
    rollback_trust_store: TrustStore | None,
    bundle: PolicyBundle,
    now: datetime,
) -> None:
    policy_id = bundle.payload.policy_id
    if authorization is None or rollback_trust_store is None:
        raise PolicyVerificationError(VerificationCode.ROLLBACK_REJECTED, policy_id=policy_id)
    if (
        authorization.policy_id != policy_id
        or authorization.target_version != bundle.payload.policy_version
        or not hmac.compare_digest(
            authorization.target_payload_sha256, bundle.payload_sha256
        )
        or now < authorization.issued_at
        or now > authorization.expires_at
    ):
        raise PolicyVerificationError(
            VerificationCode.ROLLBACK_AUTHORIZATION_INVALID, policy_id=policy_id
        )
    try:
        _verify_signature(
            algorithm=authorization.algorithm,
            key_id=authorization.key_id,
            signature_base64=authorization.signature,
            message=canonical_json_bytes(authorization.signed_dict()),
            policy_id=policy_id,
            trust_store=rollback_trust_store,
            now=now,
        )
    except PolicyVerificationError as exc:
        raise PolicyVerificationError(
            VerificationCode.ROLLBACK_AUTHORIZATION_INVALID,
            policy_id=policy_id,
            key_id=authorization.key_id,
        ) from exc


def verify_policy_bundle(
    bundle: PolicyBundle,
    trust_store: TrustStore,
    activation_state: ActivationState,
    *,
    now: datetime,
    rollback_authorization: RollbackAuthorization | None = None,
    rollback_trust_store: TrustStore | None = None,
) -> VerifiedPolicy:
    """Verify a policy atomically with no network or unsigned fallback."""

    if now.tzinfo is None or now.utcoffset() != UTC.utcoffset(now):
        raise ValueError("now must be timezone-aware UTC")
    policy_id = bundle.payload.policy_id
    if activation_state.policy_id != policy_id:
        raise PolicyVerificationError(VerificationCode.INVALID_BUNDLE, policy_id=policy_id)
    if not hmac.compare_digest(bundle.payload.sha256, bundle.payload_sha256):
        raise PolicyVerificationError(VerificationCode.DIGEST_MISMATCH, policy_id=policy_id)
    if any(
        not hmac.compare_digest(approval.payload_sha256, bundle.payload_sha256)
        for approval in bundle.approvals
    ):
        raise PolicyVerificationError(VerificationCode.APPROVAL_INVALID, policy_id=policy_id)
    if bundle.signature is None:
        raise PolicyVerificationError(VerificationCode.SIGNATURE_REQUIRED, policy_id=policy_id)
    signature = bundle.signature
    key = _verify_signature(
        algorithm=signature.algorithm,
        key_id=signature.key_id,
        signature_base64=signature.signature,
        message=canonical_json_bytes(bundle.payload.to_dict()),
        policy_id=policy_id,
        trust_store=trust_store,
        now=now,
    )
    version = bundle.payload.policy_version
    is_prerelease = _semver(version)[3] is not None
    below_floor = _compare_semver(version, activation_state.minimum_version) < 0
    if is_prerelease and not activation_state.allow_prerelease:
        raise PolicyVerificationError(VerificationCode.ROLLBACK_REJECTED, policy_id=policy_id)
    rollback_authorized = False
    if below_floor:
        _validate_rollback(rollback_authorization, rollback_trust_store, bundle, now)
        rollback_authorized = True
    return VerifiedPolicy(
        bundle.payload,
        bundle.payload_sha256,
        key.key_id,
        key.public_key_sha256,
        rollback_authorized,
        now,
    )
