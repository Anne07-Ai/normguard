import hashlib
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from normguard.hosted_authorization import (
    AuthorizationCode,
    AuthorizationRequest,
    AuthorizedContext,
    DenyByDefaultAuthorizer,
    HostedAction,
    PrincipalKind,
    ResourceType,
    VerifiedPrincipal,
)
from normguard.postgres_repositories import (
    PostgresTenantGrantRepository,
    PostgresTenantPolicyRepository,
)
from normguard.tenant_policy_repository import (
    RepositoryCode,
    TenantActivation,
    TenantPolicyIdentity,
)

NOW = datetime(2026, 11, 20, 17, tzinfo=UTC)


class RecordingCursor:
    def __init__(self, *, one=(), all_rows=()) -> None:
        self.one = list(one)
        self.all_rows = list(all_rows)
        self.executions: list[tuple[str, tuple[object, ...]]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def execute(self, query: str, parameters=()):
        self.executions.append((query, tuple(parameters)))

    def fetchone(self):
        return self.one.pop(0) if self.one else None

    def fetchall(self):
        return tuple(self.all_rows)


class RecordingConnection:
    def __init__(self, cursor: RecordingCursor) -> None:
        self._cursor = cursor
        self.exited = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.exited = True

    def cursor(self):
        return self._cursor


def context(
    *,
    tenant: str = "tenant-a",
    action: HostedAction = HostedAction.NORMALISE,
    resource: ResourceType = ResourceType.NORMALISATION,
) -> AuthorizedContext:
    return AuthorizedContext(
        "principal-1", action=action, tenant_id=tenant, resource_type=resource,
        resource_id="request-1", grant_ids=("grant-1",)
    )


def identity(tenant: str = "tenant-a", version: str = "1.0.0") -> TenantPolicyIdentity:
    bundle = f"bundle-{tenant}-{version}".encode()
    return TenantPolicyIdentity(
        tenant,
        "retail-en",
        version,
        hashlib.sha256(f"payload-{tenant}-{version}".encode()).hexdigest(),
        hashlib.sha256(bundle).hexdigest(),
    )


def activation_row(
    policy_identity: TenantPolicyIdentity,
    *,
    minimum_version: str = "1.0.0",
    revision: int = 1,
):
    return (
        policy_identity.policy_version,
        policy_identity.payload_sha256,
        policy_identity.artifact_sha256,
        minimum_version,
        revision,
        "change-1",
        NOW,
    )


class PostgresRepositoryTests(unittest.TestCase):
    def test_grant_query_is_principal_scoped_parameterized_and_time_bounded(self) -> None:
        row = (
            "grant-1",
            "principal-1",
            "tenant-a",
            ["normalise"],
            ["normalisation"],
            NOW - timedelta(minutes=1),
            NOW + timedelta(minutes=5),
        )
        cursor = RecordingCursor(all_rows=(row,))
        connection = RecordingConnection(cursor)
        repository = PostgresTenantGrantRepository(lambda: connection)

        grants = repository.find_grants("principal-1", at=NOW)

        self.assertEqual(grants[0].tenant_id, "tenant-a")
        self.assertEqual(len(cursor.executions), 2)
        set_query, set_parameters = cursor.executions[0]
        query, parameters = cursor.executions[1]
        self.assertIn("set_config('normguard.principal_id', %s, true)", set_query)
        self.assertEqual(set_parameters, ("principal-1",))
        self.assertIn("WHERE principal_id = %s", query)
        self.assertIn("active_from <= %s", query)
        self.assertIn("expires_at > %s", query)
        self.assertEqual(parameters, ("principal-1", NOW, NOW))
        self.assertNotIn("principal-1", query)
        self.assertTrue(connection.exited)

    def test_mismatched_grant_row_fails_closed_through_authorizer(self) -> None:
        row = (
            "grant-1",
            "different-principal",
            "tenant-a",
            ["normalise"],
            ["normalisation"],
            NOW - timedelta(minutes=1),
            NOW + timedelta(minutes=5),
        )
        repository = PostgresTenantGrantRepository(
            lambda: RecordingConnection(RecordingCursor(all_rows=(row,)))
        )
        principal = VerifiedPrincipal(
            "principal-1", "issuer-1", "subject-1", "audience-1",
            PrincipalKind.SERVICE, NOW - timedelta(minutes=1), NOW + timedelta(minutes=5)
        )
        decision = DenyByDefaultAuthorizer(repository).authorize(
            principal,
            AuthorizationRequest(
                HostedAction.NORMALISE,
                ResourceType.NORMALISATION,
                requested_tenant_id="tenant-a",
            ),
            at=NOW,
        )
        self.assertEqual(decision.code, AuthorizationCode.DEPENDENCY_FAILURE)

    def test_policy_read_requires_exact_tenant_and_immutable_identity(self) -> None:
        policy_identity = identity()
        bundle = b"bundle-tenant-a-1.0.0"
        cursor = RecordingCursor(one=((memoryview(bundle),),))
        connection = RecordingConnection(cursor)
        repository = PostgresTenantPolicyRepository(lambda: connection)

        result = repository.read_policy(context(), policy_identity)
        self.assertEqual(result.code, RepositoryCode.SUCCESS)
        self.assertEqual(result.artifact.bundle, bundle)
        self.assertEqual(cursor.executions[0][1], ("tenant-a",))
        query, parameters = cursor.executions[1]
        self.assertIn("tenant_id = %s", query)
        self.assertEqual(parameters[0], "tenant-a")
        self.assertNotIn("tenant-a", query)

        cross_tenant = repository.read_policy(context(), identity("tenant-b"))
        self.assertEqual(cross_tenant.code, RepositoryCode.IDENTITY_MISMATCH)
        self.assertEqual(len(cursor.executions), 2)

    def test_activation_read_is_tenant_scoped_and_non_enumerating(self) -> None:
        policy_identity = identity()
        cursor = RecordingCursor(one=(activation_row(policy_identity),))
        repository = PostgresTenantPolicyRepository(
            lambda: RecordingConnection(cursor)
        )
        result = repository.read_activation(context(), "retail-en")
        self.assertEqual(result.code, RepositoryCode.SUCCESS)
        self.assertEqual(result.activation.active_identity, policy_identity)
        query, parameters = cursor.executions[1]
        self.assertIn("tenant_id = %s AND policy_id = %s", query)
        self.assertEqual(parameters, ("tenant-a", "retail-en"))

        missing_cursor = RecordingCursor(one=(None,))
        missing = PostgresTenantPolicyRepository(
            lambda: RecordingConnection(missing_cursor)
        ).read_activation(context(), "retail-en")
        self.assertEqual(missing.code, RepositoryCode.NOT_FOUND)

    def test_activation_cas_uses_lock_revision_predicate_and_tenant_context(self) -> None:
        policy_identity = identity(version="1.1.0")
        current_identity = identity(version="1.0.0")
        candidate = TenantActivation(
            "tenant-a", "retail-en", policy_identity, "1.0.0", 99, "change-2", NOW
        )
        cursor = RecordingCursor(
            one=((1,), activation_row(current_identity), (2,))
        )
        repository = PostgresTenantPolicyRepository(
            lambda: RecordingConnection(cursor)
        )
        result = repository.compare_and_swap_activation(
            context(
                action=HostedAction.ACTIVATE_POLICY,
                resource=ResourceType.POLICY,
            ),
            candidate,
            expected_revision=1,
        )
        self.assertEqual(result.code, RepositoryCode.SUCCESS)
        self.assertEqual(result.activation.revision, 2)
        self.assertIn("FOR UPDATE", cursor.executions[2][0])
        update_query, parameters = cursor.executions[3]
        self.assertIn("tenant_id = %s", update_query)
        self.assertIn("revision = %s", update_query)
        self.assertEqual(parameters[-3:], ("tenant-a", "retail-en", 1))

    def test_stale_revision_and_rollback_are_rejected_before_mutation(self) -> None:
        policy_identity = identity(version="1.1.0")
        current_identity = identity(version="1.0.0")
        candidate = TenantActivation(
            "tenant-a", "retail-en", policy_identity, "0.9.0", 8, "change-2", NOW
        )
        stale_cursor = RecordingCursor(
            one=((1,), activation_row(current_identity, revision=1))
        )
        repository = PostgresTenantPolicyRepository(
            lambda: RecordingConnection(stale_cursor)
        )
        privileged = context(
            action=HostedAction.ACTIVATE_POLICY, resource=ResourceType.POLICY
        )
        stale = repository.compare_and_swap_activation(
            privileged, candidate, expected_revision=0
        )
        self.assertEqual(stale.code, RepositoryCode.STALE_REVISION)
        self.assertEqual(len(stale_cursor.executions), 3)

        rollback_cursor = RecordingCursor(
            one=(
                (1,),
                activation_row(current_identity, minimum_version="1.0.0", revision=1),
            )
        )
        rollback = PostgresTenantPolicyRepository(
            lambda: RecordingConnection(rollback_cursor)
        ).compare_and_swap_activation(privileged, candidate, expected_revision=1)
        self.assertEqual(rollback.code, RepositoryCode.ROLLBACK_REJECTED)
        self.assertEqual(len(rollback_cursor.executions), 3)

    def test_migration_forces_rls_immutability_and_public_restrictions(self) -> None:
        root = Path(__file__).resolve().parents[1]
        migration = (
            root / "src/normguard/sql/001_hosted_tenant_state.sql"
        ).read_text()
        self.assertEqual(migration.count("FORCE ROW LEVEL SECURITY"), 3)
        self.assertIn("current_setting('normguard.principal_id', true)", migration)
        self.assertGreaterEqual(
            migration.count("current_setting('normguard.tenant_id', true)"), 4
        )
        self.assertIn("normguard_policy_artifacts_immutable", migration)
        self.assertIn("ON UPDATE RESTRICT ON DELETE RESTRICT", migration)
        self.assertEqual(migration.count("REVOKE ALL"), 3)


if __name__ == "__main__":
    unittest.main()
