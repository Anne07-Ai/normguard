import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from normguard.calibration_diagnostics import build_diagnostics
from normguard.esci import sha256_file


def _canonical(value):
    return (json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ) + "\n").encode()


class CalibrationDiagnosticsTests(unittest.TestCase):
    def _policy(self, path: Path) -> Path:
        path.write_text(json.dumps({
            "schema_version": 1,
            "policy_id": "fixture",
            "policy_version": "0.1.0",
            "rules": [{
                "rule_id": "r1",
                "policy_class": "P0",
                "match_type": "exact",
                "pattern": "Brand",
                "approved_output": None,
                "source": "fixture",
            }],
        }, indent=2, sort_keys=True) + "\n")
        return path

    def _evidence(self, path: Path, policy_sha256: str, partition="calibration") -> Path:
        def query(query_id, source, normalised, score):
            return {
                "query_id": query_id,
                "source_query": source,
                "normalised_query": normalised,
                "hits": [],
                "metrics": {"ndcg_at_10": score},
            }

        report = {
            "schema_version": 1,
            "protocol_id": "NG-POC-001",
            "protocol_version": "0.1.0",
            "partition": partition,
            "baseline_manifest": {"final_test": False},
            "candidate_manifest": {
                "final_test": False,
                "configuration_sha256": policy_sha256,
            },
            "baseline": {"queries": [
                query("q1", "Brand shoes", "brand shoes", 0.4),
                query("q2", "plain socks", "plain socks", 0.5),
                query("q3", "other", "other", 0.7),
            ]},
            "candidate": {"queries": [
                query("q1", "Brand shoes", "brand shoe", 0.6),
                query("q2", "plain socks", "plain sock", 0.4),
                query("q3", "other", "other", 0.7),
            ]},
            "decision_evidence": {
                "baseline_p95_latency_ms": 1.0,
                "candidate_p95_latency_ms": 2.5,
            },
            "decision": "REVIEW",
        }
        path.write_bytes(_canonical({
            "report": report,
            "report_sha256": hashlib.sha256(_canonical(report)).hexdigest(),
        }))
        return path

    def test_outputs_are_reproducible_and_descriptive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = self._policy(root / "policy.json")
            evidence = self._evidence(root / "evidence.json", sha256_file(policy))
            first = build_diagnostics(evidence, policy, root / "first", top_n=1)
            second = build_diagnostics(evidence, policy, root / "second", top_n=1)
            self.assertEqual(first, second)
            self.assertEqual(first["query_movement"]["improved"], 1)
            self.assertEqual(first["query_movement"]["regressed"], 1)
            self.assertEqual(first["query_movement"]["unchanged"], 1)
            self.assertEqual(first["policy_coverage"]["covered_queries"], 1)
            self.assertEqual(
                first["latency"]["component_attribution"]["status"], "not-instrumented"
            )
            self.assertEqual(
                (root / "first" / "calibration-diagnostics.json").read_bytes(),
                (root / "second" / "calibration-diagnostics.json").read_bytes(),
            )

    def test_development_and_policy_digest_mismatch_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = self._policy(root / "policy.json")
            development = self._evidence(
                root / "development.json", sha256_file(policy), "development"
            )
            with self.assertRaisesRegex(ValueError, "calibration evidence only"):
                build_diagnostics(development, policy, root / "output")

            mismatch = self._evidence(root / "mismatch.json", "a" * 64)
            with self.assertRaisesRegex(ValueError, "policy digest"):
                build_diagnostics(mismatch, policy, root / "output")

    def test_tampering_and_invalid_top_n_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = self._policy(root / "policy.json")
            evidence = self._evidence(root / "evidence.json", sha256_file(policy))
            payload = json.loads(evidence.read_text())
            payload["report"]["decision"] = "PASS"
            evidence.write_text(json.dumps(payload))
            with self.assertRaisesRegex(ValueError, "digest verification"):
                build_diagnostics(evidence, policy, root / "output")
            with self.assertRaisesRegex(ValueError, "top_n"):
                build_diagnostics(evidence, policy, root / "output", top_n=0)

    def test_zero_policy_coverage_has_explicit_zero_rate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = self._policy(root / "policy.json")
            evidence = self._evidence(root / "evidence.json", sha256_file(policy))
            payload = json.loads(policy.read_text())
            payload["rules"][0]["pattern"] = "never-observed"
            policy.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
            evidence = self._evidence(root / "evidence.json", sha256_file(policy))
            result = build_diagnostics(evidence, policy, root / "output")
            self.assertEqual(result["policy_coverage"]["covered_queries"], 0)
            self.assertEqual(result["policy_coverage"]["coverage_rate"], 0.0)
            self.assertEqual(result["policy_class_effects"], {})


if __name__ == "__main__":
    unittest.main()
