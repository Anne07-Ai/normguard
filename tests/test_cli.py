import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from normguard import cli
from normguard.safety_contracts import DecisionEvidence, EvidenceStatus
from normguard.sdk import SDKError


class CLIFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        for name in ("policy.json", "trust.json", "activation.json"):
            (self.root / name).write_bytes(b"{}")
        self.policy = str(self.root / "policy.json")
        self.trust = str(self.root / "trust.json")
        self.activation = str(self.root / "activation.json")
        self.time = "2026-10-22T16:30:00Z"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def common(self) -> list[str]:
        return [
            "--trust-store",
            self.trust,
            "--activation-state",
            self.activation,
            "--verification-time",
            self.time,
            "--json",
        ]

    def run_main(self, arguments: list[str]) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = cli.main(arguments)
        return exit_code, stdout.getvalue(), stderr.getvalue()


class PolicyVerifyCLITests(CLIFixture):
    def test_policy_verify_emits_stable_json_and_success_exit(self) -> None:
        verified = SimpleNamespace(
            payload=SimpleNamespace(policy_id="retail-en", policy_version="1.0.0"),
            payload_sha256="a" * 64,
            signing_key_id="policy-key",
            signing_key_sha256="b" * 64,
            rollback_authorized=False,
        )
        with patch.object(cli, "verify_policy", return_value=verified):
            code, stdout, stderr = self.run_main(
                ["policy", "verify", self.policy, *self.common()]
            )
        self.assertEqual(code, cli.ExitCode.SUCCESS)
        self.assertEqual(stderr, "")
        value = json.loads(stdout)
        self.assertEqual(value["schema_version"], 1)
        self.assertEqual(value["status"], "verified")
        self.assertEqual(value["policy"]["policy_id"], "retail-en")

    def test_untrusted_policy_has_distinct_exit_and_safe_stderr(self) -> None:
        signature = "sensitive-signature"
        with patch.object(
            cli,
            "verify_policy",
            side_effect=SDKError(
                "UNKNOWN_KEY",
                policy_id="retail-en",
                key_id="missing-key",
            ),
        ):
            code, stdout, stderr = self.run_main(
                ["policy", "verify", self.policy, *self.common()]
            )
        self.assertEqual(code, cli.ExitCode.UNTRUSTED_POLICY)
        self.assertEqual(stdout, "")
        value = json.loads(stderr)
        self.assertEqual(value["error"]["code"], "UNKNOWN_KEY")
        self.assertNotIn(signature, stderr)

    def test_missing_json_flag_is_invalid_input(self) -> None:
        code, stdout, stderr = self.run_main(
            [
                "policy",
                "verify",
                self.policy,
                "--trust-store",
                self.trust,
                "--activation-state",
                self.activation,
                "--verification-time",
                self.time,
            ]
        )
        self.assertEqual(code, cli.ExitCode.INVALID_INPUT)
        self.assertEqual(stdout, "")
        self.assertEqual(json.loads(stderr)["error"]["code"], "JSON_OUTPUT_REQUIRED")

    def test_expected_usage_error_has_no_traceback(self) -> None:
        code, _, stderr = self.run_main(["policy", "verify"])
        self.assertEqual(code, cli.ExitCode.INVALID_INPUT)
        self.assertNotIn("Traceback", stderr)
        self.assertEqual(json.loads(stderr)["error"]["code"], "INVALID_ARGUMENT")


class NormaliseCLITests(CLIFixture):
    def evidence(self, status: EvidenceStatus) -> DecisionEvidence:
        return DecisionEvidence(
            decision_id="decision-cli",
            runtime_version="0.1.0",
            policy_id="retail-en",
            policy_version="1.0.0",
            policy_sha256="a" * 64,
            input_sha256="b" * 64,
            input_bytes=6,
            status=status,
            spans=(),
            safety_events=(),
            duration_ns=0,
        )

    def test_normalise_success_and_review_exit_codes(self) -> None:
        input_path = self.root / "input.txt"
        input_path.write_text("SKU-42", encoding="utf-8")
        for status, expected in (
            (EvidenceStatus.SUCCESS, cli.ExitCode.SUCCESS),
            (EvidenceStatus.REVIEW_REQUIRED, cli.ExitCode.REVIEW_REQUIRED),
        ):
            core = SimpleNamespace(
                normalise=lambda text, selected=status: SimpleNamespace(
                    output_text=text,
                    status=selected,
                    evidence=self.evidence(selected),
                )
            )
            with (
                self.subTest(status=status),
                patch.object(cli, "build_safety_core", return_value=core),
            ):
                code, stdout, stderr = self.run_main(
                    [
                        "normalise",
                        "--policy",
                        self.policy,
                        *self.common(),
                        "--input-file",
                        str(input_path),
                    ]
                )
            self.assertEqual(code, expected)
            self.assertEqual(stderr, "")
            value = json.loads(stdout)
            self.assertEqual(value["status"], status.value)
            self.assertEqual(value["output_text"], "SKU-42")

    def test_runtime_limit_has_distinct_exit_without_input_leak(self) -> None:
        secret = "customer-secret"
        input_path = self.root / "input.txt"
        input_path.write_text(secret, encoding="utf-8")
        core = SimpleNamespace(
            normalise=lambda text: (_ for _ in ()).throw(
                SDKError("LIMIT_EXCEEDED", policy_id="retail-en", limit="input_bytes")
            )
        )
        with patch.object(cli, "build_safety_core", return_value=core):
            code, stdout, stderr = self.run_main(
                [
                    "normalise",
                    "--policy",
                    self.policy,
                    *self.common(),
                    "--input-file",
                    str(input_path),
                ]
            )
        self.assertEqual(code, cli.ExitCode.LIMIT_EXCEEDED)
        self.assertEqual(stdout, "")
        self.assertNotIn(secret, stderr)

    def test_symlink_and_directory_inputs_fail_closed(self) -> None:
        target = self.root / "target.txt"
        target.write_text("secret", encoding="utf-8")
        symlink = self.root / "link.txt"
        symlink.symlink_to(target)
        for unsafe in (symlink, self.root):
            with self.subTest(unsafe=unsafe):
                code, stdout, stderr = self.run_main(
                    [
                        "normalise",
                        "--policy",
                        self.policy,
                        *self.common(),
                        "--input-file",
                        str(unsafe),
                    ]
                )
                self.assertEqual(code, cli.ExitCode.INVALID_INPUT)
                self.assertEqual(stdout, "")
                self.assertEqual(json.loads(stderr)["error"]["code"], "INVALID_FILE")

    def test_input_file_is_bounded_before_sdk_call(self) -> None:
        input_path = self.root / "large.txt"
        input_path.write_bytes(b"abc")
        with patch.object(cli, "build_safety_core") as build:
            code, _, stderr = self.run_main(
                [
                    "normalise",
                    "--policy",
                    self.policy,
                    *self.common(),
                    "--input-file",
                    str(input_path),
                    "--max-input-bytes",
                    "2",
                ]
            )
        self.assertEqual(code, cli.ExitCode.INVALID_INPUT)
        self.assertEqual(json.loads(stderr)["error"]["code"], "DOCUMENT_TOO_LARGE")
        build.assert_not_called()

    def test_raw_text_command_line_option_does_not_exist(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-m", "normguard.cli", "normalise", "--help"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0)
        self.assertNotIn("--text", completed.stdout)
        self.assertIn("--input-file", completed.stdout)
        self.assertIn("--stdin", completed.stdout)


if __name__ == "__main__":
    unittest.main()
