import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADR = ROOT / "docs" / "adr" / "0005-hosted-service-security-boundary.md"
CONTRACT = ROOT / "docs" / "hosted-service-security-boundary.json"
READINESS = ROOT / "docs" / "releases" / "safety-core-phase-3-readiness.json"


class HostedServiceSecurityBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = json.loads(CONTRACT.read_text(encoding="utf-8"))

    def test_contract_has_closed_schema_and_unique_owned_controls(self) -> None:
        self.assertEqual(
            set(self.contract),
            {
                "blocker",
                "closure_requirements",
                "component",
                "mandatory_controls",
                "non_claims",
                "schema_version",
                "threat",
            },
        )
        self.assertEqual(self.contract["schema_version"], 1)
        controls = self.contract["mandatory_controls"]
        self.assertEqual(len(controls), 7)
        self.assertEqual(len({item["id"] for item in controls}), len(controls))
        for item in controls:
            with self.subTest(control=item["id"]):
                self.assertEqual(set(item), {"id", "name", "owner"})
                self.assertRegex(item["id"], r"^HS-\d{2}$")
                self.assertTrue(item["name"])
                self.assertTrue(item["owner"])

    def test_ng_t22_and_b002_remain_blocked_in_contract_and_readiness(self) -> None:
        readiness = json.loads(READINESS.read_text(encoding="utf-8"))
        blockers = {item["id"]: item for item in readiness["blockers"]}
        threats = {item["id"]: item for item in readiness["threat_dispositions"]}

        self.assertEqual(self.contract["blocker"]["id"], "B-002")
        self.assertEqual(self.contract["blocker"]["status"], "BLOCKED")
        self.assertEqual(blockers["B-002"]["status"], "BLOCKED")
        self.assertEqual(self.contract["threat"]["id"], "NG-T22")
        self.assertEqual(
            self.contract["threat"]["status"], "BLOCKED_FOR_HOSTED_SERVICE"
        )
        self.assertEqual(threats["NG-T22"]["status"], "BLOCKED_FOR_HOSTED_SERVICE")

    def test_design_requires_end_to_end_tenant_controls_and_adversarial_tests(self) -> None:
        text = " ".join(ADR.read_text(encoding="utf-8").split()).casefold()
        required = (
            "deny-by-default authorization",
            "Tenant isolation contract",
            "Rate limits and abuse controls",
            "confused-deputy",
            "IDOR/BOLA",
            "cache poisoning",
            "telemetry leakage",
            "Architecture approval alone cannot change either status",
        )
        for value in required:
            with self.subTest(value=value):
                self.assertIn(value.casefold(), text)

    def test_document_contains_no_implemented_service_or_production_claim(self) -> None:
        text = ADR.read_text(encoding="utf-8")
        self.assertIn("This ADR is a design contract only", text)
        self.assertIn("does not implement a hosted endpoint", text)
        self.assertIn("does not close NG-T22 or B-002", CONTRACT.read_text())
        self.assertNotIn("Status: Accepted", text)
        self.assertNotIn("NG-T22 is closed", text)

    def test_markdown_links_resolve(self) -> None:
        text = ADR.read_text(encoding="utf-8")
        targets = re.findall(r"\[[^]]+\]\(([^)#]+)(?:#[^)]+)?\)", text)
        for target in targets:
            with self.subTest(target=target):
                self.assertTrue((ADR.parent / target).resolve().is_file(), target)


if __name__ == "__main__":
    unittest.main()
