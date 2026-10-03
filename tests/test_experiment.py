import unittest

from normguard import paired_statistics, select_bm25_parameters


class ExperimentStatisticsTests(unittest.TestCase):
    def test_paired_statistics_are_reproducible(self) -> None:
        deltas = (0.01, 0.02, 0.00, 0.03, 0.01)
        first = paired_statistics(deltas, seed=7, sample_count=2_000)
        second = paired_statistics(deltas, seed=7, sample_count=2_000)
        self.assertEqual(first, second)
        self.assertEqual(first.query_count, 5)
        self.assertEqual(first.bit_generator, "PCG64")
        self.assertGreater(first.ci95_upper, first.ci95_lower)
        self.assertGreaterEqual(first.randomisation_p_value, 0.0)
        self.assertLessEqual(first.randomisation_p_value, 1.0)

    def test_non_finite_statistics_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "finite"):
            paired_statistics((0.1, float("nan")), seed=7)

    def test_bm25_selection_uses_declared_tie_break(self) -> None:
        scores = {
            (1.2, 0.75): 0.42,
            (0.9, 0.50): 0.42,
            (0.9, 0.25): 0.42,
            (0.6, 0.75): 0.40,
        }
        self.assertEqual(select_bm25_parameters(scores), (0.9, 0.25))

    def test_parameters_outside_frozen_grid_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "frozen grid"):
            select_bm25_parameters({(2.0, 0.75): 0.5})


if __name__ == "__main__":
    unittest.main()
