import hashlib
import unittest
from datetime import UTC, datetime

from normguard.hosted_authorization import (
    AuthorizedContext,
    HostedAction,
    ResourceType,
)
from normguard.tenant_policy_repository import (
    InMemoryTenantPolicyRepository,
    RepositoryCode,
    TenantActivation,
    TenantPolicyArtifact,
    TenantPolicyIdentity,
)

NOW = datetime(2026, 11, 20, 13, tzinfo=UTC)


def identity(
    tenant_id: str = "tenant-a",
    *,
    version: str = "1.0.0",
    bundle: bytes = b"signed-policy-a-v1",
) -> TenantPolicyIdentity:
    return TenantPolicyIdentity(
        tenant_id,
        "retail-en",
        version,
        hashlib.sha256(b"payload:" + bundle).hexdigest(),
        hashlib.sha256(bundle).hexdigest(),
    )


def artifact(
    tenant_id: str = "tenant-a",
    *,
    version: str = "1.0.0",
    bundle: bytes = b"signed-policy-a-v1",
) -> TenantPolicyArtifact:
    return TenantPolicyArtifact(identity(tenant_id, version=version, bundle=bundle), bundle)


def context(
    tenant_id: str = "tenant-a",
    *,
    action: HostedAction = HostedAction.READ_POLICY,
    resource_type: ResourceType = ResourceType.POLICY,
) -> AuthorizedContext:
    return AuthorizedContext(
        "principal-1",
        tenant_id,
        action,
        resource_type,
        "retail-en",
        ("grant-1",),
    )


def activation(
    policy_identity: TenantPolicyIdentity,
    *,
    minimum_version: str = "1.0.0",
    revision: int = 0,
    change_id: str = "change-1",
) -> TenantActivation:
    return TenantActivation(
        policy_identity.tenant_id,
        policy_identity.policy_id,
        policy_identity,
        minimum_version,
        revision,
        change_id,
        NOW,
    )


class TenantPolicyRepositoryTests(unittest.TestCase):
    def test_exact_tenant_and_immutable_identity_are_required_for_read(self) -> None:
        row = artifact()
        repository = InMemoryTenantPolicyRepository((row,))

        found = repository.read_policy(context(), row.identity)
        cross_tenant = repository.read_policy(context("tenant-b"), row.identity)
        wrong_digest = TenantPolicyIdentity(
            "tenant-a", "retail-en", "1.0.0", "a" * 64, "b" * 64
        )

        self.assertEqual(found.code, RepositoryCode.SUCCESS)
        self.assertEqual(found.artifact.bundle, b"signed-policy-a-v1")
        self.assertEqual(cross_tenant.code, RepositoryCode.IDENTITY_MISMATCH)
        self.assertEqual(
            repository.read_policy(context(), wrong_digest).code,
            RepositoryCode.NOT_FOUND,
        )

    def test_unauthorized_context_is_rejected_before_policy_access(self) -> None:
        row = artifact()
        repository = InMemoryTenantPolicyRepository((row,))
        grant_admin = context(
            action=HostedAction.MANAGE_GRANT,
            resource_type=ResourceType.GRANT,
        )
        self.assertEqual(
            repository.read_policy(grant_admin, row.identity).code,
            RepositoryCode.NOT_AUTHORIZED,
        )

    def test_activation_create_and_update_use_compare_and_swap_revision(self) -> None:
        first = artifact()
        second = artifact(version="1.1.0", bundle=b"signed-policy-a-v1.1")
        repository = InMemoryTenantPolicyRepository((first, second))
        activate_context = context(action=HostedAction.ACTIVATE_POLICY)

        created = repository.compare_and_swap_activation(
            activate_context,
            activation(first.identity),
            expected_revision=None,
        )
        updated = repository.compare_and_swap_activation(
            activate_context,
            activation(
                second.identity,
                minimum_version="1.1.0",
                change_id="change-2",
            ),
            expected_revision=0,
        )

        self.assertEqual(created.code, RepositoryCode.SUCCESS)
        self.assertEqual(created.activation.revision, 0)
        self.assertEqual(updated.code, RepositoryCode.SUCCESS)
        self.assertEqual(updated.activation.revision, 1)

    def test_stale_concurrent_update_is_rejected_without_mutation(self) -> None:
        first = artifact()
        second = artifact(version="1.1.0", bundle=b"signed-policy-a-v1.1")
        repository = InMemoryTenantPolicyRepository(
            (first, second), (activation(first.identity),)
        )
        activate_context = context(action=HostedAction.ACTIVATE_POLICY)

        accepted = repository.compare_and_swap_activation(
            activate_context,
            activation(second.identity, change_id="change-2"),
            expected_revision=0,
        )
        stale = repository.compare_and_swap_activation(
            activate_context,
            activation(first.identity, change_id="stale-change"),
            expected_revision=0,
        )
        current = repository.read_activation(context(), "retail-en")

        self.assertEqual(accepted.code, RepositoryCode.SUCCESS)
        self.assertEqual(stale.code, RepositoryCode.STALE_REVISION)
        self.assertEqual(current.activation.active_identity, second.identity)

    def test_activation_floor_cannot_move_backwards(self) -> None:
        first = artifact()
        second = artifact(version="2.0.0", bundle=b"signed-policy-a-v2")
        current = activation(second.identity, minimum_version="2.0.0", revision=3)
        repository = InMemoryTenantPolicyRepository((first, second), (current,))

        result = repository.compare_and_swap_activation(
            context(action=HostedAction.ACTIVATE_POLICY),
            activation(first.identity, minimum_version="1.0.0"),
            expected_revision=3,
        )
        self.assertEqual(result.code, RepositoryCode.ROLLBACK_REJECTED)

    def test_tenant_activation_state_is_isolated(self) -> None:
        tenant_a = artifact()
        tenant_b = artifact("tenant-b", bundle=b"signed-policy-b-v1")
        repository = InMemoryTenantPolicyRepository(
            (tenant_a, tenant_b),
            (activation(tenant_a.identity), activation(tenant_b.identity)),
        )

        read_a = repository.read_activation(context(), "retail-en")
        read_b = repository.read_activation(context("tenant-b"), "retail-en")

        self.assertEqual(read_a.activation.active_identity.tenant_id, "tenant-a")
        self.assertEqual(read_b.activation.active_identity.tenant_id, "tenant-b")
        self.assertNotEqual(
            read_a.activation.active_identity.artifact_sha256,
            read_b.activation.active_identity.artifact_sha256,
        )

    def test_cross_tenant_and_wrong_action_activation_are_denied(self) -> None:
        row = artifact()
        repository = InMemoryTenantPolicyRepository((row,))
        candidate = activation(row.identity)
        cross_tenant = repository.compare_and_swap_activation(
            context("tenant-b", action=HostedAction.ACTIVATE_POLICY),
            candidate,
            expected_revision=None,
        )
        wrong_action = repository.compare_and_swap_activation(
            context(action=HostedAction.READ_POLICY),
            candidate,
            expected_revision=None,
        )
        self.assertEqual(cross_tenant.code, RepositoryCode.IDENTITY_MISMATCH)
        self.assertEqual(wrong_action.code, RepositoryCode.NOT_AUTHORIZED)

    def test_policy_digest_and_activation_contracts_fail_closed(self) -> None:
        row = artifact()
        with self.assertRaisesRegex(ValueError, "artifact digest mismatch"):
            TenantPolicyArtifact(row.identity, b"substituted-policy")
        with self.assertRaisesRegex(ValueError, "below the tenant activation floor"):
            activation(row.identity, minimum_version="2.0.0")
        with self.assertRaisesRegex(ValueError, "unknown policy artifact"):
            InMemoryTenantPolicyRepository((), (activation(row.identity),))


if __name__ == "__main__":
    unittest.main()
