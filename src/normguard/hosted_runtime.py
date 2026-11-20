"""Bounded orchestration for one tenant-scoped hosted normalisation request."""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from .hosted_audit import (
    AuditOutcome,
    AuditPseudonymizer,
    AuditRequirement,
    HostedAuditEvent,
    HostedAuditRecorder,
    InternalFailure,
    PublicError,
    public_error_for_failure,
)
from .hosted_authorization import (
    AuthorizationCode,
    AuthorizationRequest,
    DenyByDefaultAuthorizer,
    HostedAction,
    ResourceType,
    VerifiedPrincipal,
)
from .tenant_policy_repository import (
    RepositoryCode,
    TenantPolicyIdentity,
    TenantPolicyRepository,
)
from .tenant_quotas import InMemoryTenantQuotaManager, QuotaCode, QuotaRequest

HOSTED_RUNTIME_SCHEMA_VERSION = 1
_IDENTIFIER = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._:@/-]{0,126}[A-Za-z0-9])?$")


def _require_identifier(value: str, name: str) -> None:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{name} must be a bounded identifier")


class HostedRuntimeStatus(StrEnum):
    ACCEPTED = "accepted"
    REVIEW_REQUIRED = "review_required"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class TenantNormalisationResult:
    output_text: str
    review_required: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.output_text, str):
            raise ValueError("output_text must be a string")
        if not isinstance(self.review_required, bool):
            raise ValueError("review_required must be boolean")


class TenantNormaliser(Protocol):
    def normalise(self, text: str) -> TenantNormalisationResult: ...


class TenantRuntimeRegistry(Protocol):
    def resolve(self, identity: TenantPolicyIdentity) -> TenantNormaliser | None: ...


class InMemoryTenantRuntimeRegistry:
    """Registry containing only runtimes verified before request processing."""

    def __init__(
        self, entries: Iterable[tuple[TenantPolicyIdentity, TenantNormaliser]]
    ) -> None:
        rows = tuple(entries)
        if len({identity for identity, _runtime in rows}) != len(rows):
            raise ValueError("runtime identities must be unique")
        self._entries = dict(rows)

    def resolve(self, identity: TenantPolicyIdentity) -> TenantNormaliser | None:
        return self._entries.get(identity)


@dataclass(frozen=True, slots=True)
class HostedNormaliseCommand:
    principal: VerifiedPrincipal
    requested_tenant_id: str
    text: str
    correlation_id: str
    resource_id: str = "normalisation"

    def __post_init__(self) -> None:
        if not isinstance(self.principal, VerifiedPrincipal):
            raise ValueError("principal must already be verified")
        for name in ("requested_tenant_id", "correlation_id", "resource_id"):
            _require_identifier(getattr(self, name), name)
        if not isinstance(self.text, str) or not self.text:
            raise ValueError("text must be a non-empty string")


@dataclass(frozen=True, slots=True)
class HostedNormaliseResponse:
    status: HostedRuntimeStatus
    output_text: str | None = None
    error: PublicError | None = None
    schema_version: int = HOSTED_RUNTIME_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != HOSTED_RUNTIME_SCHEMA_VERSION:
            raise ValueError("unsupported hosted runtime schema_version")
        if self.status is HostedRuntimeStatus.REJECTED:
            if self.error is None or self.output_text is not None:
                raise ValueError("rejected response must contain only a public error")
        elif self.error is not None or self.output_text is None:
            raise ValueError("successful response must contain only output_text")


class BoundedHostedRuntime:
    """Fail-closed composition root for the hosted data plane."""

    def __init__(
        self,
        *,
        policy_id: str,
        authorizer: DenyByDefaultAuthorizer,
        policy_repository: TenantPolicyRepository,
        quota_manager: InMemoryTenantQuotaManager,
        runtime_registry: TenantRuntimeRegistry,
        audit_recorder: HostedAuditRecorder,
        audit_pseudonymizer: AuditPseudonymizer,
        event_id_factory: Callable[[], str] = lambda: uuid.uuid4().hex,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        _require_identifier(policy_id, "policy_id")
        self._policy_id = policy_id
        self._authorizer = authorizer
        self._policies = policy_repository
        self._quotas = quota_manager
        self._runtimes = runtime_registry
        self._audit = audit_recorder
        self._pseudonymizer = audit_pseudonymizer
        self._event_id_factory = event_id_factory
        self._clock = clock

    @staticmethod
    def _reject(
        failure: InternalFailure,
        correlation_id: str,
        *,
        retry_after_seconds: int | None = None,
    ) -> HostedNormaliseResponse:
        return HostedNormaliseResponse(
            HostedRuntimeStatus.REJECTED,
            error=public_error_for_failure(
                failure,
                correlation_id=correlation_id,
                retry_after_seconds=retry_after_seconds,
            ),
        )

    def _record(
        self,
        context,
        command: HostedNormaliseCommand,
        *,
        outcome: AuditOutcome,
        reason_code: str,
        artifact_sha256: str | None = None,
    ) -> None:
        event = HostedAuditEvent.from_authorized_context(
            context,
            pseudonymizer=self._pseudonymizer,
            event_id=self._event_id_factory(),
            correlation_id=command.correlation_id,
            outcome=outcome,
            reason_code=reason_code,
            privileged=False,
            artifact_sha256=artifact_sha256,
            occurred_at=self._clock(),
        )
        self._audit.record(event, requirement=AuditRequirement.BEST_EFFORT)

    def normalise(self, command: HostedNormaliseCommand) -> HostedNormaliseResponse:
        request = AuthorizationRequest(
            HostedAction.NORMALISE,
            ResourceType.NORMALISATION,
            command.resource_id,
            command.requested_tenant_id,
        )
        decision = self._authorizer.authorize(
            command.principal, request, at=self._clock()
        )
        if not decision.allowed:
            failure = {
                AuthorizationCode.CREDENTIAL_EXPIRED: InternalFailure.UNAUTHENTICATED,
                AuthorizationCode.DEPENDENCY_FAILURE: InternalFailure.DEPENDENCY_FAILURE,
            }.get(decision.code, InternalFailure.NOT_AUTHORIZED)
            return self._reject(failure, command.correlation_id)
        context = decision.context
        assert context is not None

        try:
            quota = self._quotas.acquire(
                QuotaRequest(context, len(command.text.encode("utf-8")))
            )
        except Exception:
            return self._reject(InternalFailure.DEPENDENCY_FAILURE, command.correlation_id)
        if not quota.allowed:
            failure = (
                InternalFailure.INVALID_REQUEST
                if quota.code in {QuotaCode.INPUT_LIMIT, QuotaCode.BATCH_LIMIT}
                else InternalFailure.RATE_LIMITED
            )
            return self._reject(
                failure,
                command.correlation_id,
                retry_after_seconds=quota.retry_after_seconds,
            )

        assert quota.lease is not None
        with quota.lease:
            try:
                activation = self._policies.read_activation(context, self._policy_id)
                if (
                    activation.code is not RepositoryCode.SUCCESS
                    or activation.activation is None
                ):
                    self._record(
                        context,
                        command,
                        outcome=AuditOutcome.FAILED,
                        reason_code="policy_unavailable",
                    )
                    return self._reject(
                        InternalFailure.DEPENDENCY_FAILURE, command.correlation_id
                    )
                identity = activation.activation.active_identity
                policy = self._policies.read_policy(context, identity)
                if policy.code is not RepositoryCode.SUCCESS or policy.artifact is None:
                    self._record(
                        context,
                        command,
                        outcome=AuditOutcome.FAILED,
                        reason_code="policy_unavailable",
                    )
                    return self._reject(
                        InternalFailure.DEPENDENCY_FAILURE, command.correlation_id
                    )
                runtime = self._runtimes.resolve(identity)
                if runtime is None:
                    self._record(
                        context,
                        command,
                        outcome=AuditOutcome.FAILED,
                        reason_code="runtime_unavailable",
                        artifact_sha256=identity.artifact_sha256,
                    )
                    return self._reject(
                        InternalFailure.DEPENDENCY_FAILURE, command.correlation_id
                    )
                result = runtime.normalise(command.text)
            except Exception:
                self._record(
                    context,
                    command,
                    outcome=AuditOutcome.FAILED,
                    reason_code="runtime_failure",
                )
                return self._reject(
                    InternalFailure.INTERNAL_FAILURE, command.correlation_id
                )

            status = (
                HostedRuntimeStatus.REVIEW_REQUIRED
                if result.review_required
                else HostedRuntimeStatus.ACCEPTED
            )
            self._record(
                context,
                command,
                outcome=AuditOutcome.SUCCEEDED,
                reason_code=status.value,
                artifact_sha256=identity.artifact_sha256,
            )
            return HostedNormaliseResponse(status, output_text=result.output_text)
