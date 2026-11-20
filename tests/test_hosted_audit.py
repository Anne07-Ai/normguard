import unittest
from datetime import UTC, datetime

from normguard.hosted_audit import (
    AuditOutcome,
    AuditPseudonymizer,
    AuditRequirement,
    AuditWriteCode,
    HostedAuditEvent,
    HostedAuditRecorder,
    InMemoryHostedAuditSink,
    InternalFailure,
    public_error_for_failure,
)
from normguard.hosted_authorization import (
    AuthorizedContext,
    HostedAction,
    ResourceType,
)

NOW = datetime(2026, 11, 20, 14, tzinfo=UTC)


def context(tenant: str = "tenant-a", principal: str = "principal-1") -> AuthorizedContext:
    return AuthorizedContext(
        principal,
        tenant,
        HostedAction.NORMALISE,
        ResourceType.NORMALISATION,
        "request-1",
        ("grant-1",),
    )


def event(
    *,
    privileged: bool = False,
    pseudonymizer: AuditPseudonymizer | None = None,
) -> HostedAuditEvent:
    return HostedAuditEvent.from_authorized_context(
        context(),
        pseudonymizer=pseudonymizer or AuditPseudonymizer(b"a" * 32, domain="prod"),
        event_id="event-1",
        correlation_id="correlation-1",
        outcome=AuditOutcome.SUCCEEDED,
        reason_code="completed",
        privileged=privileged,
        artifact_sha256="b" * 64,
        occurred_at=NOW,
    )


class HostedAuditTests(unittest.TestCase):
    def test_pseudonyms_are_stable_domain_separated_and_keyed(self) -> None:
        first = AuditPseudonymizer(b"a" * 32, domain="prod")
        same = AuditPseudonymizer(b"a" * 32, domain="prod")
        other_domain = AuditPseudonymizer(b"a" * 32, domain="staging")
        other_key = AuditPseudonymizer(b"b" * 32, domain="prod")

        expected = first.pseudonymize("tenant-a", label="tenant")
        self.assertEqual(expected, same.pseudonymize("tenant-a", label="tenant"))
        self.assertNotEqual(
            expected, other_domain.pseudonymize("tenant-a", label="tenant")
        )
        self.assertNotEqual(expected, other_key.pseudonymize("tenant-a", label="tenant"))
        self.assertNotEqual(
            expected, first.pseudonymize("tenant-a", label="principal")
        )

    def test_audit_event_contains_no_raw_authority_or_content(self) -> None:
        value = event().to_dict()
        serialized = repr(value)
        self.assertNotIn("tenant-a", serialized)
        self.assertNotIn("principal-1", serialized)
        self.assertNotIn("request-1", serialized)
        self.assertNotIn("input", serialized)
        self.assertNotIn("output", serialized)
        self.assertEqual(len(value["tenant_ref"]), 64)
        self.assertEqual(len(value["principal_ref"]), 64)

    def test_privileged_operation_requires_mandatory_audit(self) -> None:
        recorder = HostedAuditRecorder(InMemoryHostedAuditSink())
        with self.assertRaisesRegex(ValueError, "must be mandatory"):
            recorder.record(event(privileged=True), requirement=AuditRequirement.BEST_EFFORT)

    def test_mandatory_sink_failure_blocks_privileged_continuation(self) -> None:
        class BrokenSink:
            def emit(self, _event: HostedAuditEvent) -> None:
                raise RuntimeError("backend topology and credential must not escape")

        result = HostedAuditRecorder(BrokenSink()).record(
            event(privileged=True), requirement=AuditRequirement.MANDATORY
        )
        self.assertEqual(result.code, AuditWriteCode.BLOCKED)
        self.assertFalse(result.may_continue)
        self.assertNotIn("backend", repr(result))

    def test_best_effort_failure_does_not_change_data_plane_semantics(self) -> None:
        class BrokenSink:
            def emit(self, _event: HostedAuditEvent) -> None:
                raise OSError("unavailable")

        result = HostedAuditRecorder(BrokenSink()).record(
            event(), requirement=AuditRequirement.BEST_EFFORT
        )
        self.assertEqual(result.code, AuditWriteCode.DROPPED)
        self.assertTrue(result.may_continue)

    def test_successful_write_is_bounded_and_deterministic(self) -> None:
        sink = InMemoryHostedAuditSink()
        result = HostedAuditRecorder(sink).record(
            event(), requirement=AuditRequirement.BEST_EFFORT
        )
        self.assertEqual(result.code, AuditWriteCode.WRITTEN)
        self.assertEqual(len(sink.events), 1)
        self.assertEqual(sink.events[0].to_dict(), event().to_dict())

    def test_not_found_and_not_authorized_collapse_to_same_public_error(self) -> None:
        missing = public_error_for_failure(
            InternalFailure.NOT_FOUND, correlation_id="correlation-1"
        )
        denied = public_error_for_failure(
            InternalFailure.NOT_AUTHORIZED, correlation_id="correlation-1"
        )
        self.assertEqual(missing.to_dict(), denied.to_dict())
        self.assertEqual(missing.code.value, "NOT_AUTHORIZED")

    def test_public_errors_expose_only_closed_bounded_fields(self) -> None:
        error = public_error_for_failure(
            InternalFailure.RATE_LIMITED,
            correlation_id="correlation-1",
            retry_after_seconds=30,
        )
        value = error.to_dict()
        self.assertEqual(
            set(value),
            {
                "schema_version",
                "error_code",
                "correlation_id",
                "retryable",
                "retry_after_seconds",
            },
        )
        self.assertEqual(value["error_code"], "RATE_LIMITED")
        self.assertNotIn("message", value)
        self.assertNotIn("detail", value)


if __name__ == "__main__":
    unittest.main()
