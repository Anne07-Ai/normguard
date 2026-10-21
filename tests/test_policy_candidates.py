import csv
import json
import tempfile
import unittest
from pathlib import Path

from normguard.esci_experiment import EsciPartition
from normguard.policy_candidates import generate_policy_candidates, write_candidate_review
from normguard.retrieval import RetrievalDocument, RetrievalQuery


class PolicyCandidateTests(unittest.TestCase):
    def _partition(self, partition: str = "calibration") -> EsciPartition:
        return EsciPartition(
            partition=partition,
            documents=(
                RetrievalDocument("us:p1", "Acme XR-200 USB adapter"),
                RetrievalDocument("us:p2", "Acme XR-200 cable for TV"),
            ),
            queries=(RetrievalQuery("us:q1", "XR-200 USB cable", partition, ("us:p1",)),),
            qrels=(),
            split_manifest_sha256="a" * 64,
            document_brands=(("us:p1", "Acme"), ("us:p2", "Acme")),
        )

    def test_candidates_are_review_only_and_include_brand_model_and_ambiguous(self) -> None:
        candidates = generate_policy_candidates(self._partition())
        by_key = {(item.candidate_type, item.normalized_surface): item for item in candidates}
        self.assertEqual(by_key[("brand", "acme")].frequency, 2)
        self.assertEqual(by_key[("model_or_sku", "xr-200")].frequency, 3)
        self.assertEqual(by_key[("ambiguous_term", "usb")].status, "UNREVIEWED")
        self.assertTrue(all(item.candidate_id.startswith("pc-") for item in candidates))

    def test_development_is_rejected_before_output_is_created(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            with self.assertRaisesRegex(ValueError, "only be generated from calibration"):
                write_candidate_review(self._partition("development"), output)
            self.assertFalse(output.exists())

    def test_outputs_are_reproducible_and_include_empty_review_columns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first"
            second = root / "second"
            manifest_1 = write_candidate_review(self._partition(), first)
            manifest_2 = write_candidate_review(self._partition(), second)
            for name in ("policy-candidates.csv", "policy-candidates.json"):
                self.assertEqual((first / name).read_bytes(), (second / name).read_bytes())
            self.assertEqual(manifest_1, manifest_2)
            with (first / "policy-candidates.csv").open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertTrue(rows)
            self.assertTrue(all(row["status"] == "UNREVIEWED" for row in rows))
            self.assertTrue(all(row["reviewer_1_class"] == "" for row in rows))
            saved = json.loads((first / "policy-candidates-manifest.json").read_text())
            self.assertEqual(saved["official_test_status"], "sealed-not-read-or-transformed")


if __name__ == "__main__":
    unittest.main()
