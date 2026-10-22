"""Stable public SDK facade for the offline NormGuard Safety Core."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .policy_verification import (
    ActivationState,
    PolicyVerificationError,
    RollbackAuthorization,
    TrustStore,
    VerificationCode,
    VerifiedPolicy,
    verify_policy_bundle,
)
from .runtime import (
    BoundedSafetyRuntime,
    RuntimeFailure,
    RuntimeLimits,
    RuntimeResult,
)
from .safety_contracts import policy_bundle_from_dict

SDK_VERSION = "0.1.0"
SDK_SCHEMA_VERSION = 1


class SDKError(Exception):
    """One privacy-safe error boundary for SDK consumers."""

    def __init__(
        self,
        code: str,
        *,
        document: str | None = None,
        policy_id: str | None = None,
        key_id: str | None = None,
        limit: str | None = None,
    ) -> None:
        self.code = code
        self.document = document
        self.policy_id = policy_id
        self.key_id = key_id
        self.limit = limit
        fields = (
            ("document", document),
            ("policy_id", policy_id),
            ("key_id", key_id),
            ("limit", limit),
        )
        safe = " ".join(f"{name}={value}" for name, value in fields if value is not None)
        super().__init__(f"{code} {safe}".rstrip())


@dataclass(frozen=True, slots=True)
class SDKDocumentLimits:
    max_policy_bundle_bytes: int = 2_000_000
    max_trust_store_bytes: int = 1_000_000
    max_activation_state_bytes: int = 64_000
    max_rollback_authorization_bytes: int = 128_000
    max_rollback_trust_store_bytes: int = 1_000_000

    def __post_init__(self) -> None:
        for name, value in self.to_dict().items():
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")

    def to_dict(self) -> dict[str, int]:
        return {
            "max_policy_bundle_bytes": self.max_policy_bundle_bytes,
            "max_trust_store_bytes": self.max_trust_store_bytes,
            "max_activation_state_bytes": self.max_activation_state_bytes,
            "max_rollback_authorization_bytes": self.max_rollback_authorization_bytes,
            "max_rollback_trust_store_bytes": self.max_rollback_trust_store_bytes,
        }


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is forbidden: {value}")


def _closed_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _parse_document(data: bytes, *, maximum: int, name: str) -> dict[str, Any]:
    if not isinstance(data, bytes):
        raise SDKError("INVALID_DOCUMENT", document=name)
    if len(data) > maximum:
        raise SDKError("DOCUMENT_TOO_LARGE", document=name)
    try:
        text = data.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=_closed_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
        raise SDKError("INVALID_DOCUMENT", document=name) from None
    if not isinstance(value, dict):
        raise SDKError("INVALID_DOCUMENT", document=name)
    return value


def verify_policy(
    policy_bundle_bytes: bytes,
    trust_store_bytes: bytes,
    activation_state_bytes: bytes,
    *,
    verification_time: datetime,
    document_limits: SDKDocumentLimits | None = None,
    rollback_authorization_bytes: bytes | None = None,
    rollback_trust_store_bytes: bytes | None = None,
) -> VerifiedPolicy:
    """Parse and verify untrusted policy documents fully offline."""

    limits = document_limits or SDKDocumentLimits()
    try:
        bundle = policy_bundle_from_dict(
            _parse_document(
                policy_bundle_bytes,
                maximum=limits.max_policy_bundle_bytes,
                name="policy_bundle",
            )
        )
        trust_store = TrustStore.from_dict(
            _parse_document(
                trust_store_bytes,
                maximum=limits.max_trust_store_bytes,
                name="trust_store",
            )
        )
        activation_state = ActivationState.from_dict(
            _parse_document(
                activation_state_bytes,
                maximum=limits.max_activation_state_bytes,
                name="activation_state",
            )
        )
        rollback_authorization = (
            None
            if rollback_authorization_bytes is None
            else RollbackAuthorization.from_dict(
                _parse_document(
                    rollback_authorization_bytes,
                    maximum=limits.max_rollback_authorization_bytes,
                    name="rollback_authorization",
                )
            )
        )
        rollback_trust_store = (
            None
            if rollback_trust_store_bytes is None
            else TrustStore.from_dict(
                _parse_document(
                    rollback_trust_store_bytes,
                    maximum=limits.max_rollback_trust_store_bytes,
                    name="rollback_trust_store",
                )
            )
        )
    except SDKError:
        raise
    except (KeyError, TypeError, ValueError):
        raise SDKError("INVALID_DOCUMENT") from None

    try:
        return verify_policy_bundle(
            bundle,
            trust_store,
            activation_state,
            now=verification_time,
            rollback_authorization=rollback_authorization,
            rollback_trust_store=rollback_trust_store,
        )
    except PolicyVerificationError as exc:
        raise SDKError(
            exc.code.value,
            policy_id=exc.policy_id,
            key_id=exc.key_id,
        ) from None
    except ValueError:
        raise SDKError("INVALID_VERIFICATION_TIME") from None


class SafetyCore:
    """Stable SDK object for bounded use of one verified policy."""

    __slots__ = ("_runtime",)

    def __init__(
        self,
        verified_policy: VerifiedPolicy,
        *,
        runtime_limits: RuntimeLimits | None = None,
    ) -> None:
        self._runtime = BoundedSafetyRuntime(
            verified_policy,
            limits=runtime_limits,
        )

    @property
    def verified_policy(self) -> VerifiedPolicy:
        return self._runtime.verified_policy

    @property
    def runtime_limits(self) -> RuntimeLimits:
        return self._runtime.limits

    def normalise(self, text: str) -> RuntimeResult:
        try:
            return self._runtime.normalise(text)
        except RuntimeFailure as exc:
            raise SDKError(
                exc.code.value,
                policy_id=exc.policy_id,
                limit=None if exc.limit is None else exc.limit.value,
            ) from None


def build_safety_core(
    policy_bundle_bytes: bytes,
    trust_store_bytes: bytes,
    activation_state_bytes: bytes,
    *,
    verification_time: datetime,
    document_limits: SDKDocumentLimits | None = None,
    runtime_limits: RuntimeLimits | None = None,
    rollback_authorization_bytes: bytes | None = None,
    rollback_trust_store_bytes: bytes | None = None,
) -> SafetyCore:
    """Verify untrusted documents and construct the bounded SDK runtime."""

    verified = verify_policy(
        policy_bundle_bytes,
        trust_store_bytes,
        activation_state_bytes,
        verification_time=verification_time,
        document_limits=document_limits,
        rollback_authorization_bytes=rollback_authorization_bytes,
        rollback_trust_store_bytes=rollback_trust_store_bytes,
    )
    return SafetyCore(verified, runtime_limits=runtime_limits)
