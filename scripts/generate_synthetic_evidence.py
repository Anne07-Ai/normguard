"""Generate deterministic synthetic evidence for CI artifact verification."""

from pathlib import Path

from normguard import (
    Decision,
    DecisionEvidence,
    EvidenceReporter,
    PairedComparison,
    QueryEvaluation,
    RetrievalEvaluation,
    RunManifest,
    SafetyAuditResult,
)


def manifest(configuration_id: str) -> RunManifest:
    return RunManifest(
        protocol_id="NG-POC-001",
        protocol_version="0.1.0",
        dataset_name="synthetic-ci-only",
        dataset_sha256="a" * 64,
        split_manifest_sha256="b" * 64,
        configuration_id=configuration_id,
        configuration_sha256=("c" if configuration_id == "C0" else "d") * 64,
        seed=7,
    )


def retrieval(configuration_id: str, score: float) -> RetrievalEvaluation:
    return RetrievalEvaluation(
        configuration_id,
        "development",
        0.9,
        0.75,
        (QueryEvaluation("synthetic-q1", "shoes", "shoe", (), {"ndcg_at_10": score}),),
        {"ndcg_at_10": score},
    )


def main() -> None:
    baseline = retrieval("C0", 0.50)
    candidate = retrieval("C4", 0.51)
    comparison = PairedComparison(
        "C0",
        "C4",
        ("synthetic-q1",),
        {"ndcg_at_10": (0.01,)},
        {"ndcg_at_10": 0.01},
    )
    safety = SafetyAuditResult(
        "C4",
        Decision.PASS,
        ("synthetic CI safety evidence",),
        {
            "protected_terms": 1,
            "protected_term_violation_rate": 0.0,
            "exact_identifier_retention": 1.0,
            "harmful_transformation_rate": 0.0,
            "review_or_abstention_rate": 0.0,
            "s0_event_count": 0,
        },
        {},
        {},
        {"P0": 1},
        (),
    )
    reporter = EvidenceReporter()
    report = reporter.build(
        baseline_manifest=manifest("C0"),
        candidate_manifest=manifest("C4"),
        baseline=baseline,
        candidate=candidate,
        comparison=comparison,
        safety=safety,
        decision_evidence=DecisionEvidence(0.002, 5.0, 8.0),
    )
    reporter.write(report, Path("artifacts/ci-evidence"))


if __name__ == "__main__":
    main()
