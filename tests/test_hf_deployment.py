import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy" / "huggingface"


class HuggingFaceDeploymentContractTests(unittest.TestCase):
    def test_dockerfile_runs_non_root_without_access_logs_or_research_extras(self) -> None:
        dockerfile = (DEPLOY / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn(
            "FROM python:3.12.12-slim@sha256:"
            "f3fa41d74a768c2fce8016b98c191ae8c1bacd8f1152870a3f9f87d350920b7c",
            dockerfile,
        )
        self.assertIn('python -m pip install ".[service]"', dockerfile)
        self.assertIn("USER appuser", dockerfile)
        self.assertIn('"--no-access-log"', dockerfile)
        self.assertIn('"--workers", "1"', dockerfile)
        self.assertNotIn("[research]", dockerfile)
        self.assertNotIn("NORMGUARD_BEARER_TOKEN=", dockerfile)

    def test_space_contract_is_private_single_tenant_and_forbids_signing_keys(self) -> None:
        readme = (DEPLOY / "README.md").read_text(encoding="utf-8")
        normalized = " ".join(readme.split())
        for required in (
            "private, single-tenant controlled pilot",
            "Never upload a private signing key",
            "synthetic inputs only",
            "never treat a private Space as an enterprise identity",
        ):
            self.assertIn(required, normalized)

    def test_entry_point_only_builds_the_fail_closed_environment_app(self) -> None:
        entrypoint = (DEPLOY / "app.py").read_text(encoding="utf-8")
        self.assertEqual(
            entrypoint,
            '"""Process entry point for the private Hugging Face Docker Space."""\n\n'
            "from normguard.service import build_app_from_environment\n\n"
            "app = build_app_from_environment()\n",
        )


if __name__ == "__main__":
    unittest.main()
