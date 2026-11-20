import unittest
from datetime import UTC, datetime, timedelta

from normguard.hosted_authorization import (
    AuthorizationCode,
    AuthorizationRequest,
    DenyByDefaultAuthorizer,
    HostedAction,
    InMemoryTenantGrantRepository,
    PrincipalKind,
    ResourceType,
    TenantGrant,
    VerifiedPrincipal,
)

NOW = datetime(2026, 11, 20, 12, tzinfo=UTC)


def principal(
    principal_id: str = "principal-1",
    *,
    kind: PrincipalKind = PrincipalKind.HUMAN,
    expires_at: datetime | None = None,
) -> VerifiedPrincipal:
    return VerifiedPrincipal(
        principal_id=principal_id,
        issuer="https://identity.example",
        subject=principal_id,
        audience="normguard-production",
        kind=kind,
        authenticated_at=NOW - timedelta(minutes=1),
        expires_at=expires_at or NOW + timedelta(minutes=10),
    )


def grant(
    tenant_id: str = "tenant-a",
    *,
    principal_id: str = "principal-1",
    grant_id: str = "grant-1",
    actions: frozenset[HostedAction] = frozenset({HostedAction.NORMALISE}),
    resources: frozenset[ResourceType] = frozenset({ResourceType.NORMALISATION}),
) -> TenantGrant:
    return TenantGrant(
        grant_id=grant_id,
        principal_id=principal_id,
        tenant_id=tenant_id,
        actions=actions,
        resource_types=resources,
        active_from=NOW - timedelta(days=1),
        expires_at=NOW + timedelta(days=1),
    )


def request(tenant: str | None = "tenant-a") -> AuthorizationRequest:
    return AuthorizationRequest(
        HostedAction.NORMALISE,
        ResourceType.NORMALISATION,
        resource_id="normalise-request",
        requested_tenant_id=tenant,
    )


class HostedAuthorizationTests(unittest.TestCase):
    def test_authorized_context_is_derived_from_matching_authoritative_grant(self) -> None:
        authorizer = DenyByDefaultAuthorizer(InMemoryTenantGrantRepository((grant(),)))

        decision = authorizer.authorize(principal(), request(), at=NOW)

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.code, AuthorizationCode.ALLOW)
        self.assertEqual(decision.context.tenant_id, "tenant-a")
        self.assertEqual(decision.context.grant_ids, ("grant-1",))

    def test_client_tenant_label_cannot_create_authority(self) -> None:
        authorizer = DenyByDefaultAuthorizer(InMemoryTenantGrantRepository((grant(),)))

        decision = authorizer.authorize(principal(), request("tenant-b"), at=NOW)

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.code, AuthorizationCode.TENANT_CONFLICT)
        self.assertIsNone(decision.context)

    def test_unknown_action_and_resource_are_denied(self) -> None:
        authorizer = DenyByDefaultAuthorizer(InMemoryTenantGrantRepository((grant(),)))
        attempts = (
            AuthorizationRequest(HostedAction.ACTIVATE_POLICY, ResourceType.POLICY, "policy-1"),
            AuthorizationRequest(HostedAction.NORMALISE, ResourceType.POLICY, "policy-1"),
        )
        for attempt in attempts:
            with self.subTest(attempt=attempt):
                decision = authorizer.authorize(principal(), attempt, at=NOW)
                self.assertEqual(decision.code, AuthorizationCode.NOT_AUTHORIZED)

    def test_expired_principal_and_inactive_grant_deny(self) -> None:
        expired = principal(expires_at=NOW)
        active_authorizer = DenyByDefaultAuthorizer(
            InMemoryTenantGrantRepository((grant(),))
        )
        self.assertEqual(
            active_authorizer.authorize(expired, request(), at=NOW).code,
            AuthorizationCode.CREDENTIAL_EXPIRED,
        )

        inactive_authorizer = DenyByDefaultAuthorizer(
            InMemoryTenantGrantRepository(
                (
                    TenantGrant(
                        "old-grant",
                        "principal-1",
                        "tenant-a",
                        frozenset({HostedAction.NORMALISE}),
                        frozenset({ResourceType.NORMALISATION}),
                        NOW - timedelta(days=2),
                        NOW - timedelta(days=1),
                    ),
                )
            )
        )
        self.assertEqual(
            inactive_authorizer.authorize(principal(), request(), at=NOW).code,
            AuthorizationCode.NOT_AUTHORIZED,
        )

    def test_multiple_authorized_tenants_require_explicit_scope(self) -> None:
        authorizer = DenyByDefaultAuthorizer(
            InMemoryTenantGrantRepository(
                (grant(), grant("tenant-b", grant_id="grant-2"))
            )
        )
        self.assertEqual(
            authorizer.authorize(principal(), request(None), at=NOW).code,
            AuthorizationCode.AMBIGUOUS_TENANT,
        )
        scoped = authorizer.authorize(principal(), request("tenant-b"), at=NOW)
        self.assertTrue(scoped.allowed)
        self.assertEqual(scoped.context.tenant_id, "tenant-b")
        self.assertEqual(scoped.context.grant_ids, ("grant-2",))

    def test_service_account_has_no_broad_implicit_tenant_access(self) -> None:
        service = principal("service-1", kind=PrincipalKind.SERVICE)
        authorizer = DenyByDefaultAuthorizer(
            InMemoryTenantGrantRepository(
                (grant(principal_id="service-1", tenant_id="tenant-a"),)
            )
        )
        decision = authorizer.authorize(service, request("tenant-b"), at=NOW)
        self.assertEqual(decision.code, AuthorizationCode.TENANT_CONFLICT)

    def test_repository_failure_denies_without_leaking_details(self) -> None:
        class BrokenRepository:
            def find_grants(self, principal_id: str, *, at: datetime):
                raise RuntimeError(f"database failed for {principal_id} at {at}")

        decision = DenyByDefaultAuthorizer(BrokenRepository()).authorize(
            principal(), request(), at=NOW
        )
        self.assertEqual(decision.code, AuthorizationCode.DEPENDENCY_FAILURE)
        self.assertEqual(
            decision.evidence(),
            {
                "schema_version": 1,
                "allowed": False,
                "code": "dependency_failure",
                "action": None,
                "resource_type": None,
            },
        )
        self.assertNotIn("principal", repr(decision.evidence()))
        self.assertNotIn("tenant", repr(decision.evidence()))

    def test_contracts_reject_unbounded_or_naive_identity_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "bounded identifier"):
            principal("bad principal")
        with self.assertRaisesRegex(ValueError, "timezone aware"):
            VerifiedPrincipal(
                "principal-1",
                "issuer",
                "subject",
                "audience",
                PrincipalKind.HUMAN,
                datetime(2026, 11, 20),
                datetime(2026, 11, 21),
            )
        with self.assertRaisesRegex(ValueError, "action must be supported"):
            AuthorizationRequest("unknown", ResourceType.POLICY)  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "unsupported action"):
            TenantGrant(
                "grant-1",
                "principal-1",
                "tenant-a",
                frozenset({"normalise"}),  # type: ignore[arg-type]
                frozenset({ResourceType.NORMALISATION}),
                NOW - timedelta(minutes=1),
                NOW + timedelta(minutes=1),
            )


if __name__ == "__main__":
    unittest.main()
