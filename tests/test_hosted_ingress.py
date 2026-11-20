import unittest
from datetime import UTC, datetime, timedelta

from normguard.hosted_audit import (
    InternalFailure,
    PublicErrorCode,
    public_error_for_failure,
)
from normguard.hosted_authorization import PrincipalKind, VerifiedPrincipal
from normguard.hosted_ingress import HostedIngressRequest, HostedOidcIngress
from normguard.hosted_runtime import (
    HostedNormaliseCommand,
    HostedNormaliseResponse,
    HostedRuntimeStatus,
)
from normguard.oidc_identity import OidcIdentityCode, OidcIdentityResult

NOW = datetime(2026, 11, 20, 16, tzinfo=UTC)


def principal() -> VerifiedPrincipal:
    return VerifiedPrincipal(
        "oidc-" + ("a" * 64),
        "issuer-" + ("b" * 64),
        "subject-" + ("c" * 64),
        "audience-" + ("d" * 64),
        PrincipalKind.SERVICE,
        NOW - timedelta(seconds=1),
        NOW + timedelta(minutes=5),
    )


class StubVerifier:
    def __init__(self, result: OidcIdentityResult, *, raises: bool = False) -> None:
        self.result = result
        self.raises = raises
        self.calls = 0

    def verify(self, token: str, *, at: datetime | None = None) -> OidcIdentityResult:
        self.calls += 1
        if self.raises:
            raise RuntimeError(f"must not leak token {token}")
        return self.result


class CapturingRuntime:
    def __init__(self, response: HostedNormaliseResponse, *, raises: bool = False) -> None:
        self.response = response
        self.raises = raises
        self.commands: list[HostedNormaliseCommand] = []

    def normalise(self, command: HostedNormaliseCommand) -> HostedNormaliseResponse:
        self.commands.append(command)
        if self.raises:
            raise RuntimeError("sensitive runtime detail")
        return self.response


def request(*, tenant: str = "tenant-a") -> HostedIngressRequest:
    return HostedIngressRequest(
        "secret.jwt.value",
        tenant,
        "SKU-42 ACME",
        "correlation-1",
        "request-1",
    )


class HostedIngressTests(unittest.TestCase):
    def test_verified_identity_is_the_only_principal_forwarded_to_runtime(self) -> None:
        verified = principal()
        verifier = StubVerifier(OidcIdentityResult(OidcIdentityCode.VERIFIED, verified))
        runtime = CapturingRuntime(
            HostedNormaliseResponse(HostedRuntimeStatus.ACCEPTED, "sku-42 acme")
        )
        response = HostedOidcIngress(
            identity_verifier=verifier, runtime=runtime, clock=lambda: NOW
        ).normalise(request())

        self.assertEqual(response.status, HostedRuntimeStatus.ACCEPTED)
        self.assertEqual(len(runtime.commands), 1)
        command = runtime.commands[0]
        self.assertIs(command.principal, verified)
        self.assertEqual(command.requested_tenant_id, "tenant-a")
        self.assertEqual(command.text, "SKU-42 ACME")
        self.assertNotIn("secret.jwt.value", repr(command))

    def test_every_identity_failure_collapses_before_runtime(self) -> None:
        for code in OidcIdentityCode:
            if code is OidcIdentityCode.VERIFIED:
                continue
            with self.subTest(code=code):
                verifier = StubVerifier(OidcIdentityResult(code))
                runtime = CapturingRuntime(
                    HostedNormaliseResponse(HostedRuntimeStatus.ACCEPTED, "unexpected")
                )
                response = HostedOidcIngress(
                    identity_verifier=verifier, runtime=runtime, clock=lambda: NOW
                ).normalise(request())
                self.assertEqual(response.status, HostedRuntimeStatus.REJECTED)
                self.assertEqual(response.error.code, PublicErrorCode.UNAUTHENTICATED)
                self.assertEqual(runtime.commands, [])
                self.assertNotIn("secret.jwt.value", repr(response))

    def test_identity_dependency_exception_is_credential_free_and_fail_closed(self) -> None:
        verifier = StubVerifier(
            OidcIdentityResult(OidcIdentityCode.MALFORMED), raises=True
        )
        runtime = CapturingRuntime(
            HostedNormaliseResponse(HostedRuntimeStatus.ACCEPTED, "unexpected")
        )
        response = HostedOidcIngress(
            identity_verifier=verifier, runtime=runtime, clock=lambda: NOW
        ).normalise(request())
        self.assertEqual(response.error.code, PublicErrorCode.UNAUTHENTICATED)
        self.assertEqual(runtime.commands, [])
        self.assertNotIn("secret", repr(response))

    def test_tenant_request_is_not_promoted_to_authority_at_ingress(self) -> None:
        verifier = StubVerifier(
            OidcIdentityResult(OidcIdentityCode.VERIFIED, principal())
        )
        denied = HostedNormaliseResponse(
            HostedRuntimeStatus.REJECTED,
            error=public_error_for_failure(
                InternalFailure.NOT_AUTHORIZED,
                correlation_id="correlation-1",
            ),
        )
        runtime = CapturingRuntime(denied)
        response = HostedOidcIngress(
            identity_verifier=verifier, runtime=runtime, clock=lambda: NOW
        ).normalise(request(tenant="tenant-b"))
        self.assertEqual(response.error.code, PublicErrorCode.NOT_AUTHORIZED)
        self.assertEqual(runtime.commands[0].requested_tenant_id, "tenant-b")

    def test_unexpected_runtime_exception_is_bounded(self) -> None:
        verifier = StubVerifier(
            OidcIdentityResult(OidcIdentityCode.VERIFIED, principal())
        )
        runtime = CapturingRuntime(
            HostedNormaliseResponse(HostedRuntimeStatus.ACCEPTED, "unused"), raises=True
        )
        response = HostedOidcIngress(
            identity_verifier=verifier, runtime=runtime, clock=lambda: NOW
        ).normalise(request())
        self.assertEqual(response.error.code, PublicErrorCode.INTERNAL_FAILURE)
        self.assertNotIn("sensitive", repr(response))

    def test_untrusted_request_shape_is_bounded_before_identity_work(self) -> None:
        with self.assertRaises(ValueError):
            HostedIngressRequest("token", "../tenant", "text", "correlation-1")
        with self.assertRaises(ValueError):
            HostedIngressRequest("token", "tenant-a", "", "correlation-1")
        with self.assertRaises(ValueError):
            HostedIngressRequest("", "tenant-a", "text", "correlation-1")


if __name__ == "__main__":
    unittest.main()
