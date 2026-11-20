import hashlib
import unittest
from datetime import UTC, datetime, timedelta

from normguard.hosted_audit import (
    AuditPseudonymizer,
    HostedAuditEvent,
    HostedAuditRecorder,
    InMemoryHostedAuditSink,
    PublicErrorCode,
)
from normguard.hosted_authorization import (
    DenyByDefaultAuthorizer,
    HostedAction,
    InMemoryTenantGrantRepository,
    PrincipalKind,
    ResourceType,
    TenantGrant,
    VerifiedPrincipal,
)
from normguard.hosted_runtime import (
    BoundedHostedRuntime,
    HostedNormaliseCommand,
    HostedRuntimeStatus,
    InMemoryTenantRuntimeRegistry,
    TenantNormalisationResult,
)
from normguard.tenant_policy_repository import (
    InMemoryTenantPolicyRepository,
    TenantActivation,
    TenantPolicyArtifact,
    TenantPolicyIdentity,
)
from normguard.tenant_quotas import InMemoryTenantQuotaManager, QuotaProfile

NOW = datetime(2026, 11, 20, 12, tzinfo=UTC)


class Normaliser:
    def __init__(self, *, review_required: bool = False, fail_once: bool = False) -> None:
        self.review_required = review_required
        self.fail_once = fail_once
        self.calls = 0

    def normalise(self, text: str) -> TenantNormalisationResult:
        self.calls += 1
        if self.fail_once and self.calls == 1:
            raise RuntimeError("sensitive dependency detail")
        return TenantNormalisationResult(text.lower(), self.review_required)


class BrokenSink:
    def emit(self, _event: HostedAuditEvent) -> None:
        raise OSError("audit backend unavailable")


class HostedRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.principal = VerifiedPrincipal(
            "principal-1",
            "issuer-1",
            "subject-1",
            "audience-1",
            PrincipalKind.SERVICE,
            NOW - timedelta(minutes=1),
            NOW + timedelta(hours=1),
        )
        self.grant = TenantGrant(
            "grant-1",
            self.principal.principal_id,
            "tenant-a",
            frozenset({HostedAction.NORMALISE}),
            frozenset({ResourceType.NORMALISATION}),
            NOW - timedelta(minutes=1),
            NOW + timedelta(hours=1),
        )
        bundle = b"signed-policy-artifact"
        self.identity = TenantPolicyIdentity(
            "tenant-a",
            "retail-en",
            "1.0.0",
            "a" * 64,
            hashlib.sha256(bundle).hexdigest(),
        )
        self.artifact = TenantPolicyArtifact(self.identity, bundle)
        self.activation = TenantActivation(
            "tenant-a", "retail-en", self.identity, "1.0.0", 1, "change-1", NOW
        )
        self.sink = InMemoryHostedAuditSink()

    def command(self, *, tenant: str = "tenant-a", text: str = "SKU-42 ACME"):
        return HostedNormaliseCommand(
            self.principal, tenant, text, "correlation-1", "request-1"
        )

    def runtime(
        self,
        normaliser,
        *,
        sink=None,
        max_input_bytes: int = 100,
        grants=None,
        activations=None,
        artifacts=None,
    ) -> BoundedHostedRuntime:
        profile = QuotaProfile(20, 1, max_input_bytes, 1)
        return BoundedHostedRuntime(
            policy_id="retail-en",
            authorizer=DenyByDefaultAuthorizer(
                InMemoryTenantGrantRepository(grants or (self.grant,))
            ),
            policy_repository=InMemoryTenantPolicyRepository(
                artifacts if artifacts is not None else (self.artifact,),
                activations if activations is not None else (self.activation,),
            ),
            quota_manager=InMemoryTenantQuotaManager(profile),
            runtime_registry=InMemoryTenantRuntimeRegistry(
                () if normaliser is None else ((self.identity, normaliser),)
            ),
            audit_recorder=HostedAuditRecorder(sink or self.sink),
            audit_pseudonymizer=AuditPseudonymizer(b"k" * 32, domain="prod"),
            event_id_factory=lambda: "event-1",
            clock=lambda: NOW,
        )

    def test_success_uses_exact_active_runtime_and_writes_private_audit(self) -> None:
        normaliser = Normaliser()
        response = self.runtime(normaliser).normalise(self.command())

        self.assertEqual(response.status, HostedRuntimeStatus.ACCEPTED)
        self.assertEqual(response.output_text, "sku-42 acme")
        self.assertIsNone(response.error)
        self.assertEqual(normaliser.calls, 1)
        self.assertEqual(len(self.sink.events), 1)
        event = self.sink.events[0]
        self.assertEqual(event.artifact_sha256, self.identity.artifact_sha256)
        self.assertNotIn("tenant-a", repr(event.to_dict()))
        self.assertNotIn("SKU-42", repr(event.to_dict()))

    def test_review_required_is_distinct_from_failure(self) -> None:
        response = self.runtime(Normaliser(review_required=True)).normalise(
            self.command()
        )
        self.assertEqual(response.status, HostedRuntimeStatus.REVIEW_REQUIRED)
        self.assertEqual(response.output_text, "sku-42 acme")

    def test_cross_tenant_request_is_denied_before_runtime(self) -> None:
        normaliser = Normaliser()
        response = self.runtime(normaliser).normalise(self.command(tenant="tenant-b"))
        self.assertEqual(response.status, HostedRuntimeStatus.REJECTED)
        self.assertEqual(response.error.code, PublicErrorCode.NOT_AUTHORIZED)
        self.assertEqual(normaliser.calls, 0)
        self.assertEqual(self.sink.events, [])

    def test_quota_rejection_and_missing_runtime_are_closed_errors(self) -> None:
        oversized = self.runtime(Normaliser(), max_input_bytes=3).normalise(
            self.command()
        )
        self.assertEqual(oversized.error.code, PublicErrorCode.INVALID_REQUEST)

        unavailable = self.runtime(None).normalise(self.command())
        self.assertEqual(unavailable.error.code, PublicErrorCode.UNAVAILABLE)
        self.assertNotIn("retail-en", repr(unavailable))
        self.assertNotIn("tenant-a", repr(unavailable))

    def test_runtime_failure_releases_lease_for_next_request(self) -> None:
        normaliser = Normaliser(fail_once=True)
        runtime = self.runtime(normaliser)
        failed = runtime.normalise(self.command())
        succeeded = runtime.normalise(self.command(text="SECOND"))

        self.assertEqual(failed.error.code, PublicErrorCode.INTERNAL_FAILURE)
        self.assertEqual(succeeded.status, HostedRuntimeStatus.ACCEPTED)
        self.assertEqual(succeeded.output_text, "second")

    def test_best_effort_audit_failure_does_not_change_success(self) -> None:
        response = self.runtime(Normaliser(), sink=BrokenSink()).normalise(self.command())
        self.assertEqual(response.status, HostedRuntimeStatus.ACCEPTED)


if __name__ == "__main__":
    unittest.main()
