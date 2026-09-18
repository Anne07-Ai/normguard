import json
import unittest

from normguard.contracts import (
    Decision,
    EvidenceBundle,
    Failure,
    FailureCode,
    PolicyClass,
    PolicyRule,
    RunManifest,
    Severity,
    SpanAction,
    SpanTrace,
)


DIGEST = "a" * 64


def manifest() -> RunManifest:
    return RunManifest(
        protocol_id="NG-POC-001",
        protocol_version="0.1.0",
        dataset_name="fixture",
        dataset_sha256=DIGEST,
        split_manifest_sha256=DIGEST,
        configuration_id="C0",
        configuration_sha256=DIGEST,
        seed=20260918,
    )


class PolicyRuleTests(unittest.TestCase):
    def test_p1_requires_approved_output(self) -> None:
        with self.assertRaisesRegex(ValueError, "P1"):
            PolicyRule(
                rule_id="retail-alias-1",
                policy_class=PolicyClass.APPROVED_ALIAS,
                match_type="exact",
                pattern="trainers",
            )

    def test_non_p1_rejects_approved_output(self) -> None:
        with self.assertRaisesRegex(ValueError, "only valid"):
            PolicyRule(
                rule_id="retail-id-1",
                policy_class=PolicyClass.EXACT_PRESERVE,
                match_type="exact",
                pattern="SKU-AX2048",
                approved_output="sku-ax2048",
            )


class SpanTraceTests(unittest.TestCase):
    def test_p0_must_be_exact(self) -> None:
        with self.assertRaisesRegex(ValueError, "preserved exactly"):
            SpanTrace(
                text="SKU-AX2048",
                start=0,
                end=10,
                policy_class=PolicyClass.EXACT_PRESERVE,
                action=SpanAction.TRANSFORMED,
                emitted_text="sku-ax2048",
            )

    def test_offset_length_must_match(self) -> None:
        with self.assertRaisesRegex(ValueError, "span length"):
            SpanTrace(
                text="shoes",
                start=0,
                end=4,
                policy_class=PolicyClass.CONTEXT_NORMALISABLE,
                action=SpanAction.TRANSFORMED,
                emitted_text="shoe",
            )


class EvidenceTests(unittest.TestCase):
    def test_pass_rejects_hard_gate_failure(self) -> None:
        failure = Failure(
            code=FailureCode.IDENTIFIER_CORRUPTION,
            severity=Severity.BLOCKER,
            case_id="retail-1",
            message="protected identifier changed",
        )
        with self.assertRaisesRegex(ValueError, "S0 or S1"):
            EvidenceBundle(
                run=manifest(),
                decision=Decision.PASS,
                metrics={"ndcg_at_10": 0.5},
                failures=[failure],
                decision_reasons=["quality threshold met"],
            )

    def test_bundle_serialises_to_json(self) -> None:
        bundle = EvidenceBundle(
            run=manifest(),
            decision=Decision.INCONCLUSIVE,
            metrics={"ndcg_at_10": None},
            decision_reasons=["fixture has no relevance labels"],
        )
        encoded = json.dumps(bundle.to_dict(), sort_keys=True)
        self.assertIn('"decision": "INCONCLUSIVE"', encoded)
        self.assertIn('"contract_version": "0.1.0"', encoded)

    def test_manifest_rejects_invalid_digest(self) -> None:
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            RunManifest(
                protocol_id="NG-POC-001",
                protocol_version="0.1.0",
                dataset_name="fixture",
                dataset_sha256="not-a-digest",
                split_manifest_sha256=DIGEST,
                configuration_id="C0",
                configuration_sha256=DIGEST,
                seed=1,
            )


if __name__ == "__main__":
    unittest.main()
