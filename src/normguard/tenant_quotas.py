"""Atomic deployment, tenant and principal quota contracts for hosted NormGuard."""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from threading import Lock

from .hosted_authorization import AuthorizedContext

QUOTA_SCHEMA_VERSION = 1


class QuotaCode(StrEnum):
    ALLOW = "allow"
    INPUT_LIMIT = "input_limit"
    BATCH_LIMIT = "batch_limit"
    RATE_LIMIT = "rate_limit"
    CONCURRENCY_LIMIT = "concurrency_limit"


@dataclass(frozen=True, slots=True)
class QuotaProfile:
    requests_per_window: int
    max_concurrency: int
    max_input_bytes: int
    max_batch_size: int

    def __post_init__(self) -> None:
        limits = {
            "requests_per_window": (self.requests_per_window, 1_000_000),
            "max_concurrency": (self.max_concurrency, 10_000),
            "max_input_bytes": (self.max_input_bytes, 10_000_000),
            "max_batch_size": (self.max_batch_size, 1_000),
        }
        for name, (value, maximum) in limits.items():
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
                raise ValueError(f"{name} must be an integer in [1, {maximum}]")

    @classmethod
    def strictest(cls, *profiles: QuotaProfile) -> QuotaProfile:
        if not profiles:
            raise ValueError("at least one quota profile is required")
        return cls(
            min(item.requests_per_window for item in profiles),
            min(item.max_concurrency for item in profiles),
            min(item.max_input_bytes for item in profiles),
            min(item.max_batch_size for item in profiles),
        )


@dataclass(frozen=True, slots=True)
class QuotaRequest:
    context: AuthorizedContext
    input_bytes: int
    batch_size: int = 1

    def __post_init__(self) -> None:
        for name in ("input_bytes", "batch_size"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")


@dataclass(slots=True)
class _WindowState:
    started_at: float
    accepted: int = 0


class QuotaLease:
    """One accepted concurrent operation; release is thread-safe and idempotent."""

    def __init__(self, release: Callable[[], None]) -> None:
        self._release = release
        self._released = False
        self._lock = Lock()

    def release(self) -> None:
        with self._lock:
            if self._released:
                return
            self._released = True
        self._release()

    def __enter__(self) -> QuotaLease:
        return self

    def __exit__(self, *_args: object) -> None:
        self.release()


@dataclass(frozen=True, slots=True)
class QuotaDecision:
    allowed: bool
    code: QuotaCode
    lease: QuotaLease | None = None
    retry_after_seconds: int | None = None
    schema_version: int = QUOTA_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != QUOTA_SCHEMA_VERSION:
            raise ValueError("unsupported quota schema_version")
        if self.allowed != (self.code is QuotaCode.ALLOW):
            raise ValueError("quota code and allowed state disagree")
        if self.allowed != (self.lease is not None):
            raise ValueError("only an allowed quota decision may contain a lease")
        if self.retry_after_seconds is not None and not 1 <= self.retry_after_seconds <= 3600:
            raise ValueError("retry_after_seconds must be bounded")

    def evidence(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "allowed": self.allowed,
            "code": self.code.value,
            "retry_after_seconds": self.retry_after_seconds,
        }


class InMemoryTenantQuotaManager:
    """Deterministic reference implementation with atomic multi-scope admission."""

    _DEPLOYMENT = ("deployment", "all")

    def __init__(
        self,
        deployment_profile: QuotaProfile,
        *,
        tenant_profiles: Mapping[str, QuotaProfile] | None = None,
        principal_profiles: Mapping[str, QuotaProfile] | None = None,
        window_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not 1 <= window_seconds <= 3600:
            raise ValueError("window_seconds must be in [1, 3600]")
        self._deployment_profile = deployment_profile
        self._tenant_profiles = dict(tenant_profiles or {})
        self._principal_profiles = dict(principal_profiles or {})
        self._window_seconds = window_seconds
        self._clock = clock
        self._windows: dict[tuple[str, str], _WindowState] = {}
        self._concurrent: dict[tuple[str, str], int] = {}
        self._lock = Lock()

    def _scopes(
        self, request: QuotaRequest
    ) -> tuple[tuple[tuple[str, str], QuotaProfile], ...]:
        tenant_override = self._tenant_profiles.get(request.context.tenant_id)
        tenant_profile = QuotaProfile.strictest(
            self._deployment_profile,
            *(tuple() if tenant_override is None else (tenant_override,)),
        )
        principal_override = self._principal_profiles.get(request.context.principal_id)
        principal_profile = QuotaProfile.strictest(
            tenant_profile,
            *(tuple() if principal_override is None else (principal_override,)),
        )
        return (
            (self._DEPLOYMENT, self._deployment_profile),
            (("tenant", request.context.tenant_id), tenant_profile),
            (("principal", request.context.principal_id), principal_profile),
        )

    def _retry_after(self, now: float, windows: tuple[_WindowState, ...]) -> int:
        remaining = min(self._window_seconds - (now - state.started_at) for state in windows)
        return min(3600, max(1, math.ceil(remaining)))

    def acquire(self, request: QuotaRequest) -> QuotaDecision:
        scopes = self._scopes(request)
        strictest = QuotaProfile.strictest(*(profile for _, profile in scopes))
        if request.input_bytes > strictest.max_input_bytes:
            return QuotaDecision(False, QuotaCode.INPUT_LIMIT)
        if request.batch_size > strictest.max_batch_size:
            return QuotaDecision(False, QuotaCode.BATCH_LIMIT)

        now = self._clock()
        with self._lock:
            windows: list[_WindowState] = []
            for key, _profile in scopes:
                state = self._windows.get(key)
                if state is None or now - state.started_at >= self._window_seconds:
                    state = _WindowState(now)
                    self._windows[key] = state
                windows.append(state)

            rate_blocked = tuple(
                state
                for state, (_key, profile) in zip(windows, scopes, strict=True)
                if state.accepted >= profile.requests_per_window
            )
            if rate_blocked:
                return QuotaDecision(
                    False,
                    QuotaCode.RATE_LIMIT,
                    retry_after_seconds=self._retry_after(now, rate_blocked),
                )
            if any(
                self._concurrent.get(key, 0) >= profile.max_concurrency
                for key, profile in scopes
            ):
                return QuotaDecision(False, QuotaCode.CONCURRENCY_LIMIT)

            for state in windows:
                state.accepted += 1
            keys = tuple(key for key, _profile in scopes)
            for key in keys:
                self._concurrent[key] = self._concurrent.get(key, 0) + 1

        return QuotaDecision(True, QuotaCode.ALLOW, QuotaLease(lambda: self._release(keys)))

    def _release(self, keys: tuple[tuple[str, str], ...]) -> None:
        with self._lock:
            for key in keys:
                current = self._concurrent.get(key, 0)
                if current < 1:
                    raise RuntimeError("quota concurrency state underflow")
            for key in keys:
                self._concurrent[key] -= 1
