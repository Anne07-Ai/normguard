"""PostgreSQL adapters for authoritative hosted tenant state."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import replace
from datetime import datetime
from typing import Protocol

from .hosted_authorization import (
    AuthorizedContext,
    HostedAction,
    ResourceType,
    TenantGrant,
)
from .tenant_policy_repository import (
    ActivationReadResult,
    ActivationWriteResult,
    PolicyReadResult,
    RepositoryCode,
    TenantActivation,
    TenantPolicyArtifact,
    TenantPolicyIdentity,
    _can_activate_policy,
    _can_read_policy,
    _compare_semver,
)

_IDENTIFIER = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._:@/-]{0,126}[A-Za-z0-9])?$")

_SET_PRINCIPAL = "SELECT set_config('normguard.principal_id', %s, true)"
_SET_TENANT = "SELECT set_config('normguard.tenant_id', %s, true)"
_FIND_GRANTS = """
SELECT grant_id, principal_id, tenant_id, actions, resource_types, active_from, expires_at
FROM normguard_tenant_grants
WHERE principal_id = %s AND active_from <= %s AND expires_at > %s
ORDER BY grant_id
"""
_READ_POLICY = """
SELECT bundle
FROM normguard_policy_artifacts
WHERE tenant_id = %s AND policy_id = %s AND policy_version = %s
  AND payload_sha256 = %s AND artifact_sha256 = %s
"""
_READ_ACTIVATION = """
SELECT policy_version, payload_sha256, artifact_sha256, minimum_version,
       revision, change_id, updated_at
FROM normguard_tenant_activations
WHERE tenant_id = %s AND policy_id = %s
"""
_LOCK_ACTIVATION = _READ_ACTIVATION.rstrip() + "\nFOR UPDATE"
_ARTIFACT_EXISTS = """
SELECT 1
FROM normguard_policy_artifacts
WHERE tenant_id = %s AND policy_id = %s AND policy_version = %s
  AND payload_sha256 = %s AND artifact_sha256 = %s
"""
_INSERT_ACTIVATION = """
INSERT INTO normguard_tenant_activations
    (tenant_id, policy_id, policy_version, payload_sha256, artifact_sha256,
     minimum_version, revision, change_id, updated_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (tenant_id, policy_id) DO NOTHING
RETURNING revision
"""
_UPDATE_ACTIVATION = """
UPDATE normguard_tenant_activations
SET policy_version = %s, payload_sha256 = %s, artifact_sha256 = %s,
    minimum_version = %s, revision = %s, change_id = %s, updated_at = %s
WHERE tenant_id = %s AND policy_id = %s AND revision = %s
RETURNING revision
"""


class Cursor(Protocol):
    def execute(self, query: str, parameters: Sequence[object] = ()) -> object: ...

    def fetchone(self) -> Sequence[object] | None: ...

    def fetchall(self) -> Sequence[Sequence[object]]: ...


class Connection(Protocol):
    def cursor(self) -> AbstractContextManager[Cursor]: ...


ConnectionFactory = Callable[[], AbstractContextManager[Connection]]


def _require_identifier(value: str, name: str) -> None:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{name} must be a bounded identifier")


def _require_aware(value: datetime, name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{name} must be timezone aware")


def _set_principal(cursor: Cursor, principal_id: str) -> None:
    cursor.execute(_SET_PRINCIPAL, (principal_id,))


def _set_tenant(cursor: Cursor, tenant_id: str) -> None:
    cursor.execute(_SET_TENANT, (tenant_id,))


def _identity_parameters(identity: TenantPolicyIdentity) -> tuple[str, ...]:
    return (
        identity.tenant_id,
        identity.policy_id,
        identity.policy_version,
        identity.payload_sha256,
        identity.artifact_sha256,
    )


def _activation_from_row(
    tenant_id: str, policy_id: str, row: Sequence[object]
) -> TenantActivation:
    if len(row) != 7:
        raise ValueError("activation row shape is invalid")
    policy_version, payload_sha256, artifact_sha256 = row[:3]
    minimum_version, revision, change_id, updated_at = row[3:]
    identity = TenantPolicyIdentity(
        tenant_id,
        policy_id,
        str(policy_version),
        str(payload_sha256),
        str(artifact_sha256),
    )
    return TenantActivation(
        tenant_id,
        policy_id,
        identity,
        str(minimum_version),
        revision,
        str(change_id),
        updated_at,
    )


class PostgresTenantGrantRepository:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    def find_grants(self, principal_id: str, *, at: datetime) -> tuple[TenantGrant, ...]:
        _require_identifier(principal_id, "principal_id")
        _require_aware(at, "at")
        with self._connection_factory() as connection, connection.cursor() as cursor:
            _set_principal(cursor, principal_id)
            cursor.execute(_FIND_GRANTS, (principal_id, at, at))
            rows = cursor.fetchall()
        grants = []
        for row in rows:
            if len(row) != 7:
                raise ValueError("tenant grant row shape is invalid")
            grant_id, row_principal, tenant_id, actions, resources, start, expiry = row
            if row_principal != principal_id:
                raise ValueError("database returned a mismatched principal")
            grants.append(
                TenantGrant(
                    str(grant_id),
                    str(row_principal),
                    str(tenant_id),
                    frozenset(HostedAction(str(value)) for value in actions),
                    frozenset(ResourceType(str(value)) for value in resources),
                    start,
                    expiry,
                )
            )
        return tuple(grants)


class PostgresTenantPolicyRepository:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    def read_policy(
        self, context: AuthorizedContext, identity: TenantPolicyIdentity
    ) -> PolicyReadResult:
        if not _can_read_policy(context):
            return PolicyReadResult(RepositoryCode.NOT_AUTHORIZED)
        if context.tenant_id != identity.tenant_id:
            return PolicyReadResult(RepositoryCode.IDENTITY_MISMATCH)
        with self._connection_factory() as connection, connection.cursor() as cursor:
            _set_tenant(cursor, context.tenant_id)
            cursor.execute(_READ_POLICY, _identity_parameters(identity))
            row = cursor.fetchone()
        if row is None:
            return PolicyReadResult(RepositoryCode.NOT_FOUND)
        if len(row) != 1:
            raise ValueError("policy row shape is invalid")
        return PolicyReadResult(
            RepositoryCode.SUCCESS,
            TenantPolicyArtifact(identity, bytes(row[0])),
        )

    def read_activation(
        self, context: AuthorizedContext, policy_id: str
    ) -> ActivationReadResult:
        _require_identifier(policy_id, "policy_id")
        if not _can_read_policy(context):
            return ActivationReadResult(RepositoryCode.NOT_AUTHORIZED)
        with self._connection_factory() as connection, connection.cursor() as cursor:
            _set_tenant(cursor, context.tenant_id)
            cursor.execute(_READ_ACTIVATION, (context.tenant_id, policy_id))
            row = cursor.fetchone()
        if row is None:
            return ActivationReadResult(RepositoryCode.NOT_FOUND)
        return ActivationReadResult(
            RepositoryCode.SUCCESS,
            _activation_from_row(context.tenant_id, policy_id, row),
        )

    def compare_and_swap_activation(
        self,
        context: AuthorizedContext,
        candidate: TenantActivation,
        *,
        expected_revision: int | None,
    ) -> ActivationWriteResult:
        if not _can_activate_policy(context):
            return ActivationWriteResult(RepositoryCode.NOT_AUTHORIZED)
        if context.tenant_id != candidate.tenant_id:
            return ActivationWriteResult(RepositoryCode.IDENTITY_MISMATCH)
        if expected_revision is not None and (
            isinstance(expected_revision, bool)
            or not isinstance(expected_revision, int)
            or expected_revision < 0
        ):
            raise ValueError("expected_revision must be a non-negative integer or None")

        with self._connection_factory() as connection, connection.cursor() as cursor:
            _set_tenant(cursor, context.tenant_id)
            cursor.execute(_ARTIFACT_EXISTS, _identity_parameters(candidate.active_identity))
            if cursor.fetchone() is None:
                return ActivationWriteResult(RepositoryCode.NOT_FOUND)
            cursor.execute(
                _LOCK_ACTIVATION, (candidate.tenant_id, candidate.policy_id)
            )
            current_row = cursor.fetchone()
            current = (
                None
                if current_row is None
                else _activation_from_row(
                    candidate.tenant_id, candidate.policy_id, current_row
                )
            )
            current_revision = None if current is None else current.revision
            if current_revision != expected_revision:
                return ActivationWriteResult(RepositoryCode.STALE_REVISION)
            if current is not None and _compare_semver(
                candidate.minimum_version, current.minimum_version
            ) < 0:
                return ActivationWriteResult(RepositoryCode.ROLLBACK_REJECTED)

            next_revision = 0 if current is None else current.revision + 1
            stored = replace(candidate, revision=next_revision)
            if current is None:
                cursor.execute(
                    _INSERT_ACTIVATION,
                    (
                        stored.tenant_id,
                        stored.policy_id,
                        stored.active_identity.policy_version,
                        stored.active_identity.payload_sha256,
                        stored.active_identity.artifact_sha256,
                        stored.minimum_version,
                        stored.revision,
                        stored.change_id,
                        stored.updated_at,
                    ),
                )
            else:
                cursor.execute(
                    _UPDATE_ACTIVATION,
                    (
                        stored.active_identity.policy_version,
                        stored.active_identity.payload_sha256,
                        stored.active_identity.artifact_sha256,
                        stored.minimum_version,
                        stored.revision,
                        stored.change_id,
                        stored.updated_at,
                        stored.tenant_id,
                        stored.policy_id,
                        expected_revision,
                    ),
                )
            if cursor.fetchone() is None:
                return ActivationWriteResult(RepositoryCode.STALE_REVISION)
        return ActivationWriteResult(RepositoryCode.SUCCESS, stored)
