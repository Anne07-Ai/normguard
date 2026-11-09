"""Optional, bounded HTTP boundary for a private single-tenant pilot."""

from __future__ import annotations

import asyncio
import base64
import binascii
import os
import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from threading import Lock
from typing import Protocol

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError

from normguard.runtime import RuntimeLimits
from normguard.safety_contracts import EvidenceStatus
from normguard.sdk import SDK_VERSION, SDKError, build_safety_core

SERVICE_SCHEMA_VERSION = 1


class ApplicationStatus(StrEnum):
    ACCEPTED = "accepted"
    REVIEW_REQUIRED = "review_required"
    INVALID_INPUT = "invalid_input"
    LIMIT_EXCEEDED = "limit_exceeded"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class ApplicationResponse:
    status: ApplicationStatus
    output_text: str | None = None
    error_code: str | None = None


class PilotService(Protocol):
    def normalise(self, text: str) -> ApplicationResponse: ...

    def health(self) -> object: ...


@dataclass(frozen=True, slots=True)
class PolicyDocuments:
    policy_bundle: bytes
    trust_store: bytes
    activation_state: bytes


@dataclass(frozen=True, slots=True)
class HealthResponse:
    status: str
    ready: bool
    sdk_version: str


class PrivatePilotService:
    """One immutable verified core with no network policy mutation surface."""

    def __init__(
        self,
        documents: PolicyDocuments,
        *,
        verification_time: datetime,
        runtime_limits: RuntimeLimits,
    ) -> None:
        self._core = build_safety_core(
            documents.policy_bundle,
            documents.trust_store,
            documents.activation_state,
            verification_time=verification_time,
            runtime_limits=runtime_limits,
        )

    def normalise(self, text: str) -> ApplicationResponse:
        try:
            result = self._core.normalise(text)
        except SDKError as exc:
            status = {
                "INVALID_INPUT": ApplicationStatus.INVALID_INPUT,
                "LIMIT_EXCEEDED": ApplicationStatus.LIMIT_EXCEEDED,
            }.get(exc.code, ApplicationStatus.UNAVAILABLE)
            return ApplicationResponse(status=status, error_code=exc.code)
        status = (
            ApplicationStatus.REVIEW_REQUIRED
            if result.status is EvidenceStatus.REVIEW_REQUIRED
            else ApplicationStatus.ACCEPTED
        )
        return ApplicationResponse(status=status, output_text=result.output_text)

    def health(self) -> HealthResponse:
        return HealthResponse("ready", True, SDK_VERSION)


class NormaliseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    text: str


@dataclass(frozen=True, slots=True)
class ServiceSettings:
    bearer_token: str
    max_body_bytes: int = 70_000
    max_concurrency: int = 4
    concurrency_wait_seconds: float = 0.25
    requests_per_minute: int = 120

    def __post_init__(self) -> None:
        if not isinstance(self.bearer_token, str) or not 32 <= len(self.bearer_token) <= 4096:
            raise ValueError("bearer_token must contain 32-4096 characters")
        for name in ("max_body_bytes", "max_concurrency", "requests_per_minute"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if self.max_body_bytes > 1_000_000 or self.max_concurrency > 64:
            raise ValueError("service settings exceed bounded pilot limits")
        if not 0 < self.concurrency_wait_seconds <= 10:
            raise ValueError("concurrency_wait_seconds must be in (0, 10]")


class FixedWindowRateLimiter:
    """One-principal limiter; token variations cannot create new buckets."""

    def __init__(
        self,
        maximum: int,
        *,
        window_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if maximum < 1 or window_seconds <= 0:
            raise ValueError("rate limiter bounds must be positive")
        self._clock = clock
        self._count = 0
        self._lock = Lock()
        self._maximum = maximum
        self._window_seconds = window_seconds
        self._window_started = clock()

    def acquire(self) -> tuple[bool, int]:
        now = self._clock()
        with self._lock:
            elapsed = now - self._window_started
            if elapsed >= self._window_seconds:
                self._window_started = now
                self._count = 0
                elapsed = 0
            retry_after = max(1, int(self._window_seconds - elapsed + 0.999))
            if self._count >= self._maximum:
                return False, retry_after
            self._count += 1
            return True, retry_after


def _error(status_code: int, code: str, *, headers: Mapping[str, str] | None = None):
    return JSONResponse(
        status_code=status_code,
        content={"schema_version": SERVICE_SCHEMA_VERSION, "error_code": code},
        headers=dict(headers or {}),
    )


def create_app(
    service: PilotService,
    settings: ServiceSettings,
    *,
    limiter: FixedWindowRateLimiter | None = None,
) -> FastAPI:
    """Create a private pilot app around one pre-verified immutable service."""

    app = FastAPI(
        title="NormGuard private pilot",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    capacity = asyncio.Semaphore(settings.max_concurrency)
    rate_limiter = limiter or FixedWindowRateLimiter(settings.requests_per_minute)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    @app.exception_handler(RequestValidationError)
    async def request_validation_error(_request: Request, _exc: RequestValidationError):
        return _error(422, "INVALID_REQUEST")

    @app.exception_handler(Exception)
    async def internal_error(_request: Request, _exc: Exception):
        return _error(500, "INTERNAL_FAILURE")

    @app.get("/")
    @app.get("/health")
    async def health():
        state = service.health()
        return {
            "schema_version": SERVICE_SCHEMA_VERSION,
            "status": state.status,
            "ready": state.ready,
            "sdk_version": state.sdk_version,
        }

    @app.post("/v1/normalise")
    async def normalise(request: Request):
        authorization = request.headers.get("authorization")
        if not authorization or not authorization.startswith("Bearer "):
            return _error(401, "UNAUTHENTICATED", headers={"WWW-Authenticate": "Bearer"})
        supplied = authorization.removeprefix("Bearer ")
        if not supplied or not secrets.compare_digest(supplied, settings.bearer_token):
            return _error(401, "UNAUTHENTICATED", headers={"WWW-Authenticate": "Bearer"})

        permitted, retry_after = rate_limiter.acquire()
        if not permitted:
            return _error(429, "RATE_LIMITED", headers={"Retry-After": str(retry_after)})
        content_type = request.headers.get("content-type", "").partition(";")[0].strip().lower()
        if content_type != "application/json":
            return _error(415, "UNSUPPORTED_MEDIA_TYPE")
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                parsed_length = int(content_length)
                if parsed_length < 0:
                    return _error(400, "INVALID_CONTENT_LENGTH")
                if parsed_length > settings.max_body_bytes:
                    return _error(413, "REQUEST_TOO_LARGE")
            except ValueError:
                return _error(400, "INVALID_CONTENT_LENGTH")
        body = await request.body()
        if len(body) > settings.max_body_bytes:
            return _error(413, "REQUEST_TOO_LARGE")
        try:
            payload = NormaliseRequest.model_validate_json(body)
        except (ValidationError, ValueError):
            return _error(422, "INVALID_REQUEST")

        try:
            await asyncio.wait_for(
                capacity.acquire(),
                timeout=settings.concurrency_wait_seconds,
            )
        except TimeoutError:
            return _error(503, "CAPACITY_EXHAUSTED", headers={"Retry-After": "1"})
        try:
            result = service.normalise(payload.text)
        finally:
            capacity.release()

        status_codes = {
            ApplicationStatus.ACCEPTED: 200,
            ApplicationStatus.REVIEW_REQUIRED: 202,
            ApplicationStatus.INVALID_INPUT: 400,
            ApplicationStatus.LIMIT_EXCEEDED: 413,
            ApplicationStatus.UNAVAILABLE: 503,
        }
        return JSONResponse(
            status_code=status_codes[result.status],
            content={
                "schema_version": SERVICE_SCHEMA_VERSION,
                "status": result.status.value,
                "output_text": result.output_text,
                "error_code": result.error_code,
            },
        )

    return app


def _decode_document(environment: Mapping[str, str], name: str) -> bytes:
    value = environment.get(name)
    if value is None or len(value) > 3_000_000:
        raise RuntimeError(f"missing or oversized service configuration: {name}")
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise RuntimeError(f"invalid service configuration: {name}") from None


def build_app_from_environment(
    environment: Mapping[str, str] | None = None,
) -> FastAPI:
    """Fail closed while constructing the Hugging Face process boundary."""

    values = os.environ if environment is None else environment
    token = values.get("NORMGUARD_BEARER_TOKEN", "")
    settings = ServiceSettings(bearer_token=token)
    documents = PolicyDocuments(
        policy_bundle=_decode_document(values, "NORMGUARD_POLICY_B64"),
        trust_store=_decode_document(values, "NORMGUARD_TRUST_STORE_B64"),
        activation_state=_decode_document(values, "NORMGUARD_ACTIVATION_STATE_B64"),
    )
    service = PrivatePilotService(
        documents,
        verification_time=datetime.now(UTC),
        runtime_limits=RuntimeLimits(max_input_bytes=65_536),
    )
    return create_app(service, settings)
