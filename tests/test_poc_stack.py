import json
import math
import unittest
from importlib.metadata import version
from pathlib import Path

import bm25s
import en_core_web_sm

from normguard import PolicyClass, PolicyRule, ProtectedNormaliser, SpanAction

FIXTURES = Path(__file__).parent / "fixtures"


class PinnedStackContractTests(unittest.TestCase):
    def test_pinned_spacy_model_is_executable_and_offset_aware(self) -> None:
        self.assertEqual(version("spacy"), "3.8.16")
        self.assertEqual(version("en-core-web-sm"), "3.8.0")
        pipeline = en_core_web_sm.load(disable=["parser", "ner"])

        tokens = [
            (token.text, token.lemma_, token.pos_, token.idx)
            for token in pipeline("Women are running in shoes.")
        ]

        self.assertEqual(
            tokens,
            [
                ("Women", "woman", "NOUN", 0),
                ("are", "be", "AUX", 6),
                ("running", "run", "VERB", 10),
                ("in", "in", "ADP", 18),
                ("shoes", "shoe", "NOUN", 21),
                (".", ".", "PUNCT", 26),
            ],
        )

    def test_bm25_reference_ranking_matches_hand_checked_order(self) -> None:
        corpus = ["red shoe", "red red shoe", "blue hat"]
        corpus_tokens = bm25s.tokenize(corpus, stopwords=[], stemmer=None, show_progress=False)
        retriever = bm25s.BM25(method="lucene")
        retriever.index(corpus_tokens, show_progress=False)

        query_tokens = bm25s.tokenize(
            ["red shoe"], stopwords=[], stemmer=None, show_progress=False
        )
        documents, scores = retriever.retrieve(query_tokens, k=3, show_progress=False)

        # Lucene BM25 gives the repeated query-term document a small, expected
        # term-frequency advantage after length normalisation.
        self.assertEqual(documents[0].tolist(), [1, 0, 2])
        self.assertAlmostEqual(float(scores[0][0]), 0.41256678, places=6)
        self.assertAlmostEqual(float(scores[0][1]), 0.40183517, places=6)
        self.assertEqual(float(scores[0][2]), 0.0)
        self.assertGreater(scores[0][0], scores[0][1])
        self.assertGreater(scores[0][1], scores[0][2])
        self.assertTrue(all(math.isfinite(float(score)) for score in scores[0]))

    def test_protected_offset_fixture_remains_exact(self) -> None:
        fixture = json.loads(
            (FIXTURES / "offset-preservation.json").read_text(encoding="utf-8")
        )
        expected = fixture["protected_span"]
        normaliser = ProtectedNormaliser(
            [
                PolicyRule(
                    rule_id=expected["rule_id"],
                    policy_class=PolicyClass.EXACT_PRESERVE,
                    match_type="exact",
                    pattern=expected["text"],
                )
            ]
        )

        result = normaliser.normalise(fixture["source"])
        protected = result.spans[-1]

        self.assertEqual(result.output_text, fixture["expected_output"])
        self.assertEqual(protected.text, expected["text"])
        self.assertEqual((protected.start, protected.end), (expected["start"], expected["end"]))
        self.assertEqual(protected.action, SpanAction.PRESERVED)


if __name__ == "__main__":
    unittest.main()
