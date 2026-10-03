import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from normguard.esci import DEFAULT_SEED
from normguard.esci_experiment import _verified_assignments, main


class GuardedEsciExperimentTests(unittest.TestCase):
    def _artifacts(self, root: Path) -> Path:
        csv_path = root / "query-splits.csv"
        with csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle, fieldnames=["product_locale", "query_id", "normguard_split"]
            )
            writer.writeheader()
            writer.writerow(
                {"product_locale": "us", "query_id": "7", "normguard_split": "calibration"}
            )
        digest = hashlib.sha256(csv_path.read_bytes()).hexdigest()
        (root / "split-manifest.json").write_text(json.dumps({
            "source_partition": "train",
            "official_test_status": "sealed-not-read-or-transformed",
            "split": {"seed": DEFAULT_SEED},
            "outputs": {"query-splits.csv": {"sha256": digest}},
        }), encoding="utf-8")
        return root

    def test_verified_assignments_accept_only_declared_partition(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            keys = _verified_assignments(self._artifacts(Path(directory)), "calibration")
        self.assertEqual(keys, {("us", "7")})

    def test_final_test_is_rejected_before_any_dataset_read(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            self.assertRaisesRegex(ValueError, "only calibration and development"),
        ):
            _verified_assignments(self._artifacts(Path(directory)), "test")

    def test_tampered_assignment_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._artifacts(Path(directory))
            (root / "query-splits.csv").write_text("tampered\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "does not match"):
                _verified_assignments(root, "calibration")

    def test_cli_parser_does_not_offer_final_test(self) -> None:
        with self.assertRaises(SystemExit):
            main(["dataset", "artifacts", "test"])


if __name__ == "__main__":
    unittest.main()
