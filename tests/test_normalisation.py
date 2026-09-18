import unittest

from normguard import (
    PolicyClass,
    PolicyRule,
    ProtectedNormaliser,
    SpanAction,
)


class RecordingLemma:
    def __init__(self) -> None:
        self.tokens: list[str] = []

    def __call__(self, token: str) -> str:
        self.tokens.append(token)
        return {"women": "woman", "shoes": "shoe", "running": "run"}.get(token, token)


class ProtectedNormaliserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.lemma = RecordingLemma()
        self.rules = [
            PolicyRule(
                rule_id="retail-sku",
                policy_class=PolicyClass.EXACT_PRESERVE,
                match_type="regex",
                pattern=r"SKU-[A-Z]{2}\d{4}",
            ),
            PolicyRule(
                rule_id="retail-alias",
                policy_class=PolicyClass.APPROVED_ALIAS,
                match_type="exact",
                pattern="trainers",
                approved_output="sneakers",
            ),
            PolicyRule(
                rule_id="retail-review",
                policy_class=PolicyClass.REVIEW_OR_ABSTAIN,
                match_type="exact",
                pattern="limited-edition",
            ),
        ]
        self.normaliser = ProtectedNormaliser(self.rules, self.lemma)

    def test_protected_identifier_never_reaches_morphology(self) -> None:
        result = self.normaliser.normalise("Running shoes SKU-AX2048")

        self.assertEqual(result.output_text, "run shoe SKU-AX2048")
        self.assertEqual(self.lemma.tokens, ["running", "shoes"])
        sku = result.spans[-1]
        self.assertEqual(sku.text, "SKU-AX2048")
        self.assertEqual(sku.action, SpanAction.PRESERVED)
        self.assertEqual((sku.start, sku.end), (14, 24))

    def test_approved_alias_is_the_only_p1_output(self) -> None:
        result = self.normaliser.normalise("blue trainers")
        self.assertEqual(result.output_text, "blue sneakers")
        alias = result.spans[-1]
        self.assertEqual(alias.rule_id, "retail-alias")
        self.assertEqual(alias.emitted_text, "sneakers")

    def test_p3_requests_review_without_transforming(self) -> None:
        result = self.normaliser.normalise("limited-edition shoes")
        self.assertTrue(result.review_required)
        self.assertEqual(result.output_text, "limited-edition shoe")
        protected = result.spans[0]
        self.assertEqual(protected.action, SpanAction.REVIEW)
        self.assertEqual(protected.emitted_text, protected.text)

    def test_leftmost_longest_rule_wins_deterministically(self) -> None:
        rules = [
            PolicyRule("short", PolicyClass.EXACT_PRESERVE, "exact", "AX"),
            PolicyRule("long", PolicyClass.EXACT_PRESERVE, "exact", "AX 20"),
        ]
        result = ProtectedNormaliser(rules, self.lemma).normalise("AX 20 shoes")
        self.assertEqual(result.spans[0].rule_id, "long")
        self.assertEqual(result.output_text, "AX 20 shoe")

    def test_whitespace_and_case_are_stable(self) -> None:
        first = self.normaliser.normalise("  WOMEN\t shoes  ")
        second = self.normaliser.normalise("  WOMEN\t shoes  ")
        self.assertEqual(first.output_text, "woman shoe")
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
