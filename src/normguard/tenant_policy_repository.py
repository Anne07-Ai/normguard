"""Tenant-scoped immutable policy and activation repository contracts."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from threading import Lock
from typing import Protocol

from .hosted_authorization import (
    AuthorizedContext,
    HostedAction,
    ResourceType,
)

TENANT_POLICY_REPOSITORY_SCHEMA_VERSION = 1
_IDENTIFIER = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._:@/-]{0,126}[A-Za-z0-9])?$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$"
)


def _require_identifier(value: str, name: str) -> None:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{name} must be a bounded identifier")


def _require_digest(value: str, name: str) -> None:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")


def _require_aware(value: datetime, name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{name} must be timezone aware")


def _semver(value: str) -> tuple[int, int, int, tuple[str, ...] | None]:
    if not isinstance(value, str):
        raise ValueError("version must use semantic versioning")
    match = _SEMVER.fullmatch(value)
    if match is None:
        raise ValueError("version must use semantic versioning")
    prerelease = None if match[4] is None else tuple(match[4].split("."))
    return int(match[1]), int(match[2]), int(match[3]), prerelease


def _compare_semver(left: str, right: str) -> int:
    left_version = _semver(left)
    right_version = _semver(right)
    if left_version[:3] != right_version[:3]:
        return (left_version[:3] > right_version[:3]) - (
            left_version[:3] < right_version[:3]
        )
    left_pre, right_pre = left_version[3], right_version[3]
    if left_pre is None or right_pre is None:
        return (left_pre is None) - (right_pre is None)
    for left_item, right_item in zip(left_pre, right_pre, strict=False):
        if left_item == right_item:
            continue
        left_numeric, right_numeric = left_item.isdigit(), right_item.isdigit()
        if left_numeric and right_numeric:
            return (int(left_item) > int(right_item)) - (
                int(left_item) < int(right_item)
            )
        if left_numeric != right_numeric:
            return -1 if left_numeric else 1
        return (left_item > right_item) - (left_item < right_item)
    return (len(left_pre) > len(right_pre)) - (len(left_pre) < len(right_pre))


class RepositoryCode(StrEnum):
    SUCCESS = "success"
    NOT_FOUND = "not_found"
    NOT_AUTHORIZED = "not_authorized"
    IDENTITY_MISMATCH = "identity_mismatch"
    STALE_REVISION = "stale_revision"
    ROLLBACK_REJECTED = "rollback_rejected"


@dataclass(frozen=True, slots=True)
class TenantPolicyIdentity:
    tenant_id: str
    policy_id: str
    policy_version: str
    payload_sha256: str
    artifact_sha256: str

    def __post_init__(self) -> None:
        _require_identifier(self.tenant_id, "tenant_id")
        _require_identifier(self.policy_id, "policy_id")
        _semver(self.policy_version)
        _require_digest(self.payload_sha256, "payload_sha256")
        _require_digest(self.artifact_sha256, "artifact_sha256")


@dataclass(frozen=True, slots=True)
class TenantPolicyArtifact:
    identity: TenantPolicyIdentity
    bundle: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.bundle, bytes) or not self.bundle:
            raise ValueError("policy bundle must be non-empty bytes")
        if hashlib.sha256(self.bundle).hexdigest() != self.identity.artifact_sha256:
            raise ValueError("policy artifact digest mismatch")


@dataclass(frozen=True, slots=True)
class TenantActivation:
    tenant_id: str
    policy_id: str
    active_identity: TenantPolicyIdentity
    minimum_version: str
    revision: int
    change_id: str
    updated_at: datetime

    def __post_init__(self) -> None:
        _require_identifier(self.tenant_id, "tenant_id")
        _require_identifier(self.policy_id, "policy_id")
        _require_identifier(self.change_id, "change_id")
        _semver(self.minimum_version)
        _require_aware(self.updated_at, "updated_at")
        if isinstance(self.revision, bool) or not isinstance(self.revision, int):
            raise ValueError("revision must be an integer")
        if self.revision < 0:
            raise ValueError("revision must not be negative")
        if self.active_identity.tenant_id != self.tenant_id:
            raise ValueError("activation tenant does not match policy identity")
        if self.active_identity.policy_id != self.policy_id:
            raise ValueError("activation policy does not match policy identity")
        if _compare_semver(self.active_identity.policy_version, self.minimum_version) < 0:
            raise ValueError("active policy is below the tenant activation floor")


@dataclass(frozen=True, slots=True)
class PolicyReadResult:
    code: RepositoryCode
    artifact: TenantPolicyArtifact | None = None

    def __post_init__(self) -> None:
        if (self.code is RepositoryCode.SUCCESS) != (self.artifact is not None):
            raise ValueError("policy read result is inconsistent")


@dataclass(frozen=True, slots=True)
class ActivationReadResult:
    code: RepositoryCode
    activation: TenantActivation | None = None

    def __post_init__(self) -> None:
        if (self.code is RepositoryCode.SUCCESS) != (self.activation is not None):
            raise ValueError("activation read result is inconsistent")


@dataclass(frozen=True, slots=True)
class ActivationWriteResult:
    code: RepositoryCode
    activation: TenantActivation | None = None

    def __post_init__(self) -> None:
        if (self.code is RepositoryCode.SUCCESS) != (self.activation is not None):
            raise ValueError("activation write result is inconsistent")

    def evidence(self) -> dict[str, object]:
        return {
            "schema_version": TENANT_POLICY_REPOSITORY_SCHEMA_VERSION,
            "code": self.code.value,
            "revision": None if self.activation is None else self.activation.revision,
        }


class TenantPolicyRepository(Protocol):
    def read_policy(
        self, context: AuthorizedContext, identity: TenantPolicyIdentity
    ) -> PolicyReadResult: ...

    def read_activation(
        self, context: AuthorizedContext, policy_id: str
    ) -> ActivationReadResult: ...

    def compare_and_swap_activation(
        self,
        context: AuthorizedContext,
        candidate: TenantActivation,
        *,
        expected_revision: int | None,
    ) -> ActivationWriteResult: ...


def _can_read_policy(context: AuthorizedContext) -> bool:
    return context.action in {HostedAction.NORMALISE, HostedAction.READ_POLICY} and (
        context.resource_type in {ResourceType.NORMALISATION, ResourceType.POLICY}
    )


def _can_activate_policy(context: AuthorizedContext) -> bool:
    return (
        context.action is HostedAction.ACTIVATE_POLICY
        and context.resource_type is ResourceType.POLICY
    )


class InMemoryTenantPolicyRepository:
    """Thread-safe fake with the same isolation and CAS rules as a production adapter."""

    def __init__(
        self,
        artifacts: Iterable[TenantPolicyArtifact],
        activations: Iterable[TenantActivation] = (),
    ) -> None:
        artifact_rows = tuple(artifacts)
        activation_rows = tuple(activations)
        artifact_keys = [row.identity for row in artifact_rows]
        activation_keys = [(row.tenant_id, row.policy_id) for row in activation_rows]
        if len(artifact_keys) != len(set(artifact_keys)):
            raise ValueError("policy artifact identities must be unique")
        if len(activation_keys) != len(set(activation_keys)):
            raise ValueError("tenant activation identities must be unique")
        known = set(artifact_keys)
        if any(row.active_identity not in known for row in activation_rows):
            raise ValueError("activation references an unknown policy artifact")
        self._artifacts = {row.identity: row for row in artifact_rows}
        self._activations = {
            (row.tenant_id, row.policy_id): row for row in activation_rows
        }
        self._lock = Lock()

    def read_policy(
        self, context: AuthorizedContext, identity: TenantPolicyIdentity
    ) -> PolicyReadResult:
        if not _can_read_policy(context):
            return PolicyReadResult(RepositoryCode.NOT_AUTHORIZED)
        if context.tenant_id != identity.tenant_id:
            return PolicyReadResult(RepositoryCode.IDENTITY_MISMATCH)
        artifact = self._artifacts.get(identity)
        if artifact is None:
            return PolicyReadResult(RepositoryCode.NOT_FOUND)
        return PolicyReadResult(RepositoryCode.SUCCESS, artifact)

    def read_activation(
        self, context: AuthorizedContext, policy_id: str
    ) -> ActivationReadResult:
        _require_identifier(policy_id, "policy_id")
        if not _can_read_policy(context):
            return ActivationReadResult(RepositoryCode.NOT_AUTHORIZED)
        activation = self._activations.get((context.tenant_id, policy_id))
        if activation is None:
            return ActivationReadResult(RepositoryCode.NOT_FOUND)
        return ActivationReadResult(RepositoryCode.SUCCESS, activation)

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
        if candidate.active_identity not in self._artifacts:
            return ActivationWriteResult(RepositoryCode.NOT_FOUND)
        key = (candidate.tenant_id, candidate.policy_id)
        with self._lock:
            current = self._activations.get(key)
            current_revision = None if current is None else current.revision
            if current_revision != expected_revision:
                return ActivationWriteResult(RepositoryCode.STALE_REVISION)
            if current is not None and _compare_semver(
                candidate.minimum_version, current.minimum_version
            ) < 0:
                return ActivationWriteResult(RepositoryCode.ROLLBACK_REJECTED)
            next_revision = 0 if current is None else current.revision + 1
            stored = replace(candidate, revision=next_revision)
            self._activations[key] = stored
        return ActivationWriteResult(RepositoryCode.SUCCESS, stored)
