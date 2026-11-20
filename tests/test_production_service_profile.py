import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADR = ROOT / "docs" / "adr" / "0006-production-hosted-service-profile.md"
PROFILE = ROOT / "docs" / "production-hosted-service-profile.json"


class ProductionServiceProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = json.loads(PROFILE.read_text(encoding="utf-8"))

    def test_profile_is_closed_and_freezes_one_target(self) -> None:
        self.assertEqual(
            set(self.profile),
            {
                "blockers",
                "controls",
                "data_handling",
                "identity",
                "non_claims",
                "profile_id",
                "profile_version",
                "schema_version",
                "service",
                "slo_targets",
                "status",
                "threat",
            },
        )
        self.assertEqual(self.profile["schema_version"], 1)
        self.assertEqual(self.profile["profile_version"], "1.0.0")
        self.assertEqual(self.profile["service"]["region"], "eu-west-2")
        self.assertEqual(self.profile["service"]["runtime"], "ECS Fargate")
        self.assertEqual(self.profile["service"]["tenancy"], "multi-tenant")
        self.assertEqual(self.profile["status"], "PROPOSED_NOT_IMPLEMENTED")

    def test_ng_t22_and_all_blockers_remain_open_or_blocked(self) -> None:
        self.assertEqual(
            self.profile["blockers"],
            {"B-001": "OPEN", "B-002": "BLOCKED", "B-003": "OPEN"},
        )
        self.assertEqual(
            self.profile["threat"],
            {"id": "NG-T22", "status": "BLOCKED_FOR_HOSTED_SERVICE"},
        )

    def test_controls_map_owner_enforcement_and_required_evidence(self) -> None:
        controls = self.profile["controls"]
        self.assertEqual([item["id"] for item in controls], [f"HS-{n:02d}" for n in range(1, 8)])
        required = {
            "evidence_required",
            "enforcement_point",
            "id",
            "name",
            "owner",
        }
        for control in controls:
            with self.subTest(control=control["id"]):
                self.assertEqual(set(control), required)
                for key in required:
                    self.assertTrue(control[key])

    def test_profile_prohibits_content_persistence_and_client_tenant_authority(self) -> None:
        self.assertEqual(self.profile["service"]["content_state"], "in-memory-only")
        self.assertEqual(self.profile["data_handling"]["content_persistence"], "prohibited")
        self.assertEqual(self.profile["data_handling"]["raw_content_telemetry"], "prohibited")
        self.assertFalse(self.profile["identity"]["effective_tenant_from_client_input"])

    def test_targets_are_not_claimed_as_achieved(self) -> None:
        text = " ".join(ADR.read_text(encoding="utf-8").split()).casefold()
        self.assertIn("objectives for readiness testing, not achieved slo or sla claims", text)
        self.assertIn("does not implement the service", text)
        self.assertIn("does not implement a hosted service", " ".join(self.profile["non_claims"]))
        self.assertNotIn("production ready", text)

    def test_markdown_links_resolve(self) -> None:
        text = ADR.read_text(encoding="utf-8")
        targets = re.findall(r"\[[^]]+\]\(([^)#]+)(?:#[^)]+)?\)", text)
        for target in targets:
            with self.subTest(target=target):
                self.assertTrue((ADR.parent / target).resolve().is_file(), target)


if __name__ == "__main__":
    unittest.main()
