"""Tenant-safe audit and public error contracts for the hosted service boundary."""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from .hosted_authorization import AuthorizedContext

HOSTED_AUDIT_SCHEMA_VERSION = 1
PUBLIC_ERROR_SCHEMA_VERSION = 1
_IDENTIFIER = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._:@/-]{0,126}[A-Za-z0-9])?$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _require_identifier(value: str, name: str) -> None:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{name} must be a bounded identifier")


def _require_digest(value: str, name: str) -> None:
    if not isinstance(value, str) or _HEX64.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")


def _require_aware(value: datetime, name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{name} must be timezone aware")


class AuditOutcome(StrEnum):
    ALLOWED = "allowed"
    DENIED = "denied"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class AuditRequirement(StrEnum):
    BEST_EFFORT = "best_effort"
    MANDATORY = "mandatory"


class AuditWriteCode(StrEnum):
    WRITTEN = "written"
    DROPPED = "dropped"
    BLOCKED = "blocked"


class InternalFailure(StrEnum):
    UNAUTHENTICATED = "unauthenticated"
    NOT_FOUND = "not_found"
    NOT_AUTHORIZED = "not_authorized"
    INVALID_REQUEST = "invalid_request"
    RATE_LIMITED = "rate_limited"
    DEPENDENCY_FAILURE = "dependency_failure"
    INTERNAL_FAILURE = "internal_failure"


class PublicErrorCode(StrEnum):
    UNAUTHENTICATED = "UNAUTHENTICATED"
    NOT_AUTHORIZED = "NOT_AUTHORIZED"
    INVALID_REQUEST = "INVALID_REQUEST"
    RATE_LIMITED = "RATE_LIMITED"
    UNAVAILABLE = "UNAVAILABLE"
    INTERNAL_FAILURE = "INTERNAL_FAILURE"


class AuditPseudonymizer:
    """Create domain-separated stable references without exposing source identifiers."""

    def __init__(self, key: bytes, *, domain: str) -> None:
        if not isinstance(key, bytes) or len(key) < 32:
            raise ValueError("audit pseudonym key must contain at least 32 bytes")
        _require_identifier(domain, "domain")
        self._key = key
        self._domain = domain

    def pseudonymize(self, value: str, *, label: str) -> str:
        _require_identifier(value, "value")
        _require_identifier(label, "label")
        payload = f"{self._domain}\x00{label}\x00{value}".encode()
        return hmac.new(self._key, payload, hashlib.sha256).hexdigest()


@dataclass(frozen=True, slots=True)
class HostedAuditEvent:
    event_id: str
    correlation_id: str
    occurred_at: datetime
    tenant_ref: str
    principal_ref: str
    action: str
    resource_type: str
    outcome: AuditOutcome
    reason_code: str
    privileged: bool
    artifact_sha256: str | None = None
    schema_version: int = HOSTED_AUDIT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != HOSTED_AUDIT_SCHEMA_VERSION:
            raise ValueError("unsupported hosted audit schema_version")
        for name in (
            "event_id",
            "correlation_id",
            "action",
            "resource_type",
            "reason_code",
        ):
            _require_identifier(getattr(self, name), name)
        _require_aware(self.occurred_at, "occurred_at")
        _require_digest(self.tenant_ref, "tenant_ref")
        _require_digest(self.principal_ref, "principal_ref")
        if not isinstance(self.outcome, AuditOutcome):
            raise ValueError("outcome must be supported")
        if not isinstance(self.privileged, bool):
            raise ValueError("privileged must be boolean")
        if self.artifact_sha256 is not None:
            _require_digest(self.artifact_sha256, "artifact_sha256")

    @classmethod
    def from_authorized_context(
        cls,
        context: AuthorizedContext,
        *,
        pseudonymizer: AuditPseudonymizer,
        event_id: str,
        correlation_id: str,
        outcome: AuditOutcome,
        reason_code: str,
        privileged: bool,
        artifact_sha256: str | None = None,
        occurred_at: datetime | None = None,
    ) -> HostedAuditEvent:
        return cls(
            event_id=event_id,
            correlation_id=correlation_id,
            occurred_at=occurred_at or datetime.now(UTC),
            tenant_ref=pseudonymizer.pseudonymize(context.tenant_id, label="tenant"),
            principal_ref=pseudonymizer.pseudonymize(
                context.principal_id, label="principal"
            ),
            action=context.action.value,
            resource_type=context.resource_type.value,
            outcome=outcome,
            reason_code=reason_code,
            privileged=privileged,
            artifact_sha256=artifact_sha256,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "correlation_id": self.correlation_id,
            "occurred_at": self.occurred_at.isoformat(),
            "tenant_ref": self.tenant_ref,
            "principal_ref": self.principal_ref,
            "action": self.action,
            "resource_type": self.resource_type,
            "outcome": self.outcome.value,
            "reason_code": self.reason_code,
            "privileged": self.privileged,
            "artifact_sha256": self.artifact_sha256,
        }


class HostedAuditSink(Protocol):
    def emit(self, event: HostedAuditEvent) -> None: ...


class InMemoryHostedAuditSink:
    def __init__(self, events: Iterable[HostedAuditEvent] = ()) -> None:
        self.events = list(events)

    def emit(self, event: HostedAuditEvent) -> None:
        self.events.append(event)


@dataclass(frozen=True, slots=True)
class AuditWriteResult:
    code: AuditWriteCode
    may_continue: bool

    def __post_init__(self) -> None:
        expected = self.code is not AuditWriteCode.BLOCKED
        if self.may_continue != expected:
            raise ValueError("audit write result is inconsistent")


class HostedAuditRecorder:
    def __init__(self, sink: HostedAuditSink) -> None:
        self._sink = sink

    def record(
        self,
        event: HostedAuditEvent,
        *,
        requirement: AuditRequirement,
    ) -> AuditWriteResult:
        if event.privileged and requirement is not AuditRequirement.MANDATORY:
            raise ValueError("privileged audit events must be mandatory")
        try:
            self._sink.emit(event)
        except Exception:
            if requirement is AuditRequirement.MANDATORY:
                return AuditWriteResult(AuditWriteCode.BLOCKED, False)
            return AuditWriteResult(AuditWriteCode.DROPPED, True)
        return AuditWriteResult(AuditWriteCode.WRITTEN, True)


@dataclass(frozen=True, slots=True)
class PublicError:
    code: PublicErrorCode
    correlation_id: str
    retryable: bool
    retry_after_seconds: int | None = None
    schema_version: int = PUBLIC_ERROR_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != PUBLIC_ERROR_SCHEMA_VERSION:
            raise ValueError("unsupported public error schema_version")
        if not isinstance(self.code, PublicErrorCode):
            raise ValueError("public error code must be supported")
        _require_identifier(self.correlation_id, "correlation_id")
        if not isinstance(self.retryable, bool):
            raise ValueError("retryable must be boolean")
        if self.retry_after_seconds is not None:
            if isinstance(self.retry_after_seconds, bool) or not isinstance(
                self.retry_after_seconds, int
            ):
                raise ValueError("retry_after_seconds must be an integer")
            if not 1 <= self.retry_after_seconds <= 3600:
                raise ValueError("retry_after_seconds must be bounded")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "error_code": self.code.value,
            "correlation_id": self.correlation_id,
            "retryable": self.retryable,
            "retry_after_seconds": self.retry_after_seconds,
        }


def public_error_for_failure(
    failure: InternalFailure,
    *,
    correlation_id: str,
    retry_after_seconds: int | None = None,
) -> PublicError:
    if not isinstance(failure, InternalFailure):
        raise ValueError("internal failure must be supported")
    mapping = {
        InternalFailure.UNAUTHENTICATED: (PublicErrorCode.UNAUTHENTICATED, False),
        InternalFailure.NOT_FOUND: (PublicErrorCode.NOT_AUTHORIZED, False),
        InternalFailure.NOT_AUTHORIZED: (PublicErrorCode.NOT_AUTHORIZED, False),
        InternalFailure.INVALID_REQUEST: (PublicErrorCode.INVALID_REQUEST, False),
        InternalFailure.RATE_LIMITED: (PublicErrorCode.RATE_LIMITED, True),
        InternalFailure.DEPENDENCY_FAILURE: (PublicErrorCode.UNAVAILABLE, True),
        InternalFailure.INTERNAL_FAILURE: (PublicErrorCode.INTERNAL_FAILURE, False),
    }
    code, retryable = mapping[failure]
    return PublicError(code, correlation_id, retryable, retry_after_seconds)
