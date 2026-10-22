import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from normguard.contracts import PolicyClass, PolicyRule
from normguard.policy_verification import VerifiedPolicy
from normguard.runtime import (
    BoundedSafetyRuntime,
    RuntimeCode,
    RuntimeFailure,
    RuntimeLimit,
    RuntimeLimits,
)
from normguard.safety_contracts import (
    EvidenceStatus,
    PolicyPayload,
    PolicyProvenance,
    SchemaCompatibility,
)


def _verified(*rules: PolicyRule, version: str = "1.0.0") -> VerifiedPolicy:
    payload = PolicyPayload(
        policy_id="retail-en",
        policy_version=version,
        compatibility=SchemaCompatibility(1, 1),
        provenance=PolicyProvenance(
            "review-register",
            "2026.10",
            "2026-10-22T09:00:00Z",
        ),
        rules=rules,
    )
    return VerifiedPolicy(
        payload=payload,
        payload_sha256=payload.sha256,
        signing_key_id="policy-key",
        signing_key_sha256="a" * 64,
        rollback_authorized=False,
        verification_time=datetime(2026, 10, 22, 10, 0, tzinfo=UTC),
    )


def _limits(**changes: int) -> RuntimeLimits:
    values = {
        "max_input_bytes": 100,
        "max_rules": 10,
        "max_pattern_characters": 100,
        "max_matches": 10,
        "max_output_bytes": 100,
        "max_evidence_spans": 20,
        "max_safety_events": 10,
    }
    values.update(changes)
    return RuntimeLimits(**values)


class RuntimeLimitContractTests(unittest.TestCase):
    def test_limits_are_positive_integers_and_immutable(self) -> None:
        for invalid in (0, -1, True, 1.5):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    RuntimeLimits(max_input_bytes=invalid)  # type: ignore[arg-type]
        limits = RuntimeLimits()
        with self.assertRaises(AttributeError):
            limits.max_rules = 1  # type: ignore[misc]

    def test_runtime_requires_verified_policy_and_is_immutable(self) -> None:
        rule = PolicyRule("sku", PolicyClass.EXACT_PRESERVE, "exact", "SKU-42")
        with self.assertRaises(TypeError):
            BoundedSafetyRuntime(_verified(rule).payload)  # type: ignore[arg-type]
        runtime = BoundedSafetyRuntime(_verified(rule))
        with self.assertRaises(AttributeError):
            runtime._limits = RuntimeLimits()  # type: ignore[misc]

    def test_rule_count_boundary_and_one_over(self) -> None:
        rules = tuple(
            PolicyRule(f"rule-{index}", PolicyClass.EXACT_PRESERVE, "exact", f"SKU-{index}")
            for index in range(2)
        )
        BoundedSafetyRuntime(_verified(*rules), limits=_limits(max_rules=2))
        with self.assertRaises(RuntimeFailure) as caught:
            BoundedSafetyRuntime(_verified(*rules), limits=_limits(max_rules=1))
        self.assertEqual(caught.exception.limit, RuntimeLimit.RULE_COUNT)

    def test_pattern_length_boundary_and_one_over(self) -> None:
        rule = PolicyRule("sku", PolicyClass.EXACT_PRESERVE, "exact", "ABC")
        BoundedSafetyRuntime(
            _verified(rule),
            limits=_limits(max_pattern_characters=3),
        )
        with self.assertRaises(RuntimeFailure) as caught:
            BoundedSafetyRuntime(
                _verified(rule),
                limits=_limits(max_pattern_characters=2),
            )
        self.assertEqual(caught.exception.limit, RuntimeLimit.PATTERN_CHARACTERS)

    def test_input_byte_boundary_and_one_over(self) -> None:
        runtime = BoundedSafetyRuntime(
            _verified(PolicyRule("x", PolicyClass.EXACT_PRESERVE, "exact", "x")),
            limits=_limits(max_input_bytes=2),
        )
        runtime.normalise("é")
        with self.assertRaises(RuntimeFailure) as caught:
            runtime.normalise("éa")
        self.assertEqual(caught.exception.limit, RuntimeLimit.INPUT_BYTES)

    def test_invalid_input_is_typed_and_does_not_echo_content(self) -> None:
        runtime = BoundedSafetyRuntime(
            _verified(PolicyRule("x", PolicyClass.EXACT_PRESERVE, "exact", "x"))
        )
        with self.assertRaises(RuntimeFailure) as caught:
            runtime.normalise(b"secret")  # type: ignore[arg-type]
        self.assertEqual(caught.exception.code, RuntimeCode.INVALID_INPUT)
        self.assertNotIn("secret", str(caught.exception))


class BoundedRuntimeBehaviourTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = BoundedSafetyRuntime(
            _verified(
                PolicyRule("sku", PolicyClass.EXACT_PRESERVE, "exact", "SKU-42"),
                PolicyRule(
                    "alias",
                    PolicyClass.APPROVED_ALIAS,
                    "exact",
                    "ACME LTD",
                    approved_output="Acme",
                ),
                PolicyRule(
                    "review",
                    PolicyClass.REVIEW_OR_ABSTAIN,
                    "exact",
                    "unclear",
                ),
                PolicyRule(
                    "bounded-p2",
                    PolicyClass.CONTEXT_NORMALISABLE,
                    "exact",
                    "shoes",
                ),
            ),
            limits=_limits(),
        )

    def test_p0_p1_p2_and_p3_semantics(self) -> None:
        result = self.runtime.normalise("SKU-42 ACME LTD shoes unclear")
        self.assertEqual(result.output_text, "SKU-42 Acme shoes unclear")
        self.assertEqual(result.status, EvidenceStatus.REVIEW_REQUIRED)
        protected = {span.rule_id: span for span in result.evidence.spans}
        self.assertEqual(protected["sku"].action, "preserved")
        self.assertEqual(protected["alias"].action, "transformed")
        self.assertEqual(protected["bounded-p2"].action, "preserved")
        self.assertEqual(protected["review"].action, "review")

    def test_semantic_result_and_evidence_are_deterministic(self) -> None:
        first = self.runtime.normalise("SKU-42 ACME LTD")
        second = self.runtime.normalise("SKU-42 ACME LTD")
        self.assertEqual(first, second)
        self.assertEqual(first.evidence.duration_ns, 0)

    def test_evidence_is_privacy_safe(self) -> None:
        secret = "customer-secret"
        result = self.runtime.normalise(secret)
        encoded = result.evidence.canonical_bytes()
        decoded = json.loads(encoded)
        self.assertNotIn(secret.encode(), encoded)
        self.assertNotIn("input_text", decoded)
        self.assertNotIn("output_text", decoded)
        self.assertEqual(decoded["input_bytes"], len(secret.encode()))

    def test_match_limit_returns_no_partial_result(self) -> None:
        runtime = BoundedSafetyRuntime(
            _verified(PolicyRule("sku", PolicyClass.EXACT_PRESERVE, "exact", "SKU")),
            limits=_limits(max_matches=1),
        )
        with self.assertRaises(RuntimeFailure) as caught:
            runtime.normalise("SKU SKU")
        self.assertEqual(caught.exception.code, RuntimeCode.LIMIT_EXCEEDED)
        self.assertEqual(caught.exception.limit, RuntimeLimit.MATCH_COUNT)

    def test_output_byte_boundary_and_one_over(self) -> None:
        exact = BoundedSafetyRuntime(
            _verified(PolicyRule("x", PolicyClass.EXACT_PRESERVE, "exact", "x")),
            limits=_limits(max_output_bytes=2),
        )
        self.assertEqual(exact.normalise("é").output_text, "é")
        over = BoundedSafetyRuntime(
            _verified(PolicyRule("x", PolicyClass.EXACT_PRESERVE, "exact", "x")),
            limits=_limits(max_output_bytes=1),
        )
        with self.assertRaises(RuntimeFailure) as caught:
            over.normalise("é")
        self.assertEqual(caught.exception.limit, RuntimeLimit.OUTPUT_BYTES)

    def test_evidence_span_boundary_and_one_over(self) -> None:
        rule = PolicyRule("x", PolicyClass.EXACT_PRESERVE, "exact", "protected")
        exact = BoundedSafetyRuntime(
            _verified(rule),
            limits=_limits(max_evidence_spans=2),
        )
        self.assertEqual(len(exact.normalise("word protected").evidence.spans), 2)
        over = BoundedSafetyRuntime(
            _verified(rule),
            limits=_limits(max_evidence_spans=1),
        )
        with self.assertRaises(RuntimeFailure) as caught:
            over.normalise("word protected")
        self.assertEqual(caught.exception.limit, RuntimeLimit.EVIDENCE_SPANS)

    def test_runtime_is_safe_for_concurrent_read_only_calls(self) -> None:
        inputs = ["SKU-42 ACME LTD", "shoes", "unclear", "plain text"] * 8
        expected = [self.runtime.normalise(item) for item in inputs]
        with ThreadPoolExecutor(max_workers=8) as pool:
            observed = list(pool.map(self.runtime.normalise, inputs))
        self.assertEqual(observed, expected)


if __name__ == "__main__":
    unittest.main()
