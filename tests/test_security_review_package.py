import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "docs" / "security-review"
READINESS = ROOT / "docs" / "releases" / "safety-core-phase-3-readiness.json"


class SecurityReviewPackageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.index = json.loads((PACKAGE / "evidence-index.json").read_text())
        self.schema = json.loads((PACKAGE / "review-record.schema.json").read_text())
        self.template = json.loads((PACKAGE / "review-record.template.json").read_text())
        self.readiness = json.loads(READINESS.read_text())

    def test_evidence_index_covers_every_threat_with_existing_paths(self) -> None:
        self.assertEqual(
            set(self.index),
            {"component", "decision_boundary", "schema_version", "threats"},
        )
        expected = {f"NG-T{number:02d}" for number in range(1, 25)}
        self.assertEqual({item["id"] for item in self.index["threats"]}, expected)
        self.assertEqual(len(self.index["threats"]), 24)
        for item in self.index["threats"]:
            with self.subTest(threat=item["id"]):
                self.assertEqual(set(item), {"id", "disposition", "evidence"})
                self.assertTrue(item["evidence"])
                for path in item["evidence"]:
                    self.assertTrue((ROOT / path).is_file(), path)

    def test_ng_t22_and_readiness_blockers_remain_open(self) -> None:
        threats = {item["id"]: item for item in self.index["threats"]}
        self.assertEqual(threats["NG-T22"]["disposition"], "BLOCKED_FOR_HOSTED_SERVICE")
        blockers = {item["id"]: item for item in self.readiness["blockers"]}
        self.assertEqual(blockers["B-001"]["status"], "OPEN")
        self.assertEqual(blockers["B-002"]["status"], "BLOCKED")
        self.assertEqual(self.readiness["decision"], "CONTROLLED_PILOT_COMPLETED")

    def test_schema_and_template_are_closed_and_non_attesting(self) -> None:
        self.assertFalse(self.schema["additionalProperties"])
        self.assertEqual(set(self.template), set(self.schema["required"]))
        self.assertEqual(self.template["review_status"], "NOT_STARTED")
        self.assertEqual(self.template["reviewers"], [])
        self.assertEqual(len(self.template["threat_coverage"]), 24)
        self.assertEqual(
            {item["outcome"] for item in self.template["threat_coverage"]},
            {"NOT_VERIFIED"},
        )
        self.assertIsNone(self.template["conclusion"])
        self.assertIsNone(self.template["sign_off"])
        self.assertIsNone(self.template["scope"]["source_commit"])
        self.assertIsNone(self.template["scope"]["artifact_sha256"])

    def test_nested_record_objects_reject_unknown_fields(self) -> None:
        properties = self.schema["properties"]
        self.assertFalse(properties["scope"]["additionalProperties"])
        reviewer = properties["reviewers"]["items"]
        coverage = properties["threat_coverage"]["items"]
        finding = properties["findings"]["items"]
        self.assertFalse(reviewer["additionalProperties"])
        self.assertFalse(coverage["additionalProperties"])
        self.assertTrue(properties["threat_coverage"]["uniqueItems"])
        required_coverage = properties["threat_coverage"]["allOf"]
        self.assertEqual(len(required_coverage), 24)
        self.assertEqual(
            {
                item["contains"]["properties"]["threat_id"]["const"]
                for item in required_coverage
            },
            {f"NG-T{number:02d}" for number in range(1, 25)},
        )
        self.assertFalse(finding["additionalProperties"])
        acceptance = finding["properties"]["risk_acceptance"]["oneOf"][1]
        sign_off = properties["sign_off"]["oneOf"][1]
        self.assertFalse(acceptance["additionalProperties"])
        self.assertFalse(sign_off["additionalProperties"])

    def test_guide_links_resolve_and_forbids_self_attestation(self) -> None:
        guide = (PACKAGE / "README.md").read_text()
        targets = re.findall(r"\[[^]]+\]\(([^)#]+)(?:#[^)]+)?\)", guide)
        for target in targets:
            self.assertTrue((PACKAGE / target).resolve().is_file(), target)
        normalized = " ".join(guide.split()).casefold()
        for phrase in (
            "not a completed review",
            "must not populate the reviewer identity",
            "blocker b-001 remains `open`",
            "ng-t22 remains `blocked_for_hosted_service`",
        ):
            self.assertIn(phrase, normalized)


if __name__ == "__main__":
    unittest.main()
