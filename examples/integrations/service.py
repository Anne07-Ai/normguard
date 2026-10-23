"""Framework-neutral production integration pattern for NormGuard Safety Core.

The host application owns authentication, authorization, transport limits and
storage. This adapter owns only verified core lifecycle and explicit outcome
mapping. It intentionally contains no logging of request or response content.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from threading import RLock

from normguard import (
    SDK_VERSION,
    RuntimeLimits,
    SDKError,
    TelemetrySink,
    build_safety_core,
)
from normguard.safety_contracts import EvidenceStatus


class ApplicationStatus(StrEnum):
    ACCEPTED = "accepted"
    REVIEW_REQUIRED = "review_required"
    INVALID_INPUT = "invalid_input"
    LIMIT_EXCEEDED = "limit_exceeded"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class PolicyDocuments:
    """Immutable bytes supplied by the host's trusted configuration boundary."""

    policy_bundle: bytes
    trust_store: bytes
    activation_state: bytes
    rollback_authorization: bytes | None = None
    rollback_trust_store: bytes | None = None


@dataclass(frozen=True, slots=True)
class ApplicationResponse:
    """Transport-neutral response; the host maps this to HTTP, queue or batch output."""

    status: ApplicationStatus
    output_text: str | None = None
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class ReloadResponse:
    activated: bool
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class HealthResponse:
    status: str
    ready: bool
    sdk_version: str


class SafetyCoreService:
    """Long-lived, concurrency-safe holder for one active verified core.

    Policy reload verifies and builds a replacement before taking the lock. A
    failed candidate never replaces the last known-good core.
    """

    __slots__ = ("_core", "_lock", "_runtime_limits", "_telemetry")

    def __init__(
        self,
        documents: PolicyDocuments,
        *,
        verification_time: datetime,
        runtime_limits: RuntimeLimits | None = None,
        telemetry: TelemetrySink | None = None,
    ) -> None:
        self._lock = RLock()
        self._runtime_limits = runtime_limits
        self._telemetry = telemetry
        # Startup deliberately raises SDKError on invalid/untrusted policy.
        self._core = self._build(documents, verification_time)

    def normalise(self, text: str) -> ApplicationResponse:
        # Snapshotting permits an in-flight request to finish on one immutable
        # policy while a verified replacement becomes active for later requests.
        with self._lock:
            core = self._core
        try:
            result = core.normalise(text)
        except SDKError as exc:
            if exc.code == "LIMIT_EXCEEDED":
                status = ApplicationStatus.LIMIT_EXCEEDED
            elif exc.code == "INVALID_INPUT":
                status = ApplicationStatus.INVALID_INPUT
            else:
                status = ApplicationStatus.UNAVAILABLE
            return ApplicationResponse(status=status, error_code=exc.code)
        status = (
            ApplicationStatus.REVIEW_REQUIRED
            if result.status is EvidenceStatus.REVIEW_REQUIRED
            else ApplicationStatus.ACCEPTED
        )
        return ApplicationResponse(status=status, output_text=result.output_text)

    def reload(
        self,
        documents: PolicyDocuments,
        *,
        verification_time: datetime,
    ) -> ReloadResponse:
        try:
            candidate = self._build(documents, verification_time)
        except SDKError as exc:
            return ReloadResponse(activated=False, error_code=exc.code)
        with self._lock:
            self._core = candidate
        return ReloadResponse(activated=True)

    def health(self) -> HealthResponse:
        # Do not expose policy IDs, digests, decision IDs, keys or rule content.
        with self._lock:
            ready = self._core is not None
        return HealthResponse(
            status="ready" if ready else "not_ready",
            ready=ready,
            sdk_version=SDK_VERSION,
        )

    def _build(self, documents: PolicyDocuments, verification_time: datetime):
        return build_safety_core(
            documents.policy_bundle,
            documents.trust_store,
            documents.activation_state,
            verification_time=verification_time,
            runtime_limits=self._runtime_limits,
            rollback_authorization_bytes=documents.rollback_authorization,
            rollback_trust_store_bytes=documents.rollback_trust_store,
            telemetry=self._telemetry,
        )
