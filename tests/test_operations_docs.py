import re
import unittest
from pathlib import Path

from normguard.cli import build_parser

ROOT = Path(__file__).resolve().parents[1]
RUNBOOK = ROOT / "docs" / "policy-operations-runbook.md"
CHECKLIST = ROOT / "docs" / "checklists" / "policy-release-rollback.md"


class OperationsDocumentationTests(unittest.TestCase):
    def test_required_operations_documents_exist_and_are_linked(self) -> None:
        self.assertTrue(RUNBOOK.is_file())
        self.assertTrue(CHECKLIST.is_file())
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("docs/policy-operations-runbook.md", readme)
        self.assertIn("checklists/policy-release-rollback.md", RUNBOOK.read_text())

    def test_runbook_relative_markdown_links_resolve(self) -> None:
        text = RUNBOOK.read_text(encoding="utf-8")
        targets = re.findall(r"\[[^]]+\]\(([^)#]+)(?:#[^)]+)?\)", text)
        self.assertGreater(len(targets), 3)
        for target in targets:
            with self.subTest(target=target):
                self.assertTrue((RUNBOOK.parent / target).resolve().is_file())

    def test_documented_standard_and_rollback_verification_commands_parse(self) -> None:
        parser = build_parser()
        standard = [
            "policy",
            "verify",
            "policy.json",
            "--trust-store",
            "trust-store.json",
            "--activation-state",
            "activation-state.json",
            "--verification-time",
            "2026-10-23T09:00:00Z",
            "--json",
        ]
        rollback = [
            *standard[:-1],
            "--rollback-authorization",
            "rollback-authorization.json",
            "--rollback-trust-store",
            "rollback-trust-store.json",
            "--json",
        ]
        self.assertEqual(parser.parse_args(standard).policy_command, "verify")
        parsed = parser.parse_args(rollback)
        self.assertEqual(parsed.rollback_authorization, "rollback-authorization.json")
        self.assertEqual(parsed.rollback_trust_store, "rollback-trust-store.json")

    def test_examples_exclude_private_key_and_customer_content_placeholders(self) -> None:
        combined = RUNBOOK.read_text(encoding="utf-8") + CHECKLIST.read_text(
            encoding="utf-8"
        )
        forbidden = ("BEGIN PRIVATE KEY", "customer query", "customer output")
        for value in forbidden:
            with self.subTest(value=value):
                self.assertNotIn(value, combined)


if __name__ == "__main__":
    unittest.main()
