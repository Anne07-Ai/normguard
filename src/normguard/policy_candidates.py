"""Generate review-only terminology policy candidates from ESCI calibration."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from .esci import sha256_file
from .esci_experiment import EsciPartition, load_partition

_MODEL_TOKEN = re.compile(
    r"(?<![\w])(?=[A-Za-z0-9._/-]{3,}\b)(?=[A-Za-z0-9._/-]*[A-Za-z])"
    r"(?=[A-Za-z0-9._/-]*\d)[A-Za-z0-9][A-Za-z0-9._/-]*(?![\w])"
)
_ACRONYM = re.compile(r"(?<![\w])[A-Z][A-Z0-9&+.-]{1,7}(?![\w])")
_STATUS = "UNREVIEWED"
_REVIEW_LIMITS = {"brand": 1000, "model_or_sku": 700, "ambiguous_term": 300}
_MIN_FREQUENCY = {"brand": 2, "model_or_sku": 2, "ambiguous_term": 5}
_ATTRIBUTE_TOKEN = re.compile(
    r"^\d+(?:-?(?:packs?|pcs?|pieces?|ct|count|gb|tb|mb|k|p|hz|inch|in|oz|ml|lb|"
    r"st|nd|rd|th))$",
    re.IGNORECASE,
)
_REVIEW_FIELDS = (
    "reviewer_1_class",
    "reviewer_1_source",
    "reviewer_2_class",
    "reviewer_2_source",
    "resolution_class",
    "approved_output",
    "resolution_source",
)


@dataclass(frozen=True, slots=True)
class PolicyCandidate:
    candidate_id: str
    candidate_type: str
    surface: str
    normalized_surface: str
    frequency: int
    source_fields: tuple[str, ...]
    example_contexts: tuple[str, ...]
    status: str = _STATUS


@dataclass(frozen=True, slots=True)
class ReviewCandidate:
    candidate: PolicyCandidate
    review_rank: int
    inclusion_reason: str


def _normalized(surface: str) -> str:
    return " ".join(surface.casefold().split())


def _candidate_id(candidate_type: str, normalized_surface: str) -> str:
    payload = f"{candidate_type}\0{normalized_surface}".encode()
    return f"pc-{hashlib.sha256(payload).hexdigest()[:16]}"


def _context(text: str, limit: int = 160) -> str:
    value = " ".join(text.split())
    return value if len(value) <= limit else f"{value[:limit - 1]}…"


def generate_policy_candidates(data: EsciPartition) -> tuple[PolicyCandidate, ...]:
    """Extract deterministic, unclassified candidates from calibration only."""
    if data.partition != "calibration":
        raise ValueError("policy candidates may only be generated from calibration")

    observations: dict[tuple[str, str], list[tuple[str, str, str]]] = defaultdict(list)
    documents = {item.document_id: item.text for item in data.documents}
    for document_id, surface in data.document_brands:
        surface = surface.strip()
        if surface:
            observations[("brand", _normalized(surface))].append(
                (surface, "product_brand", _context(documents.get(document_id, surface)))
            )

    text_sources = [
        *( (item.text, "product_title") for item in data.documents ),
        *( (item.text, "query") for item in data.queries ),
    ]
    for text, source in text_sources:
        for match in _MODEL_TOKEN.finditer(text):
            surface = match.group(0).strip("./-")
            if surface:
                observations[("model_or_sku", _normalized(surface))].append(
                    (surface, source, _context(text))
                )
        for match in _ACRONYM.finditer(text):
            surface = match.group(0).strip(".-")
            if surface and not any(character.isdigit() for character in surface):
                observations[("ambiguous_term", _normalized(surface))].append(
                    (surface, source, _context(text))
                )

    candidates: list[PolicyCandidate] = []
    for (candidate_type, normalized), seen in sorted(observations.items()):
        surfaces = sorted(
            (surface for surface, _, _ in seen), key=lambda item: (item.casefold(), item)
        )
        sources = tuple(sorted({source for _, source, _ in seen}))
        examples = tuple(sorted({example for _, _, example in seen})[:3])
        candidates.append(PolicyCandidate(
            candidate_id=_candidate_id(candidate_type, normalized),
            candidate_type=candidate_type,
            surface=surfaces[0],
            normalized_surface=normalized,
            frequency=len(seen),
            source_fields=sources,
            example_contexts=examples,
        ))
    return tuple(candidates)


def select_review_candidates(
    candidates: Sequence[PolicyCandidate],
) -> tuple[ReviewCandidate, ...]:
    """Select a deterministic, bounded reviewer queue from the complete inventory."""
    selected: list[ReviewCandidate] = []
    for candidate_type in ("brand", "model_or_sku", "ambiguous_term"):
        eligible = [
            item for item in candidates
            if item.candidate_type == candidate_type
            and item.frequency >= _MIN_FREQUENCY[candidate_type]
            and not (
                candidate_type == "model_or_sku"
                and _ATTRIBUTE_TOKEN.fullmatch(item.normalized_surface)
            )
            and not (
                candidate_type == "ambiguous_term"
                and sum(character.isalnum() for character in item.normalized_surface) < 2
            )
        ]
        eligible.sort(
            key=lambda item: (-item.frequency, item.normalized_surface, item.candidate_id)
        )
        reason = (
            f"{candidate_type}; frequency >= {_MIN_FREQUENCY[candidate_type]}; "
            "deterministic frequency rank"
        )
        selected.extend(
            ReviewCandidate(item, rank, reason)
            for rank, item in enumerate(eligible[:_REVIEW_LIMITS[candidate_type]], start=1)
        )
    return tuple(selected)


def _review_row(item: ReviewCandidate) -> dict[str, object]:
    row = asdict(item.candidate)
    row["source_fields"] = " | ".join(item.candidate.source_fields)
    row["example_contexts"] = " | ".join(item.candidate.example_contexts)
    row["review_rank"] = item.review_rank
    row["inclusion_reason"] = item.inclusion_reason
    row.update(dict.fromkeys(_REVIEW_FIELDS, ""))
    return row


def write_candidate_review(data: EsciPartition, output_dir: Path) -> dict[str, object]:
    """Write reproducible JSON, CSV review sheet, and a self-verifying manifest."""
    candidates = generate_policy_candidates(data)
    review_candidates = select_review_candidates(candidates)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "policy-candidates.json"
    csv_path = output_dir / "policy-candidates.csv"
    review_json_path = output_dir / "policy-review-queue.json"
    review_csv_path = output_dir / "policy-review-queue.csv"
    json_path.write_text(
        json.dumps([asdict(item) for item in candidates], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    fieldnames = [
        "candidate_id", "candidate_type", "surface", "normalized_surface", "frequency",
        "source_fields", "example_contexts", "status", *_REVIEW_FIELDS,
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for item in candidates:
            row = asdict(item)
            row["source_fields"] = " | ".join(item.source_fields)
            row["example_contexts"] = " | ".join(item.example_contexts)
            row.update(dict.fromkeys(_REVIEW_FIELDS, ""))
            writer.writerow(row)

    review_json_path.write_text(
        json.dumps([
            {
                **asdict(item.candidate),
                "review_rank": item.review_rank,
                "inclusion_reason": item.inclusion_reason,
            }
            for item in review_candidates
        ], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    review_fieldnames = [
        "candidate_id", "candidate_type", "surface", "normalized_surface", "frequency",
        "source_fields", "example_contexts", "status", "review_rank", "inclusion_reason",
        *_REVIEW_FIELDS,
    ]
    with review_csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=review_fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(_review_row(item) for item in review_candidates)

    manifest: dict[str, object] = {
        "schema_version": 2,
        "dataset": "Amazon Shopping Queries Dataset (ESCI)",
        "partition": "calibration",
        "candidate_count": len(candidates),
        "review_queue_count": len(review_candidates),
        "review_queue_counts": {
            candidate_type: sum(
                item.candidate.candidate_type == candidate_type for item in review_candidates
            )
            for candidate_type in _REVIEW_LIMITS
        },
        "candidate_status": _STATUS,
        "split_manifest_sha256": data.split_manifest_sha256,
        "official_test_status": "sealed-not-read-or-transformed",
        "outputs": {
            path.name: {"sha256": sha256_file(path)}
            for path in (csv_path, json_path, review_csv_path, review_json_path)
        },
    }
    manifest_path = output_dir / "policy-candidates-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset_dir", type=Path)
    parser.add_argument("artifact_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args(argv)
    data = load_partition(args.dataset_dir, args.artifact_dir, "calibration")
    print(json.dumps(write_candidate_review(data, args.output_dir), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
