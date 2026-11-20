import unittest
from collections.abc import Callable

from normguard.hosted_authorization import (
    AuthorizedContext,
    HostedAction,
    ResourceType,
)
from normguard.tenant_quotas import (
    InMemoryTenantQuotaManager,
    QuotaCode,
    QuotaDecision,
    QuotaProfile,
    QuotaRequest,
)


class Clock:
    def __init__(self) -> None:
        self.now = 10.0

    def __call__(self) -> float:
        return self.now


def context(tenant: str = "tenant-a", principal: str = "principal-1") -> AuthorizedContext:
    return AuthorizedContext(
        principal,
        tenant,
        HostedAction.NORMALISE,
        ResourceType.NORMALISATION,
        "request",
        ("grant-1",),
    )


def profile(rate: int = 4, concurrency: int = 2) -> QuotaProfile:
    return QuotaProfile(rate, concurrency, 1024, 4)


class TenantQuotaTests(unittest.TestCase):
    def test_strictest_deployment_tenant_and_principal_limits_win(self) -> None:
        manager = InMemoryTenantQuotaManager(
            profile(rate=10, concurrency=5),
            tenant_profiles={"tenant-a": profile(rate=3, concurrency=4)},
            principal_profiles={"principal-1": profile(rate=2, concurrency=1)},
        )
        first = manager.acquire(QuotaRequest(context(), 10))
        second = manager.acquire(QuotaRequest(context(), 10))

        self.assertTrue(first.allowed)
        self.assertEqual(second.code, QuotaCode.CONCURRENCY_LIMIT)
        first.lease.release()
        self.assertTrue(manager.acquire(QuotaRequest(context(), 10)).allowed)

    def test_one_tenant_cannot_consume_another_tenants_quota(self) -> None:
        manager = InMemoryTenantQuotaManager(
            profile(rate=10, concurrency=2),
            tenant_profiles={
                "tenant-a": profile(rate=10, concurrency=1),
                "tenant-b": profile(rate=10, concurrency=1),
            },
        )
        tenant_a = manager.acquire(
            QuotaRequest(context("tenant-a", "principal-a"), 10)
        )
        tenant_b = manager.acquire(
            QuotaRequest(context("tenant-b", "principal-b"), 10)
        )

        self.assertTrue(tenant_a.allowed)
        self.assertTrue(tenant_b.allowed)
        tenant_a.lease.release()
        tenant_b.lease.release()

    def test_principal_rotation_cannot_bypass_tenant_rate_limit(self) -> None:
        clock = Clock()
        manager = InMemoryTenantQuotaManager(
            profile(rate=10),
            tenant_profiles={"tenant-a": profile(rate=1)},
            clock=clock,
        )
        first = manager.acquire(QuotaRequest(context(principal="principal-1"), 10))
        first.lease.release()
        rotated = manager.acquire(QuotaRequest(context(principal="principal-2"), 10))

        self.assertEqual(rotated.code, QuotaCode.RATE_LIMIT)
        self.assertEqual(rotated.retry_after_seconds, 60)

    def test_rejected_admission_consumes_no_partial_scope_counters(self) -> None:
        manager = InMemoryTenantQuotaManager(
            profile(rate=10, concurrency=2),
            principal_profiles={"blocked": profile(rate=1, concurrency=1)},
        )
        held = manager.acquire(QuotaRequest(context(principal="blocked"), 10))
        rejected = manager.acquire(QuotaRequest(context(principal="blocked"), 10))
        other = manager.acquire(QuotaRequest(context(principal="allowed"), 10))

        self.assertTrue(held.allowed)
        self.assertEqual(rejected.code, QuotaCode.RATE_LIMIT)
        self.assertTrue(other.allowed)
        held.lease.release()
        other.lease.release()

    def test_payload_and_batch_rejection_happens_before_work(self) -> None:
        manager = InMemoryTenantQuotaManager(profile())
        called = False

        def invoke_if_allowed(
            decision: QuotaDecision, operation: Callable[[], None]
        ) -> None:
            if decision.allowed:
                operation()

        def downstream() -> None:
            nonlocal called
            called = True

        too_large = manager.acquire(QuotaRequest(context(), 1025))
        too_many = manager.acquire(QuotaRequest(context(), 10, batch_size=5))
        invoke_if_allowed(too_large, downstream)
        invoke_if_allowed(too_many, downstream)

        self.assertEqual(too_large.code, QuotaCode.INPUT_LIMIT)
        self.assertEqual(too_many.code, QuotaCode.BATCH_LIMIT)
        self.assertFalse(called)

    def test_release_is_idempotent_and_restores_concurrency(self) -> None:
        manager = InMemoryTenantQuotaManager(profile(concurrency=1))
        first = manager.acquire(QuotaRequest(context(), 10))
        first.lease.release()
        first.lease.release()
        second = manager.acquire(QuotaRequest(context(), 10))
        self.assertTrue(second.allowed)
        second.lease.release()

    def test_window_reset_is_deterministic(self) -> None:
        clock = Clock()
        manager = InMemoryTenantQuotaManager(profile(rate=1), clock=clock)
        first = manager.acquire(QuotaRequest(context(), 10))
        first.lease.release()
        self.assertEqual(
            manager.acquire(QuotaRequest(context(), 10)).code,
            QuotaCode.RATE_LIMIT,
        )
        clock.now += 60
        reset = manager.acquire(QuotaRequest(context(), 10))
        self.assertTrue(reset.allowed)
        reset.lease.release()

    def test_decision_evidence_contains_no_identity_or_tenant(self) -> None:
        manager = InMemoryTenantQuotaManager(profile(rate=1))
        first = manager.acquire(QuotaRequest(context(), 10))
        first.lease.release()
        denied = manager.acquire(QuotaRequest(context(), 10))
        evidence = denied.evidence()
        self.assertEqual(evidence["code"], "rate_limit")
        self.assertNotIn("tenant", repr(evidence))
        self.assertNotIn("principal", repr(evidence))


if __name__ == "__main__":
    unittest.main()
