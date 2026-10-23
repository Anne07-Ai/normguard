"""Synthetic, core-only performance contract and evidence generation."""

from __future__ import annotations

import hashlib
import json
import math
import platform
import sys
import time
import tracemalloc
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from typing import Any

from .contracts import PolicyClass, PolicyRule
from .policy_verification import VerifiedPolicy
from .runtime import BoundedSafetyRuntime
from .safety_contracts import (
    PolicyPayload,
    PolicyProvenance,
    SchemaCompatibility,
    canonical_json_bytes,
)

PERFORMANCE_SCHEMA_VERSION = 1
FIXTURE_VERSION = "synthetic-core-v1"


@dataclass(frozen=True, slots=True)
class PerformanceBudget:
    construction_p95_ms: float = 250.0
    normalisation_p95_ms: float = 100.0
    normalisation_max_ms: float = 500.0
    minimum_throughput_per_second: float = 10.0
    maximum_peak_bytes: int = 32_000_000
    maximum_evidence_bytes: int = 200_000

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name} must be numeric")
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")

    def to_dict(self) -> dict[str, float | int]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SyntheticFixture:
    verified_policy: VerifiedPolicy
    text: str
    rule_count: int
    expected_match_count: int
    sha256: str


def build_synthetic_fixture(*, rule_count: int = 100, match_count: int = 20) -> SyntheticFixture:
    if isinstance(rule_count, bool) or not isinstance(rule_count, int) or rule_count < 1:
        raise ValueError("rule_count must be a positive integer")
    if isinstance(match_count, bool) or not isinstance(match_count, int):
        raise ValueError("match_count must be an integer")
    if match_count < 0 or match_count > rule_count:
        raise ValueError("match_count must be between zero and rule_count")

    rules = tuple(
        PolicyRule(
            f"synthetic-sku-{index:04d}",
            PolicyClass.EXACT_PRESERVE,
            "exact",
            f"SYNTH-SKU-{index:04d}",
            source="synthetic-performance-fixture",
        )
        for index in range(rule_count)
    )
    payload = PolicyPayload(
        policy_id="synthetic-performance",
        policy_version="1.0.0",
        compatibility=SchemaCompatibility(1, 1),
        provenance=PolicyProvenance(
            "synthetic-benchmark",
            FIXTURE_VERSION,
            "2026-10-23T00:00:00Z",
        ),
        rules=rules,
    )
    verified = VerifiedPolicy(
        payload=payload,
        payload_sha256=payload.sha256,
        signing_key_id="synthetic-key",
        signing_key_sha256="a" * 64,
        rollback_authorized=False,
        verification_time=datetime(2026, 10, 23, tzinfo=UTC),
    )
    matched = " ".join(rule.pattern for rule in rules[:match_count])
    text = f"synthetic benchmark input {matched} " + ("bounded filler text " * 100)
    identity = {
        "fixture_version": FIXTURE_VERSION,
        "policy_sha256": payload.sha256,
        "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "rule_count": rule_count,
        "match_count": match_count,
    }
    return SyntheticFixture(
        verified,
        text,
        rule_count,
        match_count,
        hashlib.sha256(canonical_json_bytes(identity)).hexdigest(),
    )


def _milliseconds(samples_ns: list[int]) -> list[float]:
    return [sample / 1_000_000 for sample in samples_ns]


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        raise ValueError("at least one sample is required")
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def _time_many(operation: Callable[[], Any], samples: int) -> list[int]:
    durations = []
    for _ in range(samples):
        started = time.perf_counter_ns()
        operation()
        durations.append(time.perf_counter_ns() - started)
    return durations


def run_performance_contract(
    *,
    budget: PerformanceBudget | None = None,
    warmup_count: int = 10,
    construction_samples: int = 25,
    normalisation_samples: int = 100,
    clock: Callable[[], float] | None = None,
) -> dict[str, Any]:
    """Run the frozen synthetic benchmark and return a closed evidence envelope."""
    for name, value in (
        ("warmup_count", warmup_count),
        ("construction_samples", construction_samples),
        ("normalisation_samples", normalisation_samples),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{name} must be a positive integer")

    selected_budget = budget or PerformanceBudget()
    fixture = build_synthetic_fixture()
    for _ in range(warmup_count):
        BoundedSafetyRuntime(fixture.verified_policy).normalise(fixture.text)

    construction_ns = _time_many(
        lambda: BoundedSafetyRuntime(fixture.verified_policy), construction_samples
    )
    runtime = BoundedSafetyRuntime(fixture.verified_policy)
    normalisation_ns = _time_many(
        lambda: runtime.normalise(fixture.text), normalisation_samples
    )
    result = runtime.normalise(fixture.text)
    match_count = sum(span.rule_id != "unprotected" for span in result.evidence.spans)
    if match_count != fixture.expected_match_count:
        raise RuntimeError("synthetic benchmark match count changed")

    tracemalloc.start()
    try:
        runtime.normalise(fixture.text)
        _, peak_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    construction_ms = _milliseconds(construction_ns)
    normalisation_ms = _milliseconds(normalisation_ns)
    total_seconds = sum(normalisation_ns) / 1_000_000_000
    throughput = normalisation_samples / total_seconds
    evidence_bytes = len(canonical_json_bytes(result.evidence.to_dict()))
    metrics = {
        "construction_p50_ms": median(construction_ms),
        "construction_p95_ms": _percentile(construction_ms, 0.95),
        "normalisation_p50_ms": median(normalisation_ms),
        "normalisation_p95_ms": _percentile(normalisation_ms, 0.95),
        "normalisation_max_ms": max(normalisation_ms),
        "throughput_per_second": throughput,
        "peak_traced_bytes": peak_bytes,
        "evidence_bytes": evidence_bytes,
    }
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
        for value in metrics.values()
    ):
        raise RuntimeError("benchmark produced an invalid metric")

    checks = {
        "construction_p95_ms": metrics["construction_p95_ms"]
        <= selected_budget.construction_p95_ms,
        "normalisation_p95_ms": metrics["normalisation_p95_ms"]
        <= selected_budget.normalisation_p95_ms,
        "normalisation_max_ms": metrics["normalisation_max_ms"]
        <= selected_budget.normalisation_max_ms,
        "minimum_throughput_per_second": metrics["throughput_per_second"]
        >= selected_budget.minimum_throughput_per_second,
        "maximum_peak_bytes": metrics["peak_traced_bytes"]
        <= selected_budget.maximum_peak_bytes,
        "maximum_evidence_bytes": metrics["evidence_bytes"]
        <= selected_budget.maximum_evidence_bytes,
    }
    evidence = {
        "schema_version": PERFORMANCE_SCHEMA_VERSION,
        "status": "PASS" if all(checks.values()) else "FAIL",
        "fixture": {
            "version": FIXTURE_VERSION,
            "sha256": fixture.sha256,
            "input_bytes": len(fixture.text.encode()),
            "rule_count": fixture.rule_count,
            "expected_match_count": fixture.expected_match_count,
        },
        "protocol": {
            "warmup_count": warmup_count,
            "construction_samples": construction_samples,
            "normalisation_samples": normalisation_samples,
        },
        "environment": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform": platform.system().lower(),
            "machine": platform.machine().lower(),
        },
        "budget": selected_budget.to_dict(),
        "metrics": metrics,
        "checks": checks,
        "limitations": [
            "shared-runner results are regression evidence, not a production SLA",
            "synthetic fixtures do not establish workload-specific capacity",
            "tracemalloc reports Python allocations, not total process RSS",
        ],
    }
    if clock is not None:
        evidence["observed_at_unix_seconds"] = clock()
    digest = hashlib.sha256(canonical_json_bytes(evidence)).hexdigest()
    return {"evidence": evidence, "evidence_sha256": digest}


def verify_performance_envelope(envelope: dict[str, Any]) -> None:
    if set(envelope) != {"evidence", "evidence_sha256"}:
        raise ValueError("performance envelope keys differ from schema")
    evidence = envelope["evidence"]
    if not isinstance(evidence, dict):
        raise ValueError("performance evidence must be an object")
    digest = envelope["evidence_sha256"]
    if not isinstance(digest, str) or len(digest) != 64:
        raise ValueError("performance evidence digest is invalid")
    if hashlib.sha256(canonical_json_bytes(evidence)).hexdigest() != digest:
        raise ValueError("performance evidence digest mismatch")


def write_performance_evidence(envelope: dict[str, Any], output: Path) -> None:
    verify_performance_envelope(envelope)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(canonical_json_bytes(envelope) + b"\n")


def load_performance_evidence(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("performance evidence must be an object")
    verify_performance_envelope(value)
    return value


def assert_performance_passed(envelope: dict[str, Any]) -> None:
    verify_performance_envelope(envelope)
    if envelope["evidence"].get("status") != "PASS":
        failed = sorted(
            name for name, passed in envelope["evidence"].get("checks", {}).items() if not passed
        )
        raise RuntimeError(f"performance budget failed: {','.join(failed)}")


def main() -> int:
    output = Path(sys.argv[1]) if len(sys.argv) == 2 else Path(
        "artifacts/performance/safety-core-performance.json"
    )
    envelope = run_performance_contract()
    write_performance_evidence(envelope, output)
    print(json.dumps(envelope, indent=2, sort_keys=True))
    assert_performance_passed(envelope)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
