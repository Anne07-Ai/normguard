import unittest

from normguard.hosted_authorization import (
    AuthorizedContext,
    HostedAction,
    ResourceType,
)
from normguard.redis_quotas import (
    REDIS_QUOTA_ACQUIRE_SCRIPT,
    REDIS_QUOTA_ACQUIRE_SHA1,
    REDIS_QUOTA_RELEASE_SCRIPT,
    REDIS_QUOTA_RELEASE_SHA1,
    REDIS_QUOTA_SCRIPT_SHA256,
    RedisTenantQuotaManager,
)
from normguard.tenant_quotas import QuotaCode, QuotaProfile, QuotaRequest


class ScriptClient:
    def __init__(self, responses=()) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[object, ...]] = []

    def evalsha(self, digest: str, number_of_keys: int, *values: object):
        self.calls.append((digest, number_of_keys, *values))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def context(tenant: str = "tenant-a", principal: str = "principal-1"):
    return AuthorizedContext(
        principal,
        tenant,
        HostedAction.NORMALISE,
        ResourceType.NORMALISATION,
        "request-1",
        ("grant-1",),
    )


def manager(client: ScriptClient, **kwargs) -> RedisTenantQuotaManager:
    return RedisTenantQuotaManager(
        client,
        QuotaProfile(100, 10, 1_000, 5),
        pseudonym_key=b"k" * 32,
        lease_token_factory=lambda: "a" * 32,
        **kwargs,
    )


class RedisQuotaTests(unittest.TestCase):
    def test_atomic_admission_uses_pseudonymous_single_slot_keys_and_strict_limits(self) -> None:
        client = ScriptClient(([1, b"allow", 0],))
        quota = manager(
            client,
            tenant_profiles={"tenant-a": QuotaProfile(50, 5, 500, 3)},
            principal_profiles={"principal-1": QuotaProfile(25, 2, 250, 2)},
        )
        decision = quota.acquire(QuotaRequest(context(), 100))

        self.assertTrue(decision.allowed)
        call = client.calls[0]
        self.assertEqual(call[0], REDIS_QUOTA_ACQUIRE_SHA1)
        self.assertEqual(call[1], 6)
        keys = call[2:8]
        self.assertTrue(all(str(key).startswith("{normguard-quota}:") for key in keys))
        self.assertNotIn("tenant-a", repr(keys))
        self.assertNotIn("principal-1", repr(keys))
        self.assertEqual(call[-6:], (100, 10, 50, 5, 25, 2))

    def test_input_and_batch_limits_reject_before_redis(self) -> None:
        client = ScriptClient()
        quota = manager(
            client,
            tenant_profiles={"tenant-a": QuotaProfile(50, 5, 100, 2)},
        )
        input_denial = quota.acquire(QuotaRequest(context(), 101))
        batch_denial = quota.acquire(QuotaRequest(context(), 1, batch_size=3))
        self.assertEqual(input_denial.code, QuotaCode.INPUT_LIMIT)
        self.assertEqual(batch_denial.code, QuotaCode.BATCH_LIMIT)
        self.assertEqual(client.calls, [])

    def test_rate_and_concurrency_denials_are_bounded(self) -> None:
        client = ScriptClient(
            ([0, b"rate_limit", 1_501], [0, "concurrency_limit", 0])
        )
        quota = manager(client)
        rate = quota.acquire(QuotaRequest(context(), 1))
        concurrency = quota.acquire(QuotaRequest(context(), 1))
        self.assertEqual(rate.code, QuotaCode.RATE_LIMIT)
        self.assertEqual(rate.retry_after_seconds, 2)
        self.assertEqual(concurrency.code, QuotaCode.CONCURRENCY_LIMIT)
        self.assertIsNone(concurrency.retry_after_seconds)

    def test_lease_release_is_idempotent_and_dependency_failure_is_safe(self) -> None:
        client = ScriptClient(([1, "allow", 0], RuntimeError("redis unavailable")))
        decision = manager(client).acquire(QuotaRequest(context(), 1))
        decision.lease.release()
        decision.lease.release()
        self.assertEqual(len(client.calls), 2)
        release = client.calls[1]
        self.assertEqual(release[0], REDIS_QUOTA_RELEASE_SHA1)
        self.assertEqual(release[1], 3)
        self.assertEqual(release[-1], "a" * 32)

    def test_malformed_script_responses_and_redis_failure_fail_closed(self) -> None:
        invalid_responses = (
            None,
            [1, "allow"],
            [True, "allow", 0],
            [1, "rate_limit", 0],
            [0, "lease_collision", 0],
            [0, "rate_limit", -1],
            RuntimeError("redis endpoint secret"),
        )
        for response in invalid_responses:
            with self.subTest(response=response), self.assertRaises(
                (ValueError, RuntimeError)
            ):
                manager(ScriptClient((response,))).acquire(
                    QuotaRequest(context(), 1)
                )

    def test_principal_rotation_preserves_shared_tenant_and_deployment_keys(self) -> None:
        first_client = ScriptClient(([1, "allow", 0],))
        second_client = ScriptClient(([1, "allow", 0],))
        manager(first_client).acquire(
            QuotaRequest(context(principal="principal-1"), 1)
        )
        manager(second_client).acquire(
            QuotaRequest(context(principal="principal-2"), 1)
        )
        first_keys = first_client.calls[0][2:8]
        second_keys = second_client.calls[0][2:8]
        self.assertEqual(first_keys[:4], second_keys[:4])
        self.assertNotEqual(first_keys[4:], second_keys[4:])

    def test_scripts_are_pinned_content_free_and_use_redis_server_time(self) -> None:
        self.assertEqual(len(REDIS_QUOTA_ACQUIRE_SHA1), 40)
        self.assertEqual(len(REDIS_QUOTA_RELEASE_SHA1), 40)
        self.assertEqual(len(REDIS_QUOTA_SCRIPT_SHA256), 64)
        self.assertIn("redis.call('TIME')", REDIS_QUOTA_ACQUIRE_SCRIPT)
        self.assertIn("ZREMRANGEBYSCORE", REDIS_QUOTA_ACQUIRE_SCRIPT)
        self.assertIn("ZREM", REDIS_QUOTA_RELEASE_SCRIPT)
        scripts = REDIS_QUOTA_ACQUIRE_SCRIPT + REDIS_QUOTA_RELEASE_SCRIPT
        self.assertNotIn("tenant", scripts.lower())
        self.assertNotIn("principal", scripts.lower())
        self.assertNotIn("request_text", scripts.lower())

    def test_invalid_configuration_and_lease_token_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            RedisTenantQuotaManager(
                ScriptClient(),
                QuotaProfile(1, 1, 1, 1),
                pseudonym_key=b"short",
            )
        quota = RedisTenantQuotaManager(
            ScriptClient(),
            QuotaProfile(1, 1, 1, 1),
            pseudonym_key=b"k" * 32,
            lease_token_factory=lambda: "not-valid",
        )
        with self.assertRaises(ValueError):
            quota.acquire(QuotaRequest(context(), 1))


if __name__ == "__main__":
    unittest.main()
