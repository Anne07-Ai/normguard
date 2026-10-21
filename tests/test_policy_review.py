import csv
import tempfile
import unittest
from pathlib import Path

from normguard.policy_io import load_policy
from normguard.policy_review import generate_pilot, resolve_pilot

FIELDS = (
    "candidate_id", "candidate_type", "surface", "normalized_surface", "frequency",
    "source_fields", "example_contexts", "status", "review_rank", "inclusion_reason",
    "reviewer_1_class", "reviewer_1_source", "reviewer_2_class", "reviewer_2_source",
    "resolution_class", "approved_output", "resolution_source",
)


class PolicyReviewTests(unittest.TestCase):
    def _queue(self, path: Path) -> Path:
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n")
            writer.writeheader()
            for candidate_type, count in (
                ("brand", 40), ("model_or_sku", 40), ("ambiguous_term", 20)
            ):
                for rank in range(1, count + 1):
                    candidate_id = f"{candidate_type}-{rank:03}"
                    writer.writerow({
                        "candidate_id": candidate_id,
                        "candidate_type": candidate_type,
                        "surface": candidate_id,
                        "normalized_surface": candidate_id,
                        "frequency": str(200 - rank),
                        "source_fields": "product_title",
                        "example_contexts": candidate_id,
                        "status": "UNREVIEWED",
                        "review_rank": str(rank),
                        "inclusion_reason": "fixture",
                        **dict.fromkeys(FIELDS[10:], ""),
                    })
        return path

    def _complete(self, source: Path, target: Path) -> Path:
        with source.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        for row in rows:
            row["reviewer_1_class"] = "P0"
            row["reviewer_1_source"] = "catalogue evidence"
            row["reviewer_2_class"] = "P0"
            row["reviewer_2_source"] = "independent evidence"
        with target.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        return target

    def test_pilot_is_reproducible_and_has_frozen_composition(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            queue = self._queue(root / "queue.csv")
            first, second = root / "first", root / "second"
            manifest_1 = generate_pilot(queue, first)
            manifest_2 = generate_pilot(queue, second)
            self.assertEqual(manifest_1, manifest_2)
            self.assertEqual(manifest_1["pilot_counts"], {
                "brand": 40, "model_or_sku": 40, "ambiguous_term": 20,
            })
            self.assertEqual(
                (first / "policy-review-pilot.csv").read_bytes(),
                (second / "policy-review-pilot.csv").read_bytes(),
            )

    def test_complete_agreement_generates_loadable_policy_and_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            queue = self._queue(root / "queue.csv")
            generate_pilot(queue, root / "pilot")
            reviewed = self._complete(
                root / "pilot" / "policy-review-pilot.csv", root / "reviewed.csv"
            )
            evidence = resolve_pilot(
                queue, reviewed, root / "pilot" / "policy-review-pilot-manifest.json",
                root / "resolved", "retail-en-pilot", "0.1.0",
            )
            self.assertEqual(evidence["agreement_rate"], 1.0)
            self.assertEqual(evidence["class_distribution"]["P0"], 100)
            self.assertEqual(len(load_policy(root / "resolved" / "pilot-policy.json").rules), 100)

    def test_changed_evidence_and_unresolved_disagreement_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            queue = self._queue(root / "queue.csv")
            generate_pilot(queue, root / "pilot")
            reviewed = self._complete(
                root / "pilot" / "policy-review-pilot.csv", root / "reviewed.csv"
            )
            with reviewed.open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            rows[0]["surface"] = "tampered"
            with reviewed.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n")
                writer.writeheader()
                writer.writerows(rows)
            with self.assertRaisesRegex(ValueError, "immutable candidate evidence changed"):
                resolve_pilot(
                    queue, reviewed, root / "pilot" / "policy-review-pilot-manifest.json",
                    root / "resolved", "retail-en-pilot", "0.1.0",
                )
            rows[0]["surface"] = rows[0]["candidate_id"]
            rows[0]["reviewer_2_class"] = "P3"
            with reviewed.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n")
                writer.writeheader()
                writer.writerows(rows)
            with self.assertRaisesRegex(ValueError, "disagreement is unresolved"):
                resolve_pilot(
                    queue, reviewed, root / "pilot" / "policy-review-pilot-manifest.json",
                    root / "resolved", "retail-en-pilot", "0.1.0",
                )


if __name__ == "__main__":
    unittest.main()
