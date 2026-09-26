import unittest

from normguard import (
    C0Adapter,
    C4SpacyAdapter,
    PolicyClass,
    PolicyRule,
    SpanAction,
)


class AdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = [
            PolicyRule("sku", PolicyClass.EXACT_PRESERVE, "regex", r"SKU-[A-Z]{2}\d{4}"),
            PolicyRule(
                "alias",
                PolicyClass.APPROVED_ALIAS,
                "exact",
                "trainers",
                approved_output="sneakers",
            ),
            PolicyRule("review", PolicyClass.REVIEW_OR_ABSTAIN, "exact", "limited-edition"),
            PolicyRule("p2", PolicyClass.CONTEXT_NORMALISABLE, "exact", "running"),
        ]
        cls.c4 = C4SpacyAdapter(cls.rules)

    def test_c0_is_deterministic_and_does_not_lemmatise(self) -> None:
        adapter = C0Adapter()
        first = adapter.normalise("  WOMEN\t shoes  ")
        self.assertEqual(first.output_text, "women shoes")
        self.assertEqual(first, adapter.normalise("  WOMEN\t shoes  "))
        self.assertEqual(adapter.metadata.configuration_id, "C0")

    def test_c4_uses_sentence_context_and_preserves_offsets(self) -> None:
        result = self.c4.normalise("Women are running in shoes.")
        self.assertEqual(result.output_text, "woman be run in shoe.")
        running = next(span for span in result.spans if span.text == "running")
        self.assertEqual((running.start, running.end), (10, 17))
        self.assertEqual(running.part_of_speech, "VERB")
        self.assertEqual(running.candidate_lemma, "run")

    def test_protected_identifier_is_masked_from_morphology(self) -> None:
        result = self.c4.normalise("Running shoes SKU-AX2048")
        self.assertEqual(result.output_text, "run shoe SKU-AX2048")
        sku = next(span for span in result.spans if span.rule_id == "sku")
        self.assertEqual(sku.action, SpanAction.PRESERVED)
        self.assertEqual((sku.start, sku.end), (14, 24))
        self.assertTrue(sku.engine_metadata["masked"])
        self.assertIsNone(sku.candidate_lemma)
        self.assertFalse(any(span.candidate_lemma == "sku-ax2048" for span in result.spans))

    def test_alias_and_review_rules_bypass_morphology(self) -> None:
        result = self.c4.normalise("trainers limited-edition")
        self.assertEqual(result.output_text, "sneakers limited-edition")
        self.assertTrue(result.review_required)
        self.assertTrue(all(span.engine_metadata["masked"] for span in result.spans))

    def test_c4_is_deterministic_and_reports_pinned_components(self) -> None:
        first = self.c4.normalise("Women are running in shoes.")
        self.assertEqual(first, self.c4.normalise("Women are running in shoes."))
        self.assertEqual(self.c4.metadata.configuration_id, "C4")
        self.assertEqual(self.c4.metadata.model_version, "3.8.0")
        self.assertEqual(self.c4.metadata.disabled_components, ("parser", "ner"))


if __name__ == "__main__":
    unittest.main()
