import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from normguard import PolicyClass, PolicyRule
from normguard.esci_experiment import EsciPartition
from normguard.experiment_runner import run_experiment
from normguard.policy_io import LoadedPolicy
from normguard.retrieval import RelevanceJudgement, RetrievalDocument, RetrievalQuery


class ExperimentRunnerTests(unittest.TestCase):
    def test_paired_runner_writes_verifiable_evidence(self) -> None:
        data = EsciPartition(
            "calibration",
            (
                RetrievalDocument("us:d1", "SKU-ONE running shoes"),
                RetrievalDocument("us:d2", "walking shoe"),
            ),
            (RetrievalQuery("us:q1", "SKU-ONE running", "calibration", ("us:d1", "us:d2")),),
            (
                RelevanceJudgement("us:q1", "us:d1", 3),
                RelevanceJudgement("us:q1", "us:d2", 0),
            ),
            "a" * 64,
        )
        policy = LoadedPolicy(
            "retail", "1.0.0", "b" * 64,
            (PolicyRule("sku", PolicyClass.EXACT_PRESERVE, "exact", "SKU-ONE"),),
        )
        with tempfile.TemporaryDirectory() as directory, patch(
            "normguard.experiment_runner._latency_ms", side_effect=(1.0, 1.5)
        ):
            summary = run_experiment(data=data, policy=policy, output_dir=Path(directory))
            self.assertEqual(summary["partition"], "calibration")
            self.assertTrue((Path(directory) / "evidence.json").is_file())
            self.assertTrue((Path(directory) / "paired-statistics.json").is_file())


if __name__ == "__main__":
    unittest.main()
