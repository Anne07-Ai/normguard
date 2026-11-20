"""Redis-backed atomic quota admission for multi-task hosted deployments."""

from __future__ import annotations

import hashlib
import hmac
import math
import uuid
from collections.abc import Callable, Mapping, Sequence
from typing import Protocol

from .tenant_quotas import (
    QuotaCode,
    QuotaDecision,
    QuotaLease,
    QuotaProfile,
    QuotaRequest,
)

REDIS_QUOTA_ACQUIRE_SCRIPT = r"""
local clock = redis.call('TIME')
local now_ms = (tonumber(clock[1]) * 1000) + math.floor(tonumber(clock[2]) / 1000)
local token = ARGV[1]
local window_ms = tonumber(ARGV[2])
local lease_ms = tonumber(ARGV[3])
local retry_ms = 0
local concurrency_blocked = false

for scope = 1, 3 do
    local rate_key = KEYS[((scope - 1) * 2) + 1]
    local concurrency_key = KEYS[((scope - 1) * 2) + 2]
    local request_limit = tonumber(ARGV[4 + ((scope - 1) * 2)])
    local concurrency_limit = tonumber(ARGV[5 + ((scope - 1) * 2)])
    redis.call('ZREMRANGEBYSCORE', concurrency_key, '-inf', now_ms)
    if redis.call('ZSCORE', concurrency_key, token) then
        return {0, 'lease_collision', 0}
    end
    local current_rate = tonumber(redis.call('GET', rate_key) or '0')
    if current_rate >= request_limit then
        local ttl = redis.call('PTTL', rate_key)
        if ttl < 1 then ttl = window_ms end
        if ttl > retry_ms then retry_ms = ttl end
    end
    if tonumber(redis.call('ZCARD', concurrency_key)) >= concurrency_limit then
        concurrency_blocked = true
    end
end

if retry_ms > 0 then return {0, 'rate_limit', retry_ms} end
if concurrency_blocked then return {0, 'concurrency_limit', 0} end

for scope = 1, 3 do
    local rate_key = KEYS[((scope - 1) * 2) + 1]
    local concurrency_key = KEYS[((scope - 1) * 2) + 2]
    local count = redis.call('INCR', rate_key)
    if count == 1 then redis.call('PEXPIRE', rate_key, window_ms) end
    redis.call('ZADD', concurrency_key, now_ms + lease_ms, token)
    redis.call('PEXPIRE', concurrency_key, lease_ms)
end
return {1, 'allow', 0}
""".strip()

REDIS_QUOTA_RELEASE_SCRIPT = r"""
local token = ARGV[1]
local removed = 0
for index = 1, #KEYS do
    removed = removed + redis.call('ZREM', KEYS[index], token)
end
return removed
""".strip()

REDIS_QUOTA_ACQUIRE_SHA1 = hashlib.sha1(
    REDIS_QUOTA_ACQUIRE_SCRIPT.encode()
).hexdigest()
REDIS_QUOTA_RELEASE_SHA1 = hashlib.sha1(
    REDIS_QUOTA_RELEASE_SCRIPT.encode()
).hexdigest()
REDIS_QUOTA_SCRIPT_SHA256 = hashlib.sha256(
    (REDIS_QUOTA_ACQUIRE_SCRIPT + "\n" + REDIS_QUOTA_RELEASE_SCRIPT).encode()
).hexdigest()


class RedisScriptClient(Protocol):
    def evalsha(
        self, digest: str, number_of_keys: int, *values: object
    ) -> object: ...


def _reference(key: bytes, domain: str, value: str) -> str:
    payload = f"normguard-quota\x00{domain}\x00{value}".encode()
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def _text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("ascii")
    if isinstance(value, str):
        return value
    raise ValueError("Redis quota response code is invalid")


class RedisTenantQuotaManager:
    """Atomic deployment, tenant and principal admission across service tasks."""

    def __init__(
        self,
        client: RedisScriptClient,
        deployment_profile: QuotaProfile,
        *,
        pseudonym_key: bytes,
        tenant_profiles: Mapping[str, QuotaProfile] | None = None,
        principal_profiles: Mapping[str, QuotaProfile] | None = None,
        window_seconds: int = 60,
        lease_seconds: int = 30,
        lease_token_factory: Callable[[], str] = lambda: uuid.uuid4().hex,
    ) -> None:
        if not isinstance(pseudonym_key, bytes) or len(pseudonym_key) < 32:
            raise ValueError("pseudonym_key must contain at least 32 bytes")
        for name, value in (
            ("window_seconds", window_seconds),
            ("lease_seconds", lease_seconds),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 3600:
                raise ValueError(f"{name} must be an integer in [1, 3600]")
        self._client = client
        self._deployment_profile = deployment_profile
        self._tenant_profiles = dict(tenant_profiles or {})
        self._principal_profiles = dict(principal_profiles or {})
        self._pseudonym_key = pseudonym_key
        self._window_ms = window_seconds * 1000
        self._lease_ms = lease_seconds * 1000
        self._lease_token_factory = lease_token_factory

    def _scope_profiles(
        self, request: QuotaRequest
    ) -> tuple[tuple[str, str, QuotaProfile], ...]:
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
            ("deployment", "all", self._deployment_profile),
            ("tenant", request.context.tenant_id, tenant_profile),
            ("principal", request.context.principal_id, principal_profile),
        )

    def _keys(
        self, scopes: tuple[tuple[str, str, QuotaProfile], ...]
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        rate_keys = []
        concurrency_keys = []
        for domain, value, _profile in scopes:
            reference = _reference(self._pseudonym_key, domain, value)
            prefix = f"{{normguard-quota}}:{domain}:{reference}"
            rate_keys.append(f"{prefix}:rate")
            concurrency_keys.append(f"{prefix}:concurrency")
        interleaved = tuple(
            item
            for pair in zip(rate_keys, concurrency_keys, strict=True)
            for item in pair
        )
        return interleaved, tuple(concurrency_keys)

    def acquire(self, request: QuotaRequest) -> QuotaDecision:
        scopes = self._scope_profiles(request)
        strictest = QuotaProfile.strictest(*(profile for _, _, profile in scopes))
        if request.input_bytes > strictest.max_input_bytes:
            return QuotaDecision(False, QuotaCode.INPUT_LIMIT)
        if request.batch_size > strictest.max_batch_size:
            return QuotaDecision(False, QuotaCode.BATCH_LIMIT)

        token = self._lease_token_factory()
        if (
            not isinstance(token, str)
            or len(token) != 32
            or any(character not in "0123456789abcdef" for character in token)
        ):
            raise ValueError("lease token factory returned an invalid token")
        keys, concurrency_keys = self._keys(scopes)
        limits = tuple(
            value
            for _domain, _identifier, profile in scopes
            for value in (profile.requests_per_window, profile.max_concurrency)
        )
        response = self._client.evalsha(
            REDIS_QUOTA_ACQUIRE_SHA1,
            len(keys),
            *keys,
            token,
            self._window_ms,
            self._lease_ms,
            *limits,
        )
        if not isinstance(response, Sequence) or isinstance(response, (str, bytes)):
            raise ValueError("Redis quota response is invalid")
        if len(response) != 3:
            raise ValueError("Redis quota response is invalid")
        allowed, code_value, retry_ms = response
        if isinstance(allowed, bool) or allowed not in {0, 1}:
            raise ValueError("Redis quota allowed value is invalid")
        code = _text(code_value)
        if isinstance(retry_ms, bool) or not isinstance(retry_ms, int) or retry_ms < 0:
            raise ValueError("Redis quota retry value is invalid")
        if allowed == 1:
            if code != QuotaCode.ALLOW.value or retry_ms != 0:
                raise ValueError("Redis quota allow response is inconsistent")

            def release() -> None:
                try:
                    self._client.evalsha(
                        REDIS_QUOTA_RELEASE_SHA1,
                        len(concurrency_keys),
                        *concurrency_keys,
                        token,
                    )
                except Exception:
                    # The sorted-set lease expires using Redis server time. A failed
                    # release may reduce availability temporarily but cannot over-admit.
                    return

            return QuotaDecision(True, QuotaCode.ALLOW, QuotaLease(release))
        if code == QuotaCode.RATE_LIMIT.value:
            retry_seconds = min(3600, max(1, math.ceil(retry_ms / 1000)))
            return QuotaDecision(
                False,
                QuotaCode.RATE_LIMIT,
                retry_after_seconds=retry_seconds,
            )
        if code == QuotaCode.CONCURRENCY_LIMIT.value and retry_ms == 0:
            return QuotaDecision(False, QuotaCode.CONCURRENCY_LIMIT)
        raise ValueError("Redis quota denial response is invalid")
