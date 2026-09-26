import tempfile
import unittest
from pathlib import Path

from normguard.esci import (
    Artifact,
    assign_query_split,
    build_query_assignments,
    sha256_file,
    validate_no_overlap,
    verify_artifacts,
    write_split_outputs,
)


class EsciContractTests(unittest.TestCase):
    def test_query_assignment_is_stable(self) -> None:
        first = assign_query_split("us", 12345)
        second = assign_query_split("us", 12345)
        self.assertEqual(first, second)
        self.assertIn(first, {"train", "calibration", "development"})

    def test_duplicate_rows_receive_one_query_level_assignment(self) -> None:
        assignments = build_query_assignments([("us", 7), ("us", 7), ("es", 7)])
        self.assertEqual(len(assignments), 2)
        validate_no_overlap(assignments)

    def test_overlap_is_rejected(self) -> None:
        rows = [
            {"product_locale": "us", "query_id": "7", "normguard_split": "train"},
            {"product_locale": "us", "query_id": "7", "normguard_split": "development"},
        ]
        with self.assertRaisesRegex(ValueError, "appears in both"):
            validate_no_overlap(rows)

    def test_artifact_verification_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            payload = root / "example.bin"
            payload.write_bytes(b"verified")
            artifact = Artifact("example.bin", sha256_file(payload))
            verify_artifacts(root, (artifact,))
            payload.write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                verify_artifacts(root, (artifact,))

    def test_output_manifest_is_reproducible_and_self_verifying(self) -> None:
        assignments = build_query_assignments([("us", 2), ("us", 1), ("jp", 3)])
        with (
            tempfile.TemporaryDirectory() as first_dir,
            tempfile.TemporaryDirectory() as second_dir,
        ):
            first = Path(first_dir)
            second = Path(second_dir)
            first_manifest = write_split_outputs(assignments, first)
            second_manifest = write_split_outputs(assignments, second)
            self.assertEqual(first_manifest, second_manifest)
            self.assertEqual(
                first_manifest["outputs"]["query-splits.csv"]["sha256"],
                sha256_file(first / "query-splits.csv"),
            )
            self.assertEqual(
                (first / "split-manifest.json").read_bytes(),
                (second / "split-manifest.json").read_bytes(),
            )


if __name__ == "__main__":
    unittest.main()
