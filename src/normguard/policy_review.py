"""Deterministic two-reviewer pilot generation and resolution."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from .esci import sha256_file

_QUOTAS = {"brand": 40, "model_or_sku": 40, "ambiguous_term": 20}
_CLASSES = {"P0", "P1", "P2", "P3"}
_IMMUTABLE = (
    "candidate_id", "candidate_type", "surface", "normalized_surface", "frequency",
    "source_fields", "example_contexts", "status", "review_rank", "inclusion_reason",
)
_REVIEW = (
    "reviewer_1_class", "reviewer_1_source", "reviewer_2_class", "reviewer_2_source",
    "resolution_class", "approved_output", "resolution_source",
)
_FIELDS = (*_IMMUTABLE, *_REVIEW)


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != _FIELDS:
            raise ValueError(f"{path.name} does not match the review schema")
        rows = list(reader)
    ids = [row["candidate_id"] for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError(f"{path.name} contains duplicate candidate IDs")
    return rows


def generate_pilot(queue_path: Path, output_dir: Path) -> dict[str, object]:
    """Select the first ranked records within each frozen pilot quota."""
    rows = _rows(queue_path)
    selected: list[dict[str, str]] = []
    for candidate_type, quota in _QUOTAS.items():
        candidates = [row for row in rows if row["candidate_type"] == candidate_type]
        candidates.sort(key=lambda row: (int(row["review_rank"]), row["candidate_id"]))
        if len(candidates) < quota:
            raise ValueError(f"review queue has fewer than {quota} {candidate_type} candidates")
        selected.extend(candidates[:quota])

    output_dir.mkdir(parents=True, exist_ok=True)
    pilot_path = output_dir / "policy-review-pilot.csv"
    with pilot_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(selected)
    manifest: dict[str, object] = {
        "schema_version": 1,
        "partition": "calibration",
        "official_test_status": "sealed-not-read-or-transformed",
        "source_review_queue_sha256": sha256_file(queue_path),
        "pilot_sha256": sha256_file(pilot_path),
        "pilot_count": len(selected),
        "pilot_counts": dict(_QUOTAS),
        "candidate_ids": [row["candidate_id"] for row in selected],
    }
    manifest_path = output_dir / "policy-review-pilot-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def resolve_pilot(
    queue_path: Path,
    reviewed_path: Path,
    manifest_path: Path,
    output_dir: Path,
    policy_id: str,
    policy_version: str,
) -> dict[str, object]:
    """Validate completed reviews and emit policy plus agreement evidence."""
    queue = {row["candidate_id"]: row for row in _rows(queue_path)}
    reviewed = _rows(reviewed_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("source_review_queue_sha256") != sha256_file(queue_path):
        raise ValueError("source review queue digest does not match pilot manifest")
    expected_ids = manifest.get("candidate_ids")
    reviewed_ids = [row["candidate_id"] for row in reviewed]
    if not isinstance(expected_ids, list) or reviewed_ids != expected_ids:
        raise ValueError("reviewed pilot candidates differ from the pilot manifest")

    resolved: list[tuple[dict[str, str], str]] = []
    agreements = 0
    for row in reviewed:
        original = queue.get(row["candidate_id"])
        if original is None or any(row[field] != original[field] for field in _IMMUTABLE):
            raise ValueError(f"immutable candidate evidence changed: {row['candidate_id']}")
        first, second = row["reviewer_1_class"], row["reviewer_2_class"]
        if first not in _CLASSES or second not in _CLASSES:
            raise ValueError(f"both reviewers must classify {row['candidate_id']} as P0-P3")
        if not row["reviewer_1_source"].strip() or not row["reviewer_2_source"].strip():
            raise ValueError(f"both reviewers must provide evidence for {row['candidate_id']}")
        if first == second:
            agreements += 1
            if row["resolution_class"] not in {"", first}:
                raise ValueError(f"agreement resolution conflicts for {row['candidate_id']}")
            decision = first
        else:
            if row["resolution_class"] not in _CLASSES or not row["resolution_source"].strip():
                raise ValueError(f"reviewer disagreement is unresolved for {row['candidate_id']}")
            decision = row["resolution_class"]
        approved = row["approved_output"].strip()
        if decision == "P1" and not approved:
            raise ValueError(f"P1 requires approved_output for {row['candidate_id']}")
        if decision != "P1" and approved:
            raise ValueError(f"approved_output is only valid for P1: {row['candidate_id']}")
        resolved.append((row, decision))

    output_dir.mkdir(parents=True, exist_ok=True)
    rules = [{
        "rule_id": row["candidate_id"],
        "policy_class": decision,
        "match_type": "exact",
        "pattern": row["surface"],
        "approved_output": row["approved_output"].strip() or None,
        "source": row["resolution_source"].strip()
        or f"reviewer evidence: {row['reviewer_1_source']} | {row['reviewer_2_source']}",
    } for row, decision in resolved]
    policy = {
        "schema_version": 1,
        "policy_id": policy_id,
        "policy_version": policy_version,
        "rules": rules,
    }
    policy_path = output_dir / "pilot-policy.json"
    policy_path.write_text(json.dumps(policy, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    distribution = Counter(decision for _, decision in resolved)
    evidence: dict[str, object] = {
        "schema_version": 1,
        "partition": "calibration",
        "official_test_status": "sealed-not-read-or-transformed",
        "reviewed_pilot_sha256": sha256_file(reviewed_path),
        "policy_sha256": sha256_file(policy_path),
        "candidate_count": len(resolved),
        "agreements": agreements,
        "disagreements": len(resolved) - agreements,
        "agreement_rate": agreements / len(resolved),
        "class_distribution": {key: distribution.get(key, 0) for key in sorted(_CLASSES)},
    }
    evidence_path = output_dir / "pilot-review-evidence.json"
    evidence_path.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return evidence


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    pilot = commands.add_parser("pilot")
    pilot.add_argument("queue", type=Path)
    pilot.add_argument("output_dir", type=Path)
    resolve = commands.add_parser("resolve")
    resolve.add_argument("queue", type=Path)
    resolve.add_argument("reviewed_pilot", type=Path)
    resolve.add_argument("pilot_manifest", type=Path)
    resolve.add_argument("output_dir", type=Path)
    resolve.add_argument("--policy-id", required=True)
    resolve.add_argument("--policy-version", required=True)
    args = parser.parse_args(argv)
    if args.command == "pilot":
        result = generate_pilot(args.queue, args.output_dir)
    else:
        result = resolve_pilot(
            args.queue, args.reviewed_pilot, args.pilot_manifest, args.output_dir,
            args.policy_id, args.policy_version,
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
