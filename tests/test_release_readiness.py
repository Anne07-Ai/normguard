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
        self.assertEqual(self.record["decision"], "CONTROLLED_PILOT_COMPLETED")
        self.assertEqual(self.record["review_date"], "2026-11-09")
        self.assertEqual(
            self.record["sealed_data_status"],
            "development-and-official-test-not-read-or-transformed",
        )
        report = REPORT.read_text(encoding="utf-8")
        self.assertIn(f"**Decision:** `{self.record['decision']}`", report)

    def test_all_gates_have_unique_ids_owners_pass_status_and_existing_evidence(self) -> None:
        gates = self.record["gates"]
        self.assertEqual(len({gate["id"] for gate in gates}), len(gates))
        self.assertGreaterEqual(len(gates), 11)
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

    def test_blocker_preparation_evidence_exists_without_claiming_closure(self) -> None:
        expected = {
            "B-001": "docs/security-review/review-record.schema.json",
            "B-002": "docs/hosted-service-security-boundary.json",
            "B-003": "src/normguard/capacity.py",
        }
        blockers = {item["id"]: item for item in self.record["blockers"]}
        for blocker_id, required_path in expected.items():
            with self.subTest(blocker=blocker_id):
                blocker = blockers[blocker_id]
                self.assertEqual(
                    set(blocker),
                    {
                        "closure_evidence",
                        "id",
                        "owner",
                        "preparation_evidence",
                        "required_for",
                        "status",
                        "summary",
                    },
                )
                self.assertTrue(blocker["closure_evidence"])
                self.assertIn(required_path, blocker["preparation_evidence"])
                for evidence in blocker["preparation_evidence"]:
                    self.assertTrue((ROOT / evidence).is_file(), evidence)

        self.assertEqual(blockers["B-001"]["status"], "OPEN")
        self.assertEqual(blockers["B-002"]["status"], "BLOCKED")
        self.assertEqual(blockers["B-003"]["status"], "OPEN")

    def test_pilot_gate_exists_without_closing_production_blockers(self) -> None:
        gates = {gate["id"]: gate for gate in self.record["gates"]}
        self.assertEqual(gates["G-11"]["name"], "synthetic controlled pilot execution")
        self.assertEqual(gates["G-11"]["status"], "PASS")

        blockers = {item["id"]: item for item in self.record["blockers"]}
        self.assertEqual(set(blockers), {"B-001", "B-002", "B-003"})
        self.assertEqual(blockers["B-001"]["status"], "OPEN")
        self.assertEqual(blockers["B-002"]["status"], "BLOCKED")
        self.assertEqual(blockers["B-003"]["status"], "OPEN")
        self.assertEqual(
            blockers["B-001"]["required_for"],
            "external production-readiness claim",
        )
        self.assertEqual(
            blockers["B-002"]["required_for"],
            "hosted or multi-tenant service",
        )
        self.assertEqual(
            blockers["B-003"]["required_for"],
            "production SLA or capacity claim",
        )

    def test_report_preserves_non_claims_and_independent_review_boundary(self) -> None:
        report = REPORT.read_text(encoding="utf-8")
        normalized_report = " ".join(report.split())
        for required in (
            "not independent assurance",
            "not “production ready,” independently certified",
            "NG-T22 remains an explicit blocker",
            "The synthetic pilot did not close or change",
        ):
            self.assertIn(required, normalized_report)

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
