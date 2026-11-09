import base64
import json
import unittest

from fastapi.testclient import TestClient

from normguard.runtime import RuntimeLimits
from normguard.sdk import SDKError
from normguard.service import (
    FixedWindowRateLimiter,
    PrivatePilotService,
    ServiceSettings,
    build_app_from_environment,
    create_app,
)
from tests.test_sdk import NOW, _documents

TOKEN = "pilot-token-with-at-least-thirty-two-characters"


def _service() -> PrivatePilotService:
    bundle, trust, activation = _documents()
    from normguard.service import PolicyDocuments

    return PrivatePilotService(
        PolicyDocuments(bundle, trust, activation),
        verification_time=NOW,
        runtime_limits=RuntimeLimits(max_input_bytes=64),
    )


def _client(*, requests_per_minute: int = 120) -> TestClient:
    app = create_app(
        _service(),
        ServiceSettings(
            bearer_token=TOKEN,
            max_body_bytes=128,
            requests_per_minute=requests_per_minute,
        ),
    )
    return TestClient(app, raise_server_exceptions=False)


def _headers(token: str = TOKEN) -> dict[str, str]:
    return {"X-NormGuard-Token": token}


class PrivateServiceTests(unittest.TestCase):
    def test_health_is_minimal_and_security_headers_are_set(self) -> None:
        response = _client().get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            set(response.json()),
            {"schema_version", "status", "ready", "sdk_version"},
        )
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")

    def test_authentication_is_deny_by_default_and_never_echoes_token(self) -> None:
        client = _client()
        for headers in (
            {},
            {"Authorization": f"Bearer {TOKEN}"},
            _headers("wrong-token-with-at-least-thirty-two-chars"),
        ):
            response = client.post("/v1/normalise", json={"text": "ACME LTD"}, headers=headers)
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.json()["error_code"], "UNAUTHENTICATED")
            self.assertNotIn(TOKEN, response.text)

    def test_success_uses_closed_response_without_policy_or_evidence_details(self) -> None:
        response = _client().post(
            "/v1/normalise",
            json={"text": "SKU-42 ACME LTD"},
            headers=_headers(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "schema_version": 1,
                "status": "accepted",
                "output_text": "SKU-42 Acme",
                "error_code": None,
            },
        )
        for forbidden in ("policy-key", "retail-en", "decision_id", "input_sha256"):
            self.assertNotIn(forbidden, response.text)

    def test_unknown_fields_malformed_json_and_oversized_bodies_fail_closed(self) -> None:
        client = _client()
        unknown = client.post(
            "/v1/normalise",
            json={"text": "ACME LTD", "tenant": "bypass"},
            headers=_headers(),
        )
        malformed = client.post(
            "/v1/normalise",
            content=b'{"text":',
            headers={**_headers(), "Content-Type": "application/json"},
        )
        oversized = client.post(
            "/v1/normalise",
            json={"text": "x" * 200},
            headers=_headers(),
        )
        self.assertEqual(unknown.status_code, 422)
        self.assertEqual(malformed.status_code, 422)
        self.assertEqual(oversized.status_code, 413)
        self.assertEqual(oversized.json()["error_code"], "REQUEST_TOO_LARGE")

    def test_non_json_media_type_is_rejected_before_parsing(self) -> None:
        response = _client().post(
            "/v1/normalise",
            content=b'{"text":"ACME LTD"}',
            headers={**_headers(), "Content-Type": "text/plain"},
        )
        self.assertEqual(response.status_code, 415)
        self.assertEqual(response.json()["error_code"], "UNSUPPORTED_MEDIA_TYPE")

    def test_rate_limit_uses_one_principal_bucket(self) -> None:
        client = _client(requests_per_minute=1)
        first = client.post("/v1/normalise", json={"text": "ACME LTD"}, headers=_headers())
        second = client.post("/v1/normalise", json={"text": "ACME LTD"}, headers=_headers())
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(second.json()["error_code"], "RATE_LIMITED")
        self.assertGreaterEqual(int(second.headers["retry-after"]), 1)

    def test_limiter_resets_only_after_declared_window(self) -> None:
        now = [100.0]
        limiter = FixedWindowRateLimiter(1, window_seconds=10, clock=lambda: now[0])
        self.assertTrue(limiter.acquire()[0])
        self.assertFalse(limiter.acquire()[0])
        now[0] = 110.0
        self.assertTrue(limiter.acquire()[0])

    def test_environment_startup_rejects_missing_invalid_or_untrusted_configuration(self) -> None:
        with self.assertRaises(ValueError):
            build_app_from_environment({})

        bundle, trust, activation = _documents()
        environment = {
            "NORMGUARD_BEARER_TOKEN": TOKEN,
            "NORMGUARD_POLICY_B64": base64.b64encode(bundle).decode(),
            "NORMGUARD_TRUST_STORE_B64": base64.b64encode(trust).decode(),
            "NORMGUARD_ACTIVATION_STATE_B64": base64.b64encode(activation).decode(),
        }
        environment["NORMGUARD_POLICY_B64"] = "not-base64"
        with self.assertRaises(RuntimeError):
            build_app_from_environment(environment)

        environment["NORMGUARD_POLICY_B64"] = base64.b64encode(
            json.dumps({"tampered": True}).encode()
        ).decode()
        with self.assertRaises(SDKError):
            build_app_from_environment(environment)


if __name__ == "__main__":
    unittest.main()
