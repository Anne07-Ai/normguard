"""Reproducible, leakage-safe preparation of the Amazon ESCI benchmark.

Only the official ``train`` partition is eligible for NormGuard development.
The official ``test`` partition is rejected by construction and remains sealed.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

UPSTREAM_REPOSITORY = "https://github.com/amazon-science/esci-data"
UPSTREAM_COMMIT = "7916cdf6ab75a462e77f20ab40428a10923998d5"
SPLIT_ALGORITHM = "sha256-v1"
DEFAULT_SEED = "normguard-esci-v1"


@dataclass(frozen=True)
class Artifact:
    path: str
    sha256: str


OFFICIAL_ARTIFACTS = (
    Artifact(
        "shopping_queries_dataset_examples.parquet",
        "4a735b693b4a424a6fc67f5be6e4c811495c488bbf66d02a602d308b2744263a",
    ),
    Artifact(
        "shopping_queries_dataset_products.parquet",
        "25124442d064d64b26f74082d6fa09438d679efc0c183cf28d19064a2b65a265",
    ),
    Artifact(
        "shopping_queries_dataset_sources.csv",
        "a5fed8ecc016443de40bf3c63098f0e3f23bbe4daa4236f1c38b8c3184778c50",
    ),
)

EXPECTED_EXAMPLE_COLUMNS = frozenset(
    {
        "example_id",
        "query",
        "query_id",
        "product_id",
        "product_locale",
        "esci_label",
        "small_version",
        "large_version",
        "split",
    }
)


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def verify_artifacts(dataset_dir: Path, artifacts: Sequence[Artifact] = OFFICIAL_ARTIFACTS) -> None:
    """Fail closed when an official artefact is absent or has changed."""
    failures: list[str] = []
    for artifact in artifacts:
        path = dataset_dir / artifact.path
        if not path.is_file():
            failures.append(f"missing: {path}")
            continue
        actual = sha256_file(path)
        if actual != artifact.sha256:
            failures.append(
                f"checksum mismatch: {path} (expected {artifact.sha256}, got {actual})"
            )
    if failures:
        raise ValueError("ESCI artefact verification failed:\n" + "\n".join(failures))


def assign_query_split(locale: str, query_id: int | str, seed: str = DEFAULT_SEED) -> str:
    """Assign a query group deterministically using a stable SHA-256 bucket."""
    key = f"{seed}\x1f{locale}\x1f{query_id}".encode()
    bucket = int.from_bytes(hashlib.sha256(key).digest()[:8], "big") % 10_000
    if bucket < 8_000:
        return "train"
    if bucket < 9_000:
        return "calibration"
    return "development"


def build_query_assignments(
    query_keys: Iterable[tuple[str, int | str]], seed: str = DEFAULT_SEED
) -> list[dict[str, str]]:
    """Return one stable assignment for each unique locale/query pair."""
    unique_keys = {(str(locale), str(query_id)) for locale, query_id in query_keys}
    return [
        {
            "product_locale": locale,
            "query_id": query_id,
            "normguard_split": assign_query_split(locale, query_id, seed),
        }
        for locale, query_id in sorted(unique_keys)
    ]


def validate_no_overlap(assignments: Iterable[Mapping[str, str]]) -> None:
    """Reject manifests that assign one query group to multiple splits."""
    seen: dict[tuple[str, str], str] = {}
    for row in assignments:
        key = (row["product_locale"], row["query_id"])
        split = row["normguard_split"]
        previous = seen.setdefault(key, split)
        if previous != split:
            raise ValueError(f"query group {key!r} appears in both {previous!r} and {split!r}")


def load_official_training_queries(examples_path: Path) -> list[tuple[str, int | str]]:
    """Read reduced-ranking query keys from the official training partition only."""
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover - exercised without the optional extra
        raise RuntimeError("Install the ESCI extra first: pip install -e '.[esci]'") from exc

    schema_columns = frozenset(pq.read_schema(examples_path).names)
    missing = EXPECTED_EXAMPLE_COLUMNS - schema_columns
    if missing:
        raise ValueError(f"ESCI examples schema is missing columns: {sorted(missing)}")

    table = pq.read_table(
        examples_path,
        columns=["query_id", "product_locale"],
        filters=[("split", "=", "train"), ("small_version", "=", 1)],
    )
    rows = table.to_pylist()
    return [(row["product_locale"], row["query_id"]) for row in rows]


def write_split_outputs(
    assignments: Sequence[Mapping[str, str]], output_dir: Path, seed: str = DEFAULT_SEED
) -> dict[str, object]:
    """Write deterministic CSV assignments and a self-verifying JSON manifest."""
    validate_no_overlap(assignments)
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "query-splits.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["product_locale", "query_id", "normguard_split"]
        )
        writer.writeheader()
        writer.writerows(assignments)

    counts = Counter(row["normguard_split"] for row in assignments)
    manifest: dict[str, object] = {
        "schema_version": 1,
        "dataset": "Amazon Shopping Queries Dataset (ESCI), reduced ranking version",
        "upstream": {"repository": UPSTREAM_REPOSITORY, "commit": UPSTREAM_COMMIT},
        "source_partition": "train",
        "official_test_status": "sealed-not-read-or-transformed",
        "split": {
            "algorithm": SPLIT_ALGORITHM,
            "seed": seed,
            "unit": ["product_locale", "query_id"],
            "thresholds": {"train": 0.8, "calibration": 0.1, "development": 0.1},
        },
        "counts": {"queries": len(assignments), "by_split": dict(sorted(counts.items()))},
        "outputs": {"query-splits.csv": {"sha256": sha256_file(csv_path)}},
    }
    manifest_path = output_dir / "split-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def prepare(dataset_dir: Path, output_dir: Path, seed: str = DEFAULT_SEED) -> dict[str, object]:
    verify_artifacts(dataset_dir)
    query_keys = load_official_training_queries(
        dataset_dir / "shopping_queries_dataset_examples.parquet"
    )
    assignments = build_query_assignments(query_keys, seed)
    return write_split_outputs(assignments, output_dir, seed)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--seed", default=DEFAULT_SEED)
    args = parser.parse_args(argv)
    manifest = prepare(args.dataset_dir, args.output_dir, args.seed)
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
