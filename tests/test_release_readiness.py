import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECORD = ROOT / "docs" / "releases" / "safety-core-phase-3-readiness.json"
REPORT = ROOT / "docs" / "releases" / "safety-core-phase-3-readiness.md"


class ReleaseReadinessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.record = json.loads(RECORD.read_text(encoding="utf-8"))

    def test_closed_top_level_schema_and_decision(self) -> None:
        self.assertEqual(
            set(self.record),
            {
                "approved_scope",
                "blockers",
                "component",
                "decision",
                "gates",
                "non_claims",
                "prohibited_scope",
                "residual_risks",
                "review_date",
                "schema_version",
                "sealed_data_status",
                "threat_dispositions",
                "version_candidate",
            },
        )
        self.assertEqual(self.record["schema_version"], 1)
        self.assertEqual(self.record["decision"], "READY_FOR_CONTROLLED_PILOT")
        self.assertEqual(self.record["review_date"], "2026-10-23")
        self.assertEqual(
            self.record["sealed_data_status"],
            "development-and-official-test-not-read-or-transformed",
        )
        report = REPORT.read_text(encoding="utf-8")
        self.assertIn(f"**Decision:** `{self.record['decision']}`", report)

    def test_all_gates_have_unique_ids_owners_pass_status_and_existing_evidence(self) -> None:
        gates = self.record["gates"]
        self.assertEqual(len({gate["id"] for gate in gates}), len(gates))
        self.assertGreaterEqual(len(gates), 10)
        for gate in gates:
            with self.subTest(gate=gate["id"]):
                self.assertEqual(
                    set(gate), {"evidence", "id", "name", "owner", "status"}
                )
                self.assertEqual(gate["status"], "PASS")
                self.assertTrue(gate["owner"])
                self.assertTrue(gate["evidence"])
                for evidence in gate["evidence"]:
                    self.assertTrue((ROOT / evidence).is_file(), evidence)

    def test_every_threat_has_exactly_one_disposition_and_t22_is_blocked(self) -> None:
        dispositions = self.record["threat_dispositions"]
        expected = {f"NG-T{number:02d}" for number in range(1, 25)}
        self.assertEqual({item["id"] for item in dispositions}, expected)
        self.assertEqual(len(dispositions), 24)
        statuses = {item["id"]: item["status"] for item in dispositions}
        self.assertEqual(statuses["NG-T22"], "BLOCKED_FOR_HOSTED_SERVICE")

    def test_blockers_and_residual_risks_have_named_owners(self) -> None:
        self.assertGreaterEqual(len(self.record["blockers"]), 3)
        self.assertGreaterEqual(len(self.record["residual_risks"]), 5)
        for collection in ("blockers", "residual_risks"):
            for item in self.record[collection]:
                with self.subTest(collection=collection, item=item["id"]):
                    self.assertRegex(item["id"], r"^[BR]-\d{3}$")
                    self.assertTrue(item["owner"])
                    self.assertTrue(item["summary"])

    def test_report_links_resolve_and_readme_links_decision(self) -> None:
        text = REPORT.read_text(encoding="utf-8")
        targets = re.findall(r"\[[^]]+\]\(([^)#]+)(?:#[^)]+)?\)", text)
        for target in targets:
            with self.subTest(target=target):
                self.assertTrue((REPORT.parent / target).resolve().is_file())
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("docs/releases/safety-core-phase-3-readiness.md", readme)


if __name__ == "__main__":
    unittest.main()
