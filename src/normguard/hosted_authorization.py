"""Framework-neutral identity and tenant authorization contracts for NG-T22."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

AUTHORIZATION_SCHEMA_VERSION = 1
_IDENTIFIER = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._:@/-]{0,126}[A-Za-z0-9])?$")


def _require_identifier(value: str, name: str) -> None:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{name} must be a bounded identifier")


def _require_aware(value: datetime, name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{name} must be timezone aware")


class PrincipalKind(StrEnum):
    HUMAN = "human"
    SERVICE = "service"


class HostedAction(StrEnum):
    NORMALISE = "normalise"
    READ_POLICY = "read_policy"
    ACTIVATE_POLICY = "activate_policy"
    MANAGE_GRANT = "manage_grant"


class ResourceType(StrEnum):
    NORMALISATION = "normalisation"
    POLICY = "policy"
    GRANT = "grant"


class AuthorizationCode(StrEnum):
    ALLOW = "allow"
    CREDENTIAL_EXPIRED = "credential_expired"
    NOT_AUTHORIZED = "not_authorized"
    TENANT_CONFLICT = "tenant_conflict"
    AMBIGUOUS_TENANT = "ambiguous_tenant"
    DEPENDENCY_FAILURE = "dependency_failure"


@dataclass(frozen=True, slots=True)
class VerifiedPrincipal:
    """Identity already cryptographically verified by the trusted identity adapter."""

    principal_id: str
    issuer: str
    subject: str
    audience: str
    kind: PrincipalKind
    authenticated_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.kind, PrincipalKind):
            raise ValueError("kind must be a supported principal kind")
        for name in ("principal_id", "issuer", "subject", "audience"):
            _require_identifier(getattr(self, name), name)
        _require_aware(self.authenticated_at, "authenticated_at")
        _require_aware(self.expires_at, "expires_at")
        if self.expires_at <= self.authenticated_at:
            raise ValueError("expires_at must be after authenticated_at")

    def is_valid_at(self, at: datetime) -> bool:
        _require_aware(at, "at")
        return self.authenticated_at <= at < self.expires_at


@dataclass(frozen=True, slots=True)
class TenantGrant:
    """Authoritative tenant/action/resource membership assigned by the platform."""

    grant_id: str
    principal_id: str
    tenant_id: str
    actions: frozenset[HostedAction]
    resource_types: frozenset[ResourceType]
    active_from: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        for name in ("grant_id", "principal_id", "tenant_id"):
            _require_identifier(getattr(self, name), name)
        _require_aware(self.active_from, "active_from")
        _require_aware(self.expires_at, "expires_at")
        if self.expires_at <= self.active_from:
            raise ValueError("grant expires_at must be after active_from")
        if not self.actions or not self.resource_types:
            raise ValueError("grant actions and resource_types must not be empty")
        if any(not isinstance(action, HostedAction) for action in self.actions):
            raise ValueError("grant contains an unsupported action")
        if any(not isinstance(resource, ResourceType) for resource in self.resource_types):
            raise ValueError("grant contains an unsupported resource type")

    def is_active_at(self, at: datetime) -> bool:
        _require_aware(at, "at")
        return self.active_from <= at < self.expires_at

    def permits(self, action: HostedAction, resource_type: ResourceType) -> bool:
        return action in self.actions and resource_type in self.resource_types


@dataclass(frozen=True, slots=True)
class AuthorizationRequest:
    action: HostedAction
    resource_type: ResourceType
    resource_id: str | None = None
    requested_tenant_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.action, HostedAction):
            raise ValueError("action must be supported")
        if not isinstance(self.resource_type, ResourceType):
            raise ValueError("resource_type must be supported")
        if self.resource_id is not None:
            _require_identifier(self.resource_id, "resource_id")
        if self.requested_tenant_id is not None:
            _require_identifier(self.requested_tenant_id, "requested_tenant_id")


@dataclass(frozen=True, slots=True)
class AuthorizedContext:
    """Immutable authority derived from a verified principal and authoritative grant."""

    principal_id: str
    tenant_id: str
    action: HostedAction
    resource_type: ResourceType
    resource_id: str | None
    grant_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_identifier(self.principal_id, "principal_id")
        _require_identifier(self.tenant_id, "tenant_id")
        if self.resource_id is not None:
            _require_identifier(self.resource_id, "resource_id")
        if not self.grant_ids:
            raise ValueError("authorized context requires an authoritative grant")
        for grant_id in self.grant_ids:
            _require_identifier(grant_id, "grant_id")


@dataclass(frozen=True, slots=True)
class AuthorizationDecision:
    allowed: bool
    code: AuthorizationCode
    context: AuthorizedContext | None = None
    schema_version: int = AUTHORIZATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != AUTHORIZATION_SCHEMA_VERSION:
            raise ValueError("unsupported authorization schema_version")
        if self.allowed != (self.code is AuthorizationCode.ALLOW):
            raise ValueError("authorization code and allowed state disagree")
        if self.allowed != (self.context is not None):
            raise ValueError("only an allowed decision may contain context")

    def evidence(self) -> dict[str, object]:
        """Return bounded evidence without identity, tenant, credential or resource IDs."""
        return {
            "schema_version": self.schema_version,
            "allowed": self.allowed,
            "code": self.code.value,
            "action": None if self.context is None else self.context.action.value,
            "resource_type": (
                None if self.context is None else self.context.resource_type.value
            ),
        }


class TenantGrantRepository(Protocol):
    def find_grants(self, principal_id: str, *, at: datetime) -> tuple[TenantGrant, ...]: ...


class InMemoryTenantGrantRepository:
    """Deterministic fake that preserves the production repository contract."""

    def __init__(self, grants: Iterable[TenantGrant]) -> None:
        self._grants = tuple(grants)

    def find_grants(self, principal_id: str, *, at: datetime) -> tuple[TenantGrant, ...]:
        _require_identifier(principal_id, "principal_id")
        _require_aware(at, "at")
        return tuple(
            grant
            for grant in self._grants
            if grant.principal_id == principal_id and grant.is_active_at(at)
        )


class DenyByDefaultAuthorizer:
    def __init__(self, repository: TenantGrantRepository) -> None:
        self._repository = repository

    @staticmethod
    def _deny(code: AuthorizationCode) -> AuthorizationDecision:
        return AuthorizationDecision(False, code)

    def authorize(
        self,
        principal: VerifiedPrincipal,
        request: AuthorizationRequest,
        *,
        at: datetime | None = None,
    ) -> AuthorizationDecision:
        decision_time = at or datetime.now(UTC)
        _require_aware(decision_time, "at")
        if not principal.is_valid_at(decision_time):
            return self._deny(AuthorizationCode.CREDENTIAL_EXPIRED)

        try:
            grants = self._repository.find_grants(principal.principal_id, at=decision_time)
        except Exception:
            return self._deny(AuthorizationCode.DEPENDENCY_FAILURE)

        permitted = tuple(
            grant
            for grant in grants
            if grant.principal_id == principal.principal_id
            and grant.permits(request.action, request.resource_type)
        )
        if not permitted:
            return self._deny(AuthorizationCode.NOT_AUTHORIZED)

        if request.requested_tenant_id is not None:
            permitted = tuple(
                grant for grant in permitted if grant.tenant_id == request.requested_tenant_id
            )
            if not permitted:
                return self._deny(AuthorizationCode.TENANT_CONFLICT)

        tenant_ids = {grant.tenant_id for grant in permitted}
        if len(tenant_ids) != 1:
            return self._deny(AuthorizationCode.AMBIGUOUS_TENANT)
        tenant_id = next(iter(tenant_ids))
        grant_ids = tuple(sorted({grant.grant_id for grant in permitted}))
        context = AuthorizedContext(
            principal_id=principal.principal_id,
            tenant_id=tenant_id,
            action=request.action,
            resource_type=request.resource_type,
            resource_id=request.resource_id,
            grant_ids=grant_ids,
        )
        return AuthorizationDecision(True, AuthorizationCode.ALLOW, context)
