"""Guarded ESCI calibration/development dataset loader.

The official test partition is not an accepted input and is never read.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .esci import DEFAULT_SEED, sha256_file, verify_artifacts
from .retrieval import RelevanceJudgement, RetrievalDocument, RetrievalQuery

_ALLOWED_PARTITIONS = {"calibration", "development"}
_LABELS = {"E": 3, "S": 2, "C": 1, "I": 0}


@dataclass(frozen=True, slots=True)
class EsciPartition:
    partition: str
    documents: tuple[RetrievalDocument, ...]
    queries: tuple[RetrievalQuery, ...]
    qrels: tuple[RelevanceJudgement, ...]
    split_manifest_sha256: str


def _verified_assignments(artifact_dir: Path, partition: str) -> set[tuple[str, str]]:
    if partition not in _ALLOWED_PARTITIONS:
        raise ValueError("only calibration and development partitions may be loaded")
    csv_path = artifact_dir / "query-splits.csv"
    manifest_path = artifact_dir / "split-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = manifest.get("outputs", {}).get("query-splits.csv", {}).get("sha256")
    if manifest.get("source_partition") != "train":
        raise ValueError("split manifest source must be the official training partition")
    if manifest.get("official_test_status") != "sealed-not-read-or-transformed":
        raise ValueError("official test seal is absent")
    if manifest.get("split", {}).get("seed") != DEFAULT_SEED:
        raise ValueError("split manifest seed is not the frozen seed")
    if not isinstance(expected, str) or sha256_file(csv_path) != expected:
        raise ValueError("query-splits.csv does not match its manifest")
    with csv_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    keys = {
        (row["product_locale"], row["query_id"])
        for row in rows
        if row["normguard_split"] == partition
    }
    if not keys:
        raise ValueError(f"split manifest contains no {partition} queries")
    return keys


def load_partition(dataset_dir: Path, artifact_dir: Path, partition: str) -> EsciPartition:
    """Verify provenance, then load only English small-version official-train rows."""
    verify_artifacts(dataset_dir)
    allowed_keys = _verified_assignments(artifact_dir, partition)
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Install the ESCI extra first: uv sync --locked --all-extras") from exc

    example_rows = pq.read_table(
        dataset_dir / "shopping_queries_dataset_examples.parquet",
        columns=["query", "query_id", "product_id", "product_locale", "esci_label"],
        filters=[("split", "=", "train"), ("small_version", "=", 1),
                 ("product_locale", "=", "us")],
    ).to_pylist()
    selected = [
        row for row in example_rows
        if (str(row["product_locale"]), str(row["query_id"])) in allowed_keys
    ]
    if not selected:
        raise ValueError(f"no verified English rows found for {partition}")

    query_text: dict[str, str] = {}
    candidates: dict[str, list[str]] = defaultdict(list)
    qrels: list[RelevanceJudgement] = []
    product_ids: set[str] = set()
    for row in selected:
        query_id = f'us:{row["query_id"]}'
        document_id = f'us:{row["product_id"]}'
        text = str(row["query"])
        if query_id in query_text and query_text[query_id] != text:
            raise ValueError("one query ID has inconsistent query text")
        label = str(row["esci_label"])
        if label not in _LABELS:
            raise ValueError(f"unknown ESCI label: {label}")
        query_text[query_id] = text
        candidates[query_id].append(document_id)
        product_ids.add(str(row["product_id"]))
        qrels.append(RelevanceJudgement(query_id, document_id, _LABELS[label]))

    product_rows = pq.read_table(
        dataset_dir / "shopping_queries_dataset_products.parquet",
        columns=["product_id", "product_locale", "product_title"],
        filters=[("product_locale", "=", "us")],
    ).to_pylist()
    documents = tuple(
        RetrievalDocument(f'us:{row["product_id"]}', str(row["product_title"] or ""))
        for row in product_rows if str(row["product_id"]) in product_ids
    )
    if {item.document_id for item in documents} != {f"us:{item}" for item in product_ids}:
        raise ValueError("one or more candidate products are missing")
    queries = tuple(
        RetrievalQuery(query_id, query_text[query_id], partition, tuple(candidates[query_id]))
        for query_id in sorted(query_text)
    )
    manifest_digest = sha256_file(artifact_dir / "split-manifest.json")
    return EsciPartition(partition, documents, queries, tuple(qrels), manifest_digest)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset_dir", type=Path)
    parser.add_argument("artifact_dir", type=Path)
    parser.add_argument("partition", choices=sorted(_ALLOWED_PARTITIONS))
    args = parser.parse_args(argv)
    data = load_partition(args.dataset_dir, args.artifact_dir, args.partition)
    print(json.dumps({
        "partition": data.partition,
        "queries": len(data.queries),
        "documents": len(data.documents),
        "qrels": len(data.qrels),
        "split_manifest_sha256": data.split_manifest_sha256,
        "document_field": "product_title",
        "official_test_status": "sealed-not-read-or-transformed",
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
