import json
import unittest
from collections.abc import Callable

from normguard.capacity import CapacityDecision, CapacityProfile, CapacitySamples
from normguard.hosted_capacity import (
    DEFAULT_ENDPOINT,
    RATE_LIMIT_RESET_SECONDS,
    WORKLOAD_SHA256,
    _require_secret,
    build_operation,
    representative_profile,
    run_hosted_capacity,
)


class _Headers:
    def __init__(self, content_type: str = "application/json") -> None:
        self._content_type = content_type

    def get_content_type(self) -> str:
        return self._content_type


class _Response:
    def __init__(
        self,
        payload: object,
        *,
        status: int = 200,
        content_type: str = "application/json",
    ) -> None:
        self.status = status
        self.headers = _Headers(content_type)
        self._payload = json.dumps(payload).encode()

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, size: int) -> bytes:
        return self._payload[:size]


def _accepted() -> dict[str, object]:
    return {
        "schema_version": 1,
        "status": "accepted",
        "output_text": "sku-42 acme ltd",
        "error_code": None,
    }


def _passing_samples(
    profile: CapacityProfile, operation: Callable[[], object]
) -> CapacitySamples:
    operation()
    return CapacitySamples(
        durations_ns=(250_000_000,) * profile.measured_requests,
        error_count=0,
        elapsed_ns=601_000_000_000,
        peak_traced_bytes=1_000_000,
        process_cpu_seconds=1,
    )


class HostedCapacityTests(unittest.TestCase):
    def test_profile_is_frozen_representative_and_sustained(self) -> None:
        profile = representative_profile()
        self.assertEqual(profile.profile_id, "hf-cpu-basic-private-pilot")
        self.assertEqual(profile.measured_requests, 1_000)
        self.assertEqual(profile.minimum_duration_seconds, 600)
        self.assertEqual(profile.concurrency, 4)
        self.assertEqual(RATE_LIMIT_RESET_SECONDS, 61)
        measured_per_minute = (
            profile.measured_requests / profile.minimum_duration_seconds * 60
        )
        self.assertLessEqual(measured_per_minute, 100)
        self.assertEqual(len(WORKLOAD_SHA256), 64)

    def test_operation_uses_dual_auth_and_closed_response(self) -> None:
        captured: list[object] = []

        def opener(request: object, *, timeout: int) -> _Response:
            captured.extend((request, timeout))
            return _Response(_accepted())

        operation = build_operation(
            endpoint=DEFAULT_ENDPOINT,
            hf_token="h" * 32,
            service_token="s" * 32,
            opener=opener,
        )
        operation()

        request = captured[0]
        self.assertEqual(captured[1], 20)
        self.assertEqual(request.get_header("Authorization"), f"Bearer {'h' * 32}")
        self.assertEqual(request.get_header("X-normguard-token"), "s" * 32)
        self.assertEqual(json.loads(request.data), {"text": "SKU-42 ACME LTD"})

    def test_invalid_endpoint_response_and_missing_secrets_fail_closed(self) -> None:
        with self.assertRaises(ValueError):
            build_operation(
                endpoint="http://example.test/v1/normalise",
                hf_token="h",
                service_token="s",
            )
        with self.assertRaises(ValueError):
            _require_secret({}, "HF_TOKEN")
        with self.assertRaises(ValueError):
            _require_secret({"HF_TOKEN": " short "}, "HF_TOKEN")

        for response in (
            _Response(_accepted(), status=401),
            _Response(_accepted(), content_type="text/html"),
            _Response({"status": "accepted"}),
            _Response({**_accepted(), "status": "rejected"}),
        ):
            operation = build_operation(
                endpoint=DEFAULT_ENDPOINT,
                hf_token="h" * 32,
                service_token="s" * 32,
                opener=lambda *args, response=response, **kwargs: response,
            )
            with self.subTest(response=response), self.assertRaises(RuntimeError):
                operation()

    def test_evidence_is_representative_digest_bound_and_secret_free(self) -> None:
        envelope = run_hosted_capacity(
            source_commit="a" * 40,
            artifact_sha256="b" * 64,
            target_environment_sha256="c" * 64,
            endpoint=DEFAULT_ENDPOINT,
            hf_token="hf-secret-value-that-must-not-leak",
            service_token="service-secret-that-must-not-leak",
            sample_runner=_passing_samples,
            opener=lambda *args, **kwargs: _Response(_accepted()),
        )
        evidence = envelope["evidence"]
        self.assertEqual(evidence["decision"], CapacityDecision.PASS.value)
        self.assertEqual(evidence["scope"], "representative")
        self.assertEqual(evidence["identities"]["workload_sha256"], WORKLOAD_SHA256)
        serialised = json.dumps(envelope)
        self.assertNotIn("hf-secret", serialised)
        self.assertNotIn("service-secret", serialised)
        self.assertNotIn("SKU-42", serialised)


if __name__ == "__main__":
    unittest.main()
