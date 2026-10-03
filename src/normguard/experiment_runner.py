"""Execute guarded, paired C0/C4 ESCI calibration and development experiments."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import time
from collections.abc import Sequence
from dataclasses import asdict
from importlib.metadata import version
from pathlib import Path

import numpy as np

from .adapters import C0Adapter, C4SpacyAdapter, NormalisationAdapter
from .contracts import RunManifest
from .esci import OFFICIAL_ARTIFACTS
from .esci_experiment import EsciPartition, load_partition
from .experiment import paired_statistics, select_bm25_parameters
from .policy_io import LoadedPolicy, load_policy, require_development_approval
from .reporting import DecisionEvidence, EvidenceReporter
from .retrieval import BM25EvaluationHarness, RetrievalEvaluation, compare_paired
from .safety import ObservedSpan, SafetyCase, TerminologySafetyAuditor

PROTOCOL_ID = "NG-POC-001"
PROTOCOL_VERSION = "0.1.0"
STATISTICS_SEED = 7
_GRID = tuple((k1, b) for k1 in (0.6, 0.9, 1.2, 1.5) for b in (0.25, 0.5, 0.75))


def _digest(value: object) -> str:
    data = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(data).hexdigest()


def _dataset_digest() -> str:
    return _digest({item.path: item.sha256 for item in OFFICIAL_ARTIFACTS})


def _evaluate(
    data: EsciPartition, adapter: NormalisationAdapter, k1: float, b: float
) -> RetrievalEvaluation:
    return BM25EvaluationHarness(k1=k1, b=b).evaluate(
        adapter=adapter,
        documents=data.documents,
        queries=data.queries,
        qrels=data.qrels,
        partition=data.partition,
    )


def _select_parameters(data: EsciPartition, baseline: C0Adapter) -> tuple[float, float]:
    if data.partition == "calibration":
        return 0.9, 0.75
    scores = {
        pair: _evaluate(data, baseline, *pair).aggregate_metrics["ndcg_at_10"]
        for pair in _GRID
    }
    return select_bm25_parameters(scores)


def _latency_ms(adapter: NormalisationAdapter, texts: Sequence[str]) -> float:
    sample = tuple(texts[: min(200, len(texts))])
    for text in sample:
        adapter.normalise(text)
    observed: list[float] = []
    for text in sample:
        start = time.perf_counter_ns()
        adapter.normalise(text)
        observed.append((time.perf_counter_ns() - start) / 1_000_000)
    return float(np.quantile(np.asarray(observed), 0.95))


def _manifest(
    configuration_id: str, configuration_sha256: str, data: EsciPartition
) -> RunManifest:
    return RunManifest(
        protocol_id=PROTOCOL_ID,
        protocol_version=PROTOCOL_VERSION,
        dataset_name="Amazon Shopping Queries Dataset (ESCI), reduced ranking version",
        dataset_sha256=_dataset_digest(),
        split_manifest_sha256=data.split_manifest_sha256,
        configuration_id=configuration_id,
        configuration_sha256=configuration_sha256,
        seed=STATISTICS_SEED,
        final_test=False,
        environment={
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
        dependency_versions={
            name: version(name) for name in ("bm25s", "ir-measures", "numpy", "spacy")
        },
    )


def run_experiment(
    *, data: EsciPartition, policy: LoadedPolicy, output_dir: Path
) -> dict[str, object]:
    """Run paired retrieval, statistics, safety gates and evidence reporting."""
    baseline_adapter = C0Adapter()
    candidate_adapter = C4SpacyAdapter(policy.rules)
    k1, b = _select_parameters(data, baseline_adapter)
    baseline = _evaluate(data, baseline_adapter, k1, b)
    candidate = _evaluate(data, candidate_adapter, k1, b)
    comparison = compare_paired(baseline, candidate)
    statistics = paired_statistics(
        comparison.metric_deltas["ndcg_at_10"], seed=STATISTICS_SEED
    )

    cases: list[SafetyCase] = []
    texts = [query.text for query in data.queries] + [doc.text for doc in data.documents]
    for index, text in enumerate(texts):
        result = candidate_adapter.normalise(text)
        cases.append(SafetyCase(
            f"{data.partition}:{index}", text, result.output_text,
            tuple(
                ObservedSpan.from_trace(trace)
                for trace in result.spans
                if trace.rule_id is not None
            ),
        ))
    safety = TerminologySafetyAuditor(policy.rules).audit(configuration_id="C4", cases=cases)
    baseline_latency = _latency_ms(baseline_adapter, texts)
    candidate_latency = _latency_ms(candidate_adapter, texts)
    baseline_manifest = _manifest("C0", _digest({"configuration": "C0"}), data)
    candidate_manifest = _manifest("C4", policy.sha256, data)
    reporter = EvidenceReporter()
    report = reporter.build(
        baseline_manifest=baseline_manifest,
        candidate_manifest=candidate_manifest,
        baseline=baseline,
        candidate=candidate,
        comparison=comparison,
        safety=safety,
        decision_evidence=DecisionEvidence(
            statistics.ci95_lower, baseline_latency, candidate_latency
        ),
    )
    hashes = reporter.write(report, output_dir)
    statistics_path = output_dir / "paired-statistics.json"
    statistics_path.write_text(
        json.dumps(asdict(statistics), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    hashes[statistics_path.name] = hashlib.sha256(statistics_path.read_bytes()).hexdigest()
    return {
        "decision": report.decision.value,
        "partition": data.partition,
        "k1": k1,
        "b": b,
        "queries": len(data.queries),
        "policy_sha256": policy.sha256,
        "outputs": dict(sorted(hashes.items())),
        "official_test_status": "sealed-not-read-or-transformed",
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset_dir", type=Path)
    parser.add_argument("artifact_dir", type=Path)
    parser.add_argument("policy", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("partition", choices=("calibration", "development"))
    parser.add_argument("--approval", type=Path)
    args = parser.parse_args(argv)
    policy = load_policy(args.policy)
    if args.partition == "development":
        if args.approval is None:
            parser.error("development requires --approval")
        require_development_approval(args.approval, policy.sha256)
    data = load_partition(args.dataset_dir, args.artifact_dir, args.partition)
    summary = run_experiment(data=data, policy=policy, output_dir=args.output_dir)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return {"PASS": 0, "REVIEW": 0, "FAIL": 1, "INCONCLUSIVE": 2}[
        str(summary["decision"])
    ]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
