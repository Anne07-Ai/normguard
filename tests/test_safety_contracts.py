import json
import unittest

from normguard.contracts import PolicyClass, PolicyRule
from normguard.safety_contracts import (
    DecisionEvidence,
    EvidenceSpan,
    EvidenceStatus,
    PolicyBundle,
    PolicyPayload,
    PolicyProvenance,
    ReviewerApproval,
    SchemaCompatibility,
    canonical_json_bytes,
    policy_bundle_from_dict,
)


def payload() -> PolicyPayload:
    return PolicyPayload(
        policy_id="retail-en",
        policy_version="1.0.0",
        compatibility=SchemaCompatibility(1, 1),
        provenance=PolicyProvenance("review-register", "2026.10", "2026-10-21T13:00:00Z"),
        rules=(
            PolicyRule("sku", PolicyClass.EXACT_PRESERVE, "exact", "SKU-42"),
            PolicyRule(
                "alias",
                PolicyClass.APPROVED_ALIAS,
                "exact",
                "ACME LTD",
                approved_output="Acme",
            ),
        ),
    )


def bundle() -> PolicyBundle:
    policy = payload()
    approvals = tuple(
        ReviewerApproval(name, "reviewer", "2026-10-21T13:01:00Z", policy.sha256)
        for name in ("reviewer-one", "reviewer-two")
    )
    return PolicyBundle(policy, policy.sha256, approvals, "1.0.0", None)


class SafetyPolicyContractTests(unittest.TestCase):
    def test_canonical_bytes_are_order_independent(self) -> None:
        left = {"z": 1, "a": ["é", 2]}
        right = {"a": ["é", 2], "z": 1}
        self.assertEqual(canonical_json_bytes(left), canonical_json_bytes(right))

    def test_bundle_round_trip_is_byte_reproducible(self) -> None:
        original = bundle()
        loaded = policy_bundle_from_dict(json.loads(original.canonical_bytes()))
        self.assertEqual(original.canonical_bytes(), loaded.canonical_bytes())
        self.assertFalse(loaded.publication_ready)

    def test_tampered_payload_digest_fails_closed(self) -> None:
        value = bundle().to_dict()
        value["payload"]["policy_version"] = "1.0.1"
        with self.assertRaisesRegex(ValueError, "digest mismatch"):
            policy_bundle_from_dict(value)

    def test_unknown_fields_fail_closed_at_nested_levels(self) -> None:
        value = bundle().to_dict()
        value["payload"]["rules"][0]["unsafe"] = True
        with self.assertRaisesRegex(ValueError, "unknown"):
            policy_bundle_from_dict(value)

    def test_duplicate_rule_ids_are_rejected(self) -> None:
        first = payload().rules[0]
        with self.assertRaisesRegex(ValueError, "unique"):
            PolicyPayload(
                "retail-en",
                "1.0.0",
                SchemaCompatibility(1, 1),
                PolicyProvenance("review-register", "1", "2026-10-21T13:00:00Z"),
                (first, first),
            )

    def test_two_distinct_digest_bound_reviewers_are_required(self) -> None:
        policy = payload()
        one = ReviewerApproval(
            "reviewer-one", "reviewer", "2026-10-21T13:01:00Z", policy.sha256
        )
        with self.assertRaisesRegex(ValueError, "two reviewer"):
            PolicyBundle(policy, policy.sha256, (one,), "1.0.0", None)
        duplicate = (one, one)
        with self.assertRaisesRegex(ValueError, "distinct"):
            PolicyBundle(policy, policy.sha256, duplicate, "1.0.0", None)
        wrong = ReviewerApproval(
            "reviewer-two", "reviewer", "2026-10-21T13:01:00Z", "f" * 64
        )
        with self.assertRaisesRegex(ValueError, "does not match"):
            PolicyBundle(policy, policy.sha256, (one, wrong), "1.0.0", None)

    def test_incompatible_runtime_schema_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "incompatible"):
            PolicyPayload(
                "retail-en",
                "1.0.0",
                SchemaCompatibility(2, 3),
                PolicyProvenance("review-register", "1", "2026-10-21T13:00:00Z"),
                (payload().rules[0],),
            )

    def test_invalid_semver_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "semantic"):
            PolicyPayload(
                "retail-en",
                "v1",
                SchemaCompatibility(1, 1),
                PolicyProvenance("review-register", "1", "2026-10-21T13:00:00Z"),
                (payload().rules[0],),
            )


class SafetyEvidenceContractTests(unittest.TestCase):
    def test_evidence_is_privacy_safe_and_deterministic(self) -> None:
        evidence = DecisionEvidence(
            decision_id="decision-001",
            runtime_version="0.1.0",
            policy_id="retail-en",
            policy_version="1.0.0",
            policy_sha256="a" * 64,
            input_sha256="b" * 64,
            input_bytes=17,
            status=EvidenceStatus.SUCCESS,
            spans=(EvidenceSpan("sku", 0, 6, PolicyClass.EXACT_PRESERVE, "preserved"),),
            safety_events=(),
            duration_ns=1200,
        )
        first = evidence.canonical_bytes()
        second = evidence.canonical_bytes()
        self.assertEqual(first, second)
        decoded = json.loads(first)
        self.assertNotIn("input_text", decoded)
        self.assertNotIn("output_text", decoded)
        self.assertNotIn("text", decoded["spans"][0])

    def test_invalid_evidence_digest_and_offsets_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            DecisionEvidence(
                "decision-001",
                "0.1.0",
                "retail-en",
                "1.0.0",
                "bad",
                "b" * 64,
                1,
                EvidenceStatus.FAILED,
                (),
                ("invalid-policy",),
                1,
            )
        with self.assertRaisesRegex(ValueError, "offsets"):
            EvidenceSpan("sku", 4, 4, PolicyClass.EXACT_PRESERVE, "preserved")


if __name__ == "__main__":
    unittest.main()
