"""Deterministic, calibration-only diagnostics for mixed experiment evidence."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .esci import sha256_file
from .policy_io import LoadedPolicy, load_policy
from .reporting import EvidenceReporter

_SCHEMA_VERSION = 1
_METRIC = "ndcg_at_10"


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _query_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    baseline_rows = report["baseline"]["queries"]
    candidate_rows = report["candidate"]["queries"]
    baseline = {row["query_id"]: row for row in baseline_rows}
    candidate = {row["query_id"]: row for row in candidate_rows}
    if len(baseline) != len(baseline_rows) or len(candidate) != len(candidate_rows):
        raise ValueError("retrieval evidence contains duplicate query IDs")
    if set(baseline) != set(candidate):
        raise ValueError("baseline and candidate query IDs differ")

    rows: list[dict[str, Any]] = []
    for query_id in sorted(baseline):
        first, second = baseline[query_id], candidate[query_id]
        delta = float(second["metrics"][_METRIC]) - float(first["metrics"][_METRIC])
        rows.append({
            "query_id": query_id,
            "source_query": first["source_query"],
            "baseline_normalised_query": first["normalised_query"],
            "candidate_normalised_query": second["normalised_query"],
            "baseline_ndcg_at_10": float(first["metrics"][_METRIC]),
            "candidate_ndcg_at_10": float(second["metrics"][_METRIC]),
            "delta": delta,
            "normalisation_changed": (
                first["normalised_query"] != second["normalised_query"]
            ),
        })
    return rows


def _coverage(
    rows: list[dict[str, Any]], policy: LoadedPolicy
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    covered = 0
    by_class: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        query = str(row["source_query"]).casefold()
        classes = {
            rule.policy_class.value
            for rule in policy.rules
            if rule.pattern.casefold() in query
        }
        if classes:
            covered += 1
        for policy_class in sorted(classes):
            by_class[policy_class].append(float(row["delta"]))
    return (
        {
            "covered_queries": covered,
            "uncovered_queries": len(rows) - covered,
            "coverage_rate": covered / len(rows) if rows else 0.0,
            "method": "case-insensitive exact policy-pattern occurrence in source query",
        },
        {
            policy_class: {
                "query_count": len(deltas),
                "mean_ndcg_at_10_delta": _mean(deltas),
            }
            for policy_class, deltas in sorted(by_class.items())
        },
    )


def build_diagnostics(
    evidence_path: Path,
    policy_path: Path,
    output_dir: Path,
    *,
    top_n: int = 20,
) -> dict[str, Any]:
    """Validate calibration evidence and write descriptive diagnostic outputs."""
    if top_n <= 0:
        raise ValueError("top_n must be positive")
    if not EvidenceReporter.verify_json(evidence_path):
        raise ValueError("calibration evidence digest verification failed")
    envelope = json.loads(evidence_path.read_text(encoding="utf-8"))
    report = envelope["report"]
    if report.get("partition") != "calibration":
        raise ValueError("diagnostics accept calibration evidence only")
    if report["baseline_manifest"].get("final_test") or (
        report["candidate_manifest"].get("final_test")
    ):
        raise ValueError("final-test evidence is prohibited")
    policy = load_policy(policy_path)
    if report["candidate_manifest"].get("configuration_sha256") != policy.sha256:
        raise ValueError("policy digest does not match calibration evidence")

    rows = _query_rows(report)
    positive = [row for row in rows if row["delta"] > 0]
    negative = [row for row in rows if row["delta"] < 0]
    unchanged = [row for row in rows if row["delta"] == 0]
    changed = [row for row in rows if row["normalisation_changed"]]
    not_changed = [row for row in rows if not row["normalisation_changed"]]
    coverage, class_effects = _coverage(rows, policy)

    ranking_fields = (
        "query_id", "source_query", "baseline_normalised_query",
        "candidate_normalised_query", "delta",
    )
    ranked_positive = sorted(positive, key=lambda row: (-row["delta"], row["query_id"]))
    ranked_negative = sorted(negative, key=lambda row: (row["delta"], row["query_id"]))

    decision_evidence = report["decision_evidence"]
    baseline_latency = float(decision_evidence["baseline_p95_latency_ms"])
    candidate_latency = float(decision_evidence["candidate_p95_latency_ms"])
    payload: dict[str, Any] = {
        "schema_version": _SCHEMA_VERSION,
        "partition": "calibration",
        "official_test_status": "sealed-not-read-or-transformed",
        "evidence_sha256": sha256_file(evidence_path),
        "evidence_report_sha256": envelope["report_sha256"],
        "policy_sha256": policy.sha256,
        "decision": report["decision"],
        "query_movement": {
            "query_count": len(rows),
            "improved": len(positive),
            "regressed": len(negative),
            "unchanged": len(unchanged),
            "mean_ndcg_at_10_delta": _mean([float(row["delta"]) for row in rows]),
        },
        "normalisation_effect": {
            "changed_queries": len(changed),
            "changed_mean_ndcg_at_10_delta": _mean(
                [float(row["delta"]) for row in changed]
            ),
            "unchanged_queries": len(not_changed),
            "unchanged_mean_ndcg_at_10_delta": _mean(
                [float(row["delta"]) for row in not_changed]
            ),
        },
        "policy_coverage": coverage,
        "policy_class_effects": class_effects,
        "largest_improvements": [
            {key: row[key] for key in ranking_fields}
            for row in ranked_positive[:top_n]
        ],
        "largest_regressions": [
            {key: row[key] for key in ranking_fields}
            for row in ranked_negative[:top_n]
        ],
        "latency": {
            "baseline_p95_ms": baseline_latency,
            "candidate_p95_ms": candidate_latency,
            "incremental_p95_ms": candidate_latency - baseline_latency,
            "candidate_to_baseline_ratio": (
                candidate_latency / baseline_latency if baseline_latency else None
            ),
            "component_attribution": {
                "status": "not-instrumented",
                "policy_matching_ms": None,
                "spacy_morphology_ms": None,
                "retrieval_ms": None,
                "limitation": (
                    "existing evidence records end-to-end adapter latency only; "
                    "component attribution requires a separately versioned rerun"
                ),
            },
        },
        "interpretation": {
            "diagnostic_only": True,
            "primary_thresholds_unchanged": True,
            "statistical_significance_claimed": False,
            "allowed_next_decisions": [
                "version one calibration refinement and rerun the full calibration protocol",
                "stop and record that the POC utility hypothesis was not supported",
            ],
        },
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "calibration-diagnostics.json"
    json_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    movement = payload["query_movement"]
    normalisation = payload["normalisation_effect"]
    latency = payload["latency"]
    markdown = (
        "# NormGuard calibration diagnostics\n\n"
        f"**Decision:** \`{payload['decision']}\`  \n"
        f"**Evidence SHA-256:** \`{payload['evidence_sha256']}\`  \n"
        f"**Policy SHA-256:** \`{payload['policy_sha256']}\`\n\n"
        "## Query movement\n\n"
        "| Measure | Value |\n|---|---:|\n"
        f"| Queries | {movement['query_count']} |\n"
        f"| Improved | {movement['improved']} |\n"
        f"| Regressed | {movement['regressed']} |\n"
        f"| Unchanged | {movement['unchanged']} |\n"
        f"| Mean nDCG@10 delta | {movement['mean_ndcg_at_10_delta']:+.6f} |\n\n"
        "## Normalisation effect\n\n"
        f"- Changed queries: {normalisation['changed_queries']}\n"
        f"- Unchanged queries: {normalisation['unchanged_queries']}\n"
        f"- Policy-covered queries: {coverage['covered_queries']}\n\n"
        "## Latency\n\n"
        f"- Baseline p95: {latency['baseline_p95_ms']:.6f} ms\n"
        f"- Candidate p95: {latency['candidate_p95_ms']:.6f} ms\n"
        f"- Incremental p95: {latency['incremental_p95_ms']:.6f} ms\n"
        "- Component attribution: not instrumented in the existing evidence\n\n"
        "## Governance\n\n"
        "- Descriptive calibration analysis only.\n"
        "- Primary thresholds are unchanged.\n"
        "- Development and official-test data were not read.\n"
        "- No statistical-significance claim is made.\n"
    )
    markdown_path = output_dir / "calibration-diagnostics.md"
    markdown_path.write_text(markdown, encoding="utf-8")
    return {
        **payload,
        "outputs": {
            json_path.name: {"sha256": sha256_file(json_path)},
            markdown_path.name: {"sha256": sha256_file(markdown_path)},
        },
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path)
    parser.add_argument("policy", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--top-n", type=int, default=20)
    args = parser.parse_args(argv)
    result = build_diagnostics(
        args.evidence, args.policy, args.output_dir, top_n=args.top_n
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
