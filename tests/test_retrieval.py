import unittest

from normguard import (
    BM25EvaluationHarness,
    C0Adapter,
    RelevanceJudgement,
    RetrievalDocument,
    RetrievalQuery,
    compare_paired,
)


class RetrievalHarnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.documents = [
            RetrievalDocument("d1", "red running shoe"),
            RetrievalDocument("d2", "blue hat"),
            RetrievalDocument("d3", "shoe"),
        ]
        self.queries = [
            RetrievalQuery("q1", "red shoe", "development"),
            RetrievalQuery("q2", "blue hat", "development"),
        ]
        self.qrels = [
            RelevanceJudgement("q1", "d1", 2),
            RelevanceJudgement("q1", "d3", 1),
            RelevanceJudgement("q2", "d2", 1),
        ]
        self.harness = BM25EvaluationHarness(k1=0.9, b=0.75)

    def test_rankings_metrics_and_output_are_reproducible(self) -> None:
        first = self.harness.evaluate(
            adapter=C0Adapter(),
            documents=self.documents,
            queries=self.queries,
            qrels=self.qrels,
            partition="development",
        )
        second = self.harness.evaluate(
            adapter=C0Adapter(),
            documents=self.documents,
            queries=self.queries,
            qrels=self.qrels,
            partition="development",
        )
        self.assertEqual(first, second)
        self.assertEqual(first.queries[0].hits[0].document_id, "d1")
        self.assertEqual(first.queries[1].hits[0].document_id, "d2")
        self.assertAlmostEqual(first.aggregate_metrics["ndcg_at_10"], 1.0)
        self.assertAlmostEqual(first.aggregate_metrics["mrr_at_10"], 1.0)
        self.assertAlmostEqual(first.aggregate_metrics["recall_at_10"], 1.0)
        self.assertEqual(first.to_dict()["configuration_id"], "C0")

    def test_paired_comparison_aligns_query_level_metrics(self) -> None:
        result = self.harness.evaluate(
            adapter=C0Adapter(),
            documents=self.documents,
            queries=self.queries,
            qrels=self.qrels,
            partition="development",
        )
        comparison = compare_paired(result, result)
        self.assertEqual(comparison.query_ids, ("q1", "q2"))
        self.assertTrue(all(value == 0 for value in comparison.mean_deltas.values()))

    def test_query_specific_candidate_set_excludes_global_documents(self) -> None:
        queries = [
            RetrievalQuery("q1", "blue hat", "development", ("d1", "d3")),
        ]
        result = self.harness.evaluate(
            adapter=C0Adapter(),
            documents=self.documents,
            queries=queries,
            qrels=[RelevanceJudgement("q1", "d3", 1)],
            partition="development",
        )
        self.assertEqual({hit.document_id for hit in result.queries[0].hits}, {"d1", "d3"})
        self.assertNotIn("d2", [hit.document_id for hit in result.queries[0].hits])

    def test_official_test_partition_is_rejected(self) -> None:
        queries = [RetrievalQuery("q1", "red shoe", "test")]
        with self.assertRaisesRegex(ValueError, "only calibration and development"):
            self.harness.evaluate(
                adapter=C0Adapter(),
                documents=self.documents,
                queries=queries,
                qrels=[RelevanceJudgement("q1", "d1", 1)],
                partition="test",
            )

    def test_invalid_inputs_fail_before_retrieval(self) -> None:
        cases = [
            (
                [*self.documents, RetrievalDocument("d1", "duplicate")],
                self.queries,
                self.qrels,
                "duplicate document_id",
            ),
            (
                self.documents,
                self.queries,
                [*self.qrels, RelevanceJudgement("q1", "missing", 1)],
                "missing document",
            ),
            (
                self.documents,
                [RetrievalQuery("q1", "red shoe", "calibration")],
                [RelevanceJudgement("q1", "d1", 1)],
                "split leakage",
            ),
        ]
        for documents, queries, qrels, error in cases:
            with self.subTest(error=error), self.assertRaisesRegex(ValueError, error):
                self.harness.evaluate(
                    adapter=C0Adapter(),
                    documents=documents,
                    queries=queries,
                    qrels=qrels,
                    partition="development",
                )


if __name__ == "__main__":
    unittest.main()
