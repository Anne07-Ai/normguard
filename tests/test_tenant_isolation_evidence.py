import copy
import hashlib
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

from normguard.hosted_audit import (
    AuditPseudonymizer,
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
from normguard.safety_contracts import canonical_json_bytes
from normguard.tenant_isolation_evidence import (
    ASSURANCE_LEVEL,
    IsolationDecision,
    IsolationEvidenceIdentity,
    IsolationScenario,
    IsolationScenarioResult,
    build_isolation_evidence,
    load_isolation_evidence,
    verify_isolation_evidence,
    write_isolation_evidence,
)
from normguard.tenant_policy_repository import (
    InMemoryTenantPolicyRepository,
    TenantActivation,
    TenantPolicyArtifact,
    TenantPolicyIdentity,
)
from normguard.tenant_quotas import InMemoryTenantQuotaManager, QuotaProfile

NOW = datetime(2026, 11, 20, 14, tzinfo=UTC)


class TenantNormaliser:
    def __init__(self, marker: str) -> None:
        self._marker = marker

    def normalise(self, text: str) -> TenantNormalisationResult:
        return TenantNormalisationResult(f"{self._marker}:{text.lower()}")


def evidence_identity() -> IsolationEvidenceIdentity:
    return IsolationEvidenceIdentity("a" * 40, "b" * 64, "c" * 64)


def passing_results() -> tuple[IsolationScenarioResult, ...]:
    return tuple(
        IsolationScenarioResult(scenario, True, index + 1)
        for index, scenario in enumerate(IsolationScenario)
    )


class TenantIsolationEvidenceTests(unittest.TestCase):
    def _principal(self, suffix: str) -> VerifiedPrincipal:
        return VerifiedPrincipal(
            f"principal-{suffix}",
            "issuer-1",
            f"subject-{suffix}",
            "audience-1",
            PrincipalKind.SERVICE,
            NOW - timedelta(minutes=1),
            NOW + timedelta(hours=1),
        )

    def _fixture(self):
        principals = {name: self._principal(name) for name in ("a", "b")}
        grants = tuple(
            TenantGrant(
                f"grant-{name}",
                principals[name].principal_id,
                f"tenant-{name}",
                frozenset({HostedAction.NORMALISE}),
                frozenset({ResourceType.NORMALISATION}),
                NOW - timedelta(minutes=1),
                NOW + timedelta(hours=1),
            )
            for name in ("a", "b")
        )
        artifacts = []
        activations = []
        runtimes = []
        for name in ("a", "b"):
            bundle = f"signed-policy-{name}".encode()
            identity = TenantPolicyIdentity(
                f"tenant-{name}",
                "retail-en",
                "1.0.0",
                hashlib.sha256(f"payload-{name}".encode()).hexdigest(),
                hashlib.sha256(bundle).hexdigest(),
            )
            artifacts.append(TenantPolicyArtifact(identity, bundle))
            activations.append(
                TenantActivation(
                    f"tenant-{name}",
                    "retail-en",
                    identity,
                    "1.0.0",
                    1,
                    f"change-{name}",
                    NOW,
                )
            )
            runtimes.append((identity, TenantNormaliser(name.upper())))
        sink = InMemoryHostedAuditSink()
        service = BoundedHostedRuntime(
            policy_id="retail-en",
            authorizer=DenyByDefaultAuthorizer(InMemoryTenantGrantRepository(grants)),
            policy_repository=InMemoryTenantPolicyRepository(artifacts, activations),
            quota_manager=InMemoryTenantQuotaManager(QuotaProfile(1_000, 100, 1_000, 1)),
            runtime_registry=InMemoryTenantRuntimeRegistry(runtimes),
            audit_recorder=HostedAuditRecorder(sink),
            audit_pseudonymizer=AuditPseudonymizer(b"p" * 32, domain="prod"),
            clock=lambda: NOW,
        )
        return service, principals, sink

    def test_cross_tenant_and_concurrent_results_never_mix(self) -> None:
        service, principals, sink = self._fixture()
        attack = service.normalise(
            HostedNormaliseCommand(
                principals["a"],
                "tenant-b",
                "SYNTH-ATTACK",
                "correlation-attack",
            )
        )
        self.assertEqual(attack.status, HostedRuntimeStatus.REJECTED)
        self.assertEqual(attack.error.code, PublicErrorCode.NOT_AUTHORIZED)

        def call(index: int):
            name = "a" if index % 2 == 0 else "b"
            return name, service.normalise(
                HostedNormaliseCommand(
                    principals[name],
                    f"tenant-{name}",
                    f"SYNTH-{index}",
                    f"correlation-{index}",
                )
            )

        with ThreadPoolExecutor(max_workers=16) as executor:
            responses = tuple(executor.map(call, range(200)))
        for name, response in responses:
            self.assertEqual(response.status, HostedRuntimeStatus.ACCEPTED)
            self.assertTrue(response.output_text.startswith(f"{name.upper()}:"))
            self.assertNotIn("tenant-", response.output_text)

        serialized_audit = repr([event.to_dict() for event in sink.events])
        self.assertNotIn("tenant-a", serialized_audit)
        self.assertNotIn("tenant-b", serialized_audit)
        self.assertNotIn("SYNTH-", serialized_audit)

    def test_evidence_is_deterministic_complete_and_explicitly_not_independent(self) -> None:
        first = build_isolation_evidence(
            identity=evidence_identity(), results=passing_results()
        )
        second = build_isolation_evidence(
            identity=evidence_identity(), results=tuple(reversed(passing_results()))
        )
        self.assertEqual(first, second)
        self.assertEqual(first["evidence"]["decision"], IsolationDecision.PASS.value)
        self.assertEqual(first["evidence"]["assurance_level"], ASSURANCE_LEVEL)
        self.assertIn("not independent", " ".join(first["evidence"]["limitations"]))
        verify_isolation_evidence(first)

    def test_missing_failed_or_duplicate_scenarios_fail_closed(self) -> None:
        with self.assertRaises(ValueError):
            build_isolation_evidence(
                identity=evidence_identity(), results=passing_results()[:-1]
            )
        with self.assertRaises(ValueError):
            build_isolation_evidence(
                identity=evidence_identity(),
                results=(*passing_results(), passing_results()[0]),
            )
        failed = list(passing_results())
        failed[0] = IsolationScenarioResult(failed[0].scenario_id, False, 1)
        value = build_isolation_evidence(
            identity=evidence_identity(), results=tuple(failed)
        )
        self.assertEqual(value["evidence"]["decision"], IsolationDecision.STOP.value)

    def test_tampering_identity_drift_and_claim_inflation_are_rejected(self) -> None:
        envelope = build_isolation_evidence(
            identity=evidence_identity(), results=passing_results()
        )
        mutations = []
        digest_tamper = copy.deepcopy(envelope)
        digest_tamper["evidence"]["scenarios"][0]["passed"] = False
        mutations.append(digest_tamper)

        identity_drift = copy.deepcopy(envelope)
        identity_drift["evidence"]["identities"]["source_commit"] = "d" * 40
        identity_drift["evidence_sha256"] = hashlib.sha256(
            canonical_json_bytes(identity_drift["evidence"])
        ).hexdigest()
        mutations.append(identity_drift)

        inflated = copy.deepcopy(envelope)
        inflated["evidence"]["assurance_level"] = "INDEPENDENT"
        inflated["evidence_sha256"] = hashlib.sha256(
            canonical_json_bytes(inflated["evidence"])
        ).hexdigest()
        mutations.append(inflated)

        for index, changed in enumerate(mutations):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                verify_isolation_evidence(
                    changed,
                    expected_identity=(evidence_identity() if index == 1 else None),
                )

    def test_canonical_file_round_trip(self) -> None:
        envelope = build_isolation_evidence(
            identity=evidence_identity(), results=passing_results()
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tenant-isolation.json"
            write_isolation_evidence(envelope, path)
            self.assertEqual(load_isolation_evidence(path), envelope)

    def test_readiness_blockers_remain_unchanged(self) -> None:
        root = Path(__file__).resolve().parents[1]
        readiness = (root / "docs/production-hosted-service-profile.json").read_text()
        self.assertIn('"B-001": "OPEN"', readiness)
        self.assertIn('"B-002": "BLOCKED"', readiness)
        self.assertIn('"B-003": "OPEN"', readiness)
        self.assertIn('"status": "BLOCKED_FOR_HOSTED_SERVICE"', readiness)


if __name__ == "__main__":
    unittest.main()
