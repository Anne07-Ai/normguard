import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from normguard.hf_staging import (
    _endpoint,
    _required_secret,
    build_space_bundle,
    verify_staging,
)

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "huggingface-staging.yml"


class FakeResponse:
    def __init__(self, status: int, value: dict[str, object]) -> None:
        self.status = status
        self._body = json.dumps(value).encode()
        self._read = False

    def read(self, size: int = -1) -> bytes:
        if self._read:
            return b""
        self._read = True
        return self._body[:size]

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None


class HuggingFaceStagingTests(unittest.TestCase):
    def test_bundle_is_exact_commit_bound_and_secret_free(self) -> None:
        commit = subprocess.run(
            ("git", "-C", str(ROOT), "rev-parse", "HEAD"),
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        archive = subprocess.run(
            ("git", "-C", str(ROOT), "archive", "--format=tar", commit),
            check=True,
            capture_output=True,
        ).stdout
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "bundle"
            manifest = build_space_bundle(ROOT, output, source_commit=commit)
            self.assertEqual(manifest["source_commit"], commit)
            self.assertEqual(
                manifest["source_archive_sha256"], hashlib.sha256(archive).hexdigest()
            )
            self.assertTrue((output / "Dockerfile").is_file())
            self.assertTrue((output / "README.md").is_file())
            self.assertEqual(
                json.loads((output / "deployment-manifest.json").read_text()), manifest
            )
            relative = {path.relative_to(output) for path in output.rglob("*")}
            for path in relative:
                self.assertTrue({".git", "artifacts", "pilot-secrets"}.isdisjoint(path.parts))
                self.assertNotEqual(path.name, "huggingface-secrets.env")

    def test_workflow_is_manual_least_privilege_and_uses_environment_secrets(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", workflow)
        self.assertNotIn("pull_request:", workflow)
        self.assertNotIn("push:", workflow)
        self.assertIn("contents: read", workflow)
        self.assertIn("environment: huggingface-staging", workflow)
        self.assertIn("secrets.HF_TOKEN", workflow)
        self.assertIn("secrets.NORMGUARD_BEARER_TOKEN", workflow)
        self.assertIn("git merge-base --is-ancestor", workflow)
        self.assertIn("persist-credentials: false", workflow)
        self.assertNotIn("set -x", workflow)
        self.assertNotIn("echo $HF_TOKEN", workflow)

    def test_secrets_and_endpoint_are_strictly_bounded(self) -> None:
        self.assertEqual(_required_secret({"TOKEN": "x" * 32}, "TOKEN"), "x" * 32)
        for value in ("", "short"):
            with self.assertRaises(ValueError):
                _required_secret({"TOKEN": value}, "TOKEN")
        self.assertEqual(
            _endpoint("https://lakshmianne-normguard-private-pilot.hf.space/"),
            "https://lakshmianne-normguard-private-pilot.hf.space",
        )
        for endpoint in (
            "http://example.hf.space",
            "https://example.com",
            "https://token@example.hf.space",
            "https://example.hf.space?token=secret",
        ):
            with self.assertRaises(ValueError):
                _endpoint(endpoint)

    def test_verifier_proves_health_denial_and_dual_authenticated_request(self) -> None:
        responses = iter(
            (
                FakeResponse(
                    200,
                    {
                        "schema_version": 1,
                        "status": "ready",
                        "ready": True,
                        "sdk_version": "0.1.0",
                    },
                ),
                FakeResponse(401, {"schema_version": 1, "error_code": "UNAUTHENTICATED"}),
                FakeResponse(
                    200,
                    {
                        "schema_version": 1,
                        "status": "accepted",
                        "output_text": "SKU-42 Acme",
                        "error_code": None,
                    },
                ),
            )
        )
        with patch("urllib.request.urlopen", side_effect=lambda *_a, **_k: next(responses)):
            result = verify_staging(
                "https://example.hf.space",
                hf_token="h" * 32,
                application_token="n" * 32,
                attempts=1,
                delay_seconds=0,
            )
        self.assertEqual(
            result,
            {
                "schema_version": 1,
                "ready": True,
                "dual_authentication": True,
                "synthetic_request": "passed",
            },
        )
        self.assertNotIn("SKU-42", repr(result))

    def test_rollback_runbook_is_exact_commit_and_does_not_claim_production(self) -> None:
        runbook = (ROOT / "docs" / "huggingface-staging-deployment.md").read_text()
        normalized = " ".join(runbook.split())
        for required in (
            "exact previously passing commit",
            "GitHub Environment approval",
            "synthetic data only",
            "does not close B-001, B-002, B-003 or NG-T22",
        ):
            self.assertIn(required, normalized)


if __name__ == "__main__":
    unittest.main()
