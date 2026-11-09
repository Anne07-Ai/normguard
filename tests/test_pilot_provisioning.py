import base64
import os
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from normguard.pilot_provisioning import SECRET_NAMES, provision_private_pilot, write_secret_file
from normguard.service import build_app_from_environment


class PilotProvisioningTests(unittest.TestCase):
    def test_generated_secrets_boot_real_service_and_preserve_identifier(self) -> None:
        provisioned = provision_private_pilot(now=datetime(2026, 11, 9, 10, 0, tzinfo=UTC))
        self.assertEqual(set(provisioned.secrets), set(SECRET_NAMES))
        app = build_app_from_environment(provisioned.secrets)
        with TestClient(app) as client:
            response = client.post(
                "/v1/normalise",
                headers={
                    "Authorization": f"Bearer {provisioned.secrets['NORMGUARD_BEARER_TOKEN']}"
                },
                json={"text": "NORMGUARD-PILOT"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["output_text"], "NORMGUARD-PILOT")

    def test_output_is_owner_only_and_contains_no_private_key(self) -> None:
        provisioned = provision_private_pilot(now=datetime(2026, 11, 9, 10, 0, tzinfo=UTC))
        with tempfile.TemporaryDirectory() as temporary:
            destination = write_secret_file(Path(temporary) / "pilot-secrets", provisioned)
            self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
            content = destination.read_text(encoding="utf-8")
            self.assertNotIn("PRIVATE KEY", content)
            self.assertEqual(
                {line.split("=", 1)[0] for line in content.splitlines()}, set(SECRET_NAMES)
            )
            for name in SECRET_NAMES[1:]:
                base64.b64decode(provisioned.secrets[name], validate=True)

    def test_existing_secret_file_is_not_overwritten(self) -> None:
        provisioned = provision_private_pilot(now=datetime(2026, 11, 9, 10, 0, tzinfo=UTC))
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "pilot-secrets"
            write_secret_file(output, provisioned)
            with self.assertRaises(FileExistsError):
                write_secret_file(output, provisioned)

    def test_secret_values_do_not_leak_to_process_environment(self) -> None:
        before = dict(os.environ)
        provision_private_pilot(now=datetime(2026, 11, 9, 10, 0, tzinfo=UTC))
        self.assertEqual(dict(os.environ), before)

    def test_naive_time_and_existing_or_symlinked_output_fail_closed(self) -> None:
        with self.assertRaises(ValueError):
            provision_private_pilot(now=datetime(2026, 11, 9, 10, 0))
        provisioned = provision_private_pilot(now=datetime(2026, 11, 9, 10, 0, tzinfo=UTC))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            existing = root / "existing"
            existing.mkdir()
            with self.assertRaises(FileExistsError):
                write_secret_file(existing, provisioned)
            target = root / "target"
            target.mkdir()
            link = root / "link"
            link.symlink_to(target, target_is_directory=True)
            with self.assertRaises(ValueError):
                write_secret_file(link, provisioned)
