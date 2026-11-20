"""Framework-neutral OIDC ingress for the bounded hosted runtime."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from .hosted_audit import InternalFailure, public_error_for_failure
from .hosted_runtime import (
    HostedNormaliseCommand,
    HostedNormaliseResponse,
    HostedRuntimeStatus,
)
from .oidc_identity import OidcIdentityResult

_IDENTIFIER = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._:@/-]{0,126}[A-Za-z0-9])?$")


def _require_identifier(value: str, name: str) -> None:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{name} must be a bounded identifier")


class IdentityVerifier(Protocol):
    def verify(
        self, token: str, *, at: datetime | None = None
    ) -> OidcIdentityResult: ...


class HostedRuntime(Protocol):
    def normalise(self, command: HostedNormaliseCommand) -> HostedNormaliseResponse: ...


@dataclass(frozen=True, slots=True)
class HostedIngressRequest:
    bearer_token: str
    requested_tenant_id: str
    text: str
    correlation_id: str
    resource_id: str = "normalisation"

    def __post_init__(self) -> None:
        if not isinstance(self.bearer_token, str) or not self.bearer_token:
            raise ValueError("bearer_token must be a non-empty string")
        for name in ("requested_tenant_id", "correlation_id", "resource_id"):
            _require_identifier(getattr(self, name), name)
        if not isinstance(self.text, str) or not self.text:
            raise ValueError("text must be a non-empty string")


class HostedOidcIngress:
    """Verify identity before constructing any command accepted by the data plane."""

    def __init__(
        self,
        *,
        identity_verifier: IdentityVerifier,
        runtime: HostedRuntime,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._identity_verifier = identity_verifier
        self._runtime = runtime
        self._clock = clock

    @staticmethod
    def _unauthenticated(correlation_id: str) -> HostedNormaliseResponse:
        return HostedNormaliseResponse(
            HostedRuntimeStatus.REJECTED,
            error=public_error_for_failure(
                InternalFailure.UNAUTHENTICATED,
                correlation_id=correlation_id,
            ),
        )

    def normalise(self, request: HostedIngressRequest) -> HostedNormaliseResponse:
        try:
            identity = self._identity_verifier.verify(
                request.bearer_token, at=self._clock()
            )
        except Exception:
            return self._unauthenticated(request.correlation_id)
        if identity.principal is None:
            return self._unauthenticated(request.correlation_id)
        command = HostedNormaliseCommand(
            principal=identity.principal,
            requested_tenant_id=request.requested_tenant_id,
            text=request.text,
            correlation_id=request.correlation_id,
            resource_id=request.resource_id,
        )
        try:
            return self._runtime.normalise(command)
        except Exception:
            return HostedNormaliseResponse(
                HostedRuntimeStatus.REJECTED,
                error=public_error_for_failure(
                    InternalFailure.INTERNAL_FAILURE,
                    correlation_id=request.correlation_id,
                ),
            )
