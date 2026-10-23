import hashlib
import itertools
import json
import unittest
from datetime import UTC, datetime
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from normguard.contracts import PolicyClass, PolicyRule
from normguard.policy_verification import VerifiedPolicy
from normguard.runtime import BoundedSafetyRuntime, RuntimeCode, RuntimeFailure, RuntimeLimits
from normguard.safety_contracts import (
    PolicyPayload,
    PolicyProvenance,
    SchemaCompatibility,
    canonical_json_bytes,
)
from normguard.sdk import SDKError, verify_policy

PROPERTY_SETTINGS = settings(
    max_examples=100,
    derandomize=True,
    database=None,
    deadline=None,
    suppress_health_check=(HealthCheck.too_slow,),
)
UNICODE = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",)),
    min_size=0,
    max_size=80,
)


def _verified(*rules: PolicyRule) -> VerifiedPolicy:
    payload = PolicyPayload(
        policy_id="adversarial-synthetic",
        policy_version="1.0.0",
        compatibility=SchemaCompatibility(1, 1),
        provenance=PolicyProvenance(
            "synthetic-adversarial",
            "1",
            "2026-10-23T00:00:00Z",
        ),
        rules=rules,
    )
    return VerifiedPolicy(
        payload=payload,
        payload_sha256=payload.sha256,
        signing_key_id="synthetic-key",
        signing_key_sha256="a" * 64,
        rollback_authorized=False,
        verification_time=datetime(2026, 10, 23, tzinfo=UTC),
    )


class UnicodeAndDeterminismProperties(unittest.TestCase):
    """NG-T08, NG-T09 and NG-T17 generated runtime properties."""

    @PROPERTY_SETTINGS
    @given(UNICODE)
    def test_generated_unicode_is_semantically_deterministic(self, text: str) -> None:
        runtime = BoundedSafetyRuntime(
            _verified(PolicyRule("sku", PolicyClass.EXACT_PRESERVE, "exact", "SKU-42"))
        )
        self.assertEqual(runtime.normalise(text), runtime.normalise(text))

    @PROPERTY_SETTINGS
    @given(UNICODE, UNICODE)
    def test_p0_offsets_and_retention_survive_unicode_context(
        self, prefix: str, suffix: str
    ) -> None:
        runtime = BoundedSafetyRuntime(
            _verified(PolicyRule("sku", PolicyClass.EXACT_PRESERVE, "exact", "SKU-42"))
        )
        separator = " "
        text = f"{prefix}{separator}SKU-42{separator}{suffix}"
        result = runtime.normalise(text)
        protected = [span for span in result.evidence.spans if span.rule_id == "sku"]
        self.assertEqual(len(protected), 1)
        span = protected[0]
        self.assertEqual(text[span.start : span.end], "SKU-42")
        self.assertIn("SKU-42", result.output_text)

    def test_unpaired_surrogate_fails_with_typed_private_error(self) -> None:
        secret = "customer-\ud800-secret"
        runtime = BoundedSafetyRuntime(
            _verified(PolicyRule("sku", PolicyClass.EXACT_PRESERVE, "exact", "SKU-42"))
        )
        with self.assertRaises(RuntimeFailure) as caught:
            runtime.normalise(secret)
        self.assertEqual(caught.exception.code, RuntimeCode.INVALID_INPUT)
        self.assertNotIn("customer", str(caught.exception))


class RuleInteractionProperties(unittest.TestCase):
    """NG-T05 deterministic overlap selection under policy permutations."""

    def test_overlapping_rule_permutations_have_one_semantic_result(self) -> None:
        rules = (
            PolicyRule("exact-long", PolicyClass.EXACT_PRESERVE, "exact", "ACME LTD"),
            PolicyRule(
                "alias-short",
                PolicyClass.APPROVED_ALIAS,
                "exact",
                "ACME",
                approved_output="Acme",
            ),
            PolicyRule("review-long", PolicyClass.REVIEW_OR_ABSTAIN, "exact", "ACME LTD"),
        )
        results = {
            BoundedSafetyRuntime(_verified(*permutation)).normalise("ACME LTD").output_text
            for permutation in itertools.permutations(rules)
        }
        self.assertEqual(results, {"ACME LTD"})


class InjectionAndPrivacyTests(unittest.TestCase):
    """NG-T06, NG-T10, NG-T16 and NG-T18 adversarial inert-input checks."""

    def test_prompt_like_input_cannot_change_governed_p0_semantics(self) -> None:
        fixture = json.loads(
            (Path(__file__).parent / "fixtures" / "adversarial-inputs.json").read_text(
                encoding="utf-8"
            )
        )
        runtime = BoundedSafetyRuntime(
            _verified(PolicyRule("sku", PolicyClass.EXACT_PRESERVE, "exact", "SKU-42"))
        )
        policy_digest = runtime.verified_policy.payload_sha256
        for text in fixture["inputs"]:
            with self.subTest(text_sha=hashlib.sha256(text.encode()).hexdigest()):
                result = runtime.normalise(text)
                self.assertIn("SKU-42", result.output_text)
                self.assertEqual(result.evidence.policy_sha256, policy_digest)
                evidence = canonical_json_bytes(result.evidence.to_dict()).decode()
                self.assertNotIn(text, evidence)
                self.assertNotIn("FREE-ITEM", result.output_text)

    @PROPERTY_SETTINGS
    @given(
        st.text(
            alphabet=st.characters(min_codepoint=97, max_codepoint=122),
            min_size=8,
            max_size=40,
        )
    )
    def test_generated_raw_secret_never_enters_default_evidence(self, secret: str) -> None:
        runtime = BoundedSafetyRuntime(
            _verified(PolicyRule("sku", PolicyClass.EXACT_PRESERVE, "exact", "SKU-42"))
        )
        raw_secret = f"private-{secret}-customer"
        result = runtime.normalise(raw_secret)
        encoded = canonical_json_bytes(result.evidence.to_dict()).decode()
        self.assertNotIn(raw_secret, encoded)

    def test_oversized_input_failure_is_atomic_and_private(self) -> None:
        secret = "private-boundary-value"
        runtime = BoundedSafetyRuntime(
            _verified(PolicyRule("sku", PolicyClass.EXACT_PRESERVE, "exact", "SKU-42")),
            limits=RuntimeLimits(max_input_bytes=4),
        )
        with self.assertRaises(RuntimeFailure) as caught:
            runtime.normalise(secret)
        self.assertEqual(caught.exception.code, RuntimeCode.LIMIT_EXCEEDED)
        self.assertNotIn(secret, str(caught.exception))


class ParserMutationProperties(unittest.TestCase):
    """NG-T13 malformed policy documents fail closed without fallback."""

    @PROPERTY_SETTINGS
    @given(st.binary(min_size=0, max_size=128))
    def test_arbitrary_policy_bytes_fail_at_sdk_boundary(self, mutated: bytes) -> None:
        with self.assertRaises(SDKError) as caught:
            verify_policy(
                mutated,
                b"{}",
                b"{}",
                verification_time=datetime(2026, 10, 23, tzinfo=UTC),
            )
        self.assertIn(caught.exception.code, {"INVALID_DOCUMENT", "UNSIGNED_POLICY"})
        self.assertNotIn(repr(mutated), str(caught.exception))


if __name__ == "__main__":
    unittest.main()
