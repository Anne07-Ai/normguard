import json
import unittest
from concurrent.futures import ThreadPoolExecutor

from examples.integrations.service import (
    ApplicationStatus,
    PolicyDocuments,
    SafetyCoreService,
)
from normguard import RuntimeLimits, SafetyCore, SDKError
from normguard.contracts import PolicyClass, PolicyRule
from tests.test_observability import RecordingSink
from tests.test_runtime import _verified
from tests.test_sdk import NOW, _documents


def _policy_documents() -> PolicyDocuments:
    bundle, trust, activation = _documents()
    return PolicyDocuments(bundle, trust, activation)


class ProductionIntegrationExampleTests(unittest.TestCase):
    def test_startup_fails_closed_for_untrusted_policy(self) -> None:
        documents = _policy_documents()
        invalid = PolicyDocuments(
            b"invalid-private-policy-content",
            documents.trust_store,
            documents.activation_state,
        )
        with self.assertRaises(SDKError):
            SafetyCoreService(invalid, verification_time=NOW)

    def test_success_review_and_limit_are_distinct_application_outcomes(self) -> None:
        service = SafetyCoreService(
            _policy_documents(),
            verification_time=NOW,
            runtime_limits=RuntimeLimits(max_input_bytes=20),
        )
        accepted = service.normalise("ACME LTD")
        limited = service.normalise("a request over twenty bytes")
        self.assertEqual(accepted.status, ApplicationStatus.ACCEPTED)
        self.assertEqual(accepted.output_text, "Acme")
        self.assertEqual(limited.status, ApplicationStatus.LIMIT_EXCEEDED)
        self.assertIsNone(limited.output_text)

        review_core = SafetyCore(
            _verified(
                PolicyRule(
                    "review",
                    PolicyClass.REVIEW_OR_ABSTAIN,
                    "exact",
                    "ambiguous",
                )
            )
        )
        with service._lock:
            service._core = review_core
        review = service.normalise("ambiguous")
        self.assertEqual(review.status, ApplicationStatus.REVIEW_REQUIRED)
        self.assertEqual(review.output_text, "ambiguous")

    def test_failed_reload_retains_last_known_good_core(self) -> None:
        documents = _policy_documents()
        service = SafetyCoreService(documents, verification_time=NOW)
        before = service.normalise("ACME LTD")
        invalid = PolicyDocuments(
            b"tampered-secret-policy",
            documents.trust_store,
            documents.activation_state,
        )
        reload_result = service.reload(invalid, verification_time=NOW)
        after = service.normalise("ACME LTD")
        self.assertFalse(reload_result.activated)
        self.assertEqual(reload_result.error_code, "INVALID_DOCUMENT")
        self.assertEqual(after, before)

    def test_successful_reload_is_atomic_under_concurrent_requests(self) -> None:
        documents = _policy_documents()
        service = SafetyCoreService(documents, verification_time=NOW)

        def request() -> tuple[ApplicationStatus, str | None]:
            response = service.normalise("SKU-42 ACME LTD")
            return response.status, response.output_text

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(request) for _ in range(30)]
            reload_result = service.reload(documents, verification_time=NOW)
            futures.extend(pool.submit(request) for _ in range(30))
        self.assertTrue(reload_result.activated)
        self.assertEqual(
            {future.result() for future in futures},
            {(ApplicationStatus.ACCEPTED, "SKU-42 Acme")},
        )

    def test_health_and_telemetry_do_not_expose_sensitive_identifiers(self) -> None:
        sink = RecordingSink()
        service = SafetyCoreService(
            _policy_documents(),
            verification_time=NOW,
            telemetry=sink,
        )
        result = service.normalise("SKU-42")
        health = service.health()
        serialized = json.dumps(
            {
                "health": {
                    "status": health.status,
                    "ready": health.ready,
                    "sdk_version": health.sdk_version,
                },
                "events": [event.to_dict() for event in sink.events],
                "metrics": [metric.to_dict() for metric in sink.metrics],
            }
        )
        self.assertEqual(result.status, ApplicationStatus.ACCEPTED)
        self.assertTrue(health.ready)
        for forbidden in (
            "SKU-42",
            "retail-en",
            "policy-key",
            service._core.verified_policy.payload_sha256,
        ):
            self.assertNotIn(forbidden, serialized)


if __name__ == "__main__":
    unittest.main()
