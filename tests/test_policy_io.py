import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from normguard.policy_io import load_policy, require_development_approval


def _write(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _policy() -> dict[str, object]:
    return {
        "schema_version": 1,
        "policy_id": "retail-en-v1",
        "policy_version": "1.0.0",
        "rules": [{
            "rule_id": "sku",
            "policy_class": "P0",
            "match_type": "regex",
            "pattern": "SKU-[A-Z0-9]+",
            "approved_output": None,
            "source": "reviewed retail terminology register",
        }],
    }


class PolicyInputTests(unittest.TestCase):
    def test_policy_digest_and_rules_are_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = _write(Path(directory) / "policy.json", _policy())
            policy = load_policy(path)
            self.assertEqual(policy.sha256, hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertEqual(policy.rules[0].rule_id, "sku")

    def test_unknown_policy_keys_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            payload = _policy() | {"unreviewed_option": True}
            path = _write(Path(directory) / "policy.json", payload)
            with self.assertRaisesRegex(ValueError, "unknown"):
                load_policy(path)

    def test_development_requires_two_reviewers_and_matching_policy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            approval = {
                "schema_version": 1,
                "partition": "calibration",
                "status": "approved",
                "policy_sha256": "a" * 64,
                "evidence_sha256": "b" * 64,
                "reviewers": ["reviewer-one", "reviewer-two"],
            }
            path = _write(Path(directory) / "approval.json", approval)
            self.assertEqual(require_development_approval(path, "a" * 64), approval)
            approval["reviewers"] = ["reviewer-one"]
            _write(path, approval)
            with self.assertRaisesRegex(ValueError, "two distinct"):
                require_development_approval(path, "a" * 64)

    def test_approval_for_different_policy_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = _write(Path(directory) / "approval.json", {
                "schema_version": 1,
                "partition": "calibration",
                "status": "approved",
                "policy_sha256": "a" * 64,
                "evidence_sha256": "b" * 64,
                "reviewers": ["reviewer-one", "reviewer-two"],
            })
            with self.assertRaisesRegex(ValueError, "does not match"):
                require_development_approval(path, "c" * 64)


if __name__ == "__main__":
    unittest.main()
