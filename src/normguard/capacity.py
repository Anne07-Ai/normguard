"""Deployment-neutral capacity contract and synthetic execution harness."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
import tracemalloc
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path

from .performance import build_synthetic_fixture
from .runtime import BoundedSafetyRuntime
from .safety_contracts import canonical_json_bytes

CAPACITY_SCHEMA_VERSION = 1
CAPACITY_PROTOCOL_VERSION = "NG-CAPACITY-001"
_HEX = frozenset("0123456789abcdef")
_EVIDENCE_KEYS = {
    "checks",
    "decision",
    "identities",
    "limitations",
    "metrics",
    "profile",
    "protocol",
    "resources",
    "schema_version",
    "scope",
}
_CHECK_KEYS = {
    "duration",
    "error_rate",
    "latency_p95",
    "peak_traced_bytes",
    "process_cpu_seconds",
    "throughput",
}
_IDENTITY_KEYS = {
    "artifact_sha256",
    "profile_sha256",
    "source_commit",
    "target_environment_sha256",
    "workload_sha256",
}
_METRIC_KEYS = {
    "error_count",
    "error_rate",
    "latency_max_ms",
    "latency_p50_ms",
    "latency_p95_ms",
    "measured_requests",
    "observed_duration_seconds",
    "throughput_per_second",
}
_PROFILE_KEYS = {
    "concurrency",
    "latency_p95_budget_ms",
    "maximum_error_rate",
    "maximum_peak_traced_bytes",
    "maximum_process_cpu_seconds",
    "measured_requests",
    "minimum_duration_seconds",
    "minimum_throughput_per_second",
    "profile_id",
    "profile_version",
    "scope",
    "warmup_requests",
}
_RESOURCE_KEYS = {"peak_traced_bytes", "process_cpu_seconds"}
_LIMITATIONS = (
    "local smoke evidence is not production capacity or SLA evidence",
    "representative evidence is valid only for its declared target environment",
    "synthetic workload results do not authorize customer traffic or replay",
    "tracemalloc excludes native allocations and total process RSS",
)


class CapacityScope(StrEnum):
    LOCAL_SMOKE = "local-smoke"
    REPRESENTATIVE = "representative"


class CapacityDecision(StrEnum):
    PASS = "PASS"
    STOP = "STOP"


@dataclass(frozen=True, slots=True)
class CapacityProfile:
    """Frozen workload and acceptance budgets for one capacity execution."""

    profile_id: str
    profile_version: str
    scope: CapacityScope
    concurrency: int
    warmup_requests: int
    measured_requests: int
    minimum_duration_seconds: float
    latency_p95_budget_ms: float
    minimum_throughput_per_second: float
    maximum_error_rate: float
    maximum_peak_traced_bytes: int
    maximum_process_cpu_seconds: float

    def __post_init__(self) -> None:
        if not self.profile_id or len(self.profile_id) > 80:
            raise ValueError("profile_id must contain 1-80 characters")
        if not self.profile_version or len(self.profile_version) > 40:
            raise ValueError("profile_version must contain 1-40 characters")
        if not isinstance(self.scope, CapacityScope):
            raise ValueError("scope must be a CapacityScope")
        for name in ("concurrency", "warmup_requests", "measured_requests"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if self.concurrency > 256 or self.measured_requests > 10_000_000:
            raise ValueError("profile exceeds the bounded execution limit")
        for name in (
            "minimum_duration_seconds",
            "latency_p95_budget_ms",
            "minimum_throughput_per_second",
            "maximum_error_rate",
            "maximum_process_cpu_seconds",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name} must be numeric")
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
        if self.latency_p95_budget_ms == 0 or self.minimum_throughput_per_second == 0:
            raise ValueError("latency and throughput budgets must be positive")
        if not 0 <= self.maximum_error_rate <= 1:
            raise ValueError("maximum_error_rate must be between zero and one")
        if (
            isinstance(self.maximum_peak_traced_bytes, bool)
            or not isinstance(self.maximum_peak_traced_bytes, int)
            or self.maximum_peak_traced_bytes < 1
        ):
            raise ValueError("maximum_peak_traced_bytes must be a positive integer")
        if self.scope is CapacityScope.REPRESENTATIVE:
            if self.minimum_duration_seconds < 300:
                raise ValueError("representative profiles require at least 300 seconds")
            if self.measured_requests < 1_000:
                raise ValueError("representative profiles require at least 1000 requests")

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["scope"] = self.scope.value
        return value

    @property
    def sha256(self) -> str:
        return hashlib.sha256(canonical_json_bytes(self.to_dict())).hexdigest()


@dataclass(frozen=True, slots=True)
class CapacityIdentity:
    source_commit: str
    artifact_sha256: str
    target_environment_sha256: str
    workload_sha256: str

    def __post_init__(self) -> None:
        _require_hex(self.source_commit, 40, "source_commit")
        for name in (
            "artifact_sha256",
            "target_environment_sha256",
            "workload_sha256",
        ):
            _require_hex(getattr(self, name), 64, name)

    def to_dict(self, profile_sha256: str) -> dict[str, str]:
        return {
            **asdict(self),
            "profile_sha256": profile_sha256,
        }


@dataclass(frozen=True, slots=True)
class CapacitySamples:
    durations_ns: tuple[int, ...]
    error_count: int
    elapsed_ns: int
    peak_traced_bytes: int
    process_cpu_seconds: float

    def __post_init__(self) -> None:
        if not self.durations_ns or any(value < 0 for value in self.durations_ns):
            raise ValueError("durations must be a non-empty non-negative sequence")
        for name in ("error_count", "elapsed_ns", "peak_traced_bytes"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.error_count > len(self.durations_ns) or self.elapsed_ns < 1:
            raise ValueError("sample counts or elapsed time are invalid")
        if (
            isinstance(self.process_cpu_seconds, bool)
            or not isinstance(self.process_cpu_seconds, (int, float))
            or not math.isfinite(self.process_cpu_seconds)
            or self.process_cpu_seconds < 0
        ):
            raise ValueError("process_cpu_seconds must be finite and non-negative")


SampleRunner = Callable[[CapacityProfile, Callable[[], object]], CapacitySamples]


def smoke_profile() -> CapacityProfile:
    return CapacityProfile(
        profile_id="synthetic-local-smoke",
        profile_version="1.0.0",
        scope=CapacityScope.LOCAL_SMOKE,
        concurrency=4,
        warmup_requests=4,
        measured_requests=40,
        minimum_duration_seconds=0,
        latency_p95_budget_ms=100,
        minimum_throughput_per_second=10,
        maximum_error_rate=0,
        maximum_peak_traced_bytes=32_000_000,
        maximum_process_cpu_seconds=30,
    )


def _require_hex(value: object, length: int, name: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != length
        or any(character not in _HEX for character in value)
    ):
        raise ValueError(f"{name} must be a lowercase {length}-character digest")


def _default_sample_runner(
    profile: CapacityProfile,
    operation: Callable[[], object],
) -> CapacitySamples:
    for _ in range(profile.warmup_requests):
        operation()

    def measured_call() -> tuple[int, bool]:
        started = time.perf_counter_ns()
        failed = False
        try:
            operation()
        except Exception:  # The evidence records only a bounded count, never exception text.
            failed = True
        return time.perf_counter_ns() - started, failed

    cpu_started = time.process_time()
    elapsed_started = time.perf_counter_ns()
    tracemalloc.start()
    try:
        with ThreadPoolExecutor(max_workers=profile.concurrency) as executor:
            results = tuple(
                executor.map(
                    lambda _: measured_call(),
                    range(profile.measured_requests),
                )
            )
        _, peak_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    elapsed_ns = time.perf_counter_ns() - elapsed_started
    cpu_seconds = time.process_time() - cpu_started
    return CapacitySamples(
        tuple(duration for duration, _ in results),
        sum(failed for _, failed in results),
        elapsed_ns,
        peak_bytes,
        cpu_seconds,
    )


def _percentile_95(values: Sequence[float]) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)]


def run_capacity_harness(
    *,
    profile: CapacityProfile,
    identity: CapacityIdentity,
    operation: Callable[[], object],
    sample_runner: SampleRunner = _default_sample_runner,
) -> dict[str, object]:
    """Execute one bounded profile and return digest-verifiable aggregate evidence."""
    samples = sample_runner(profile, operation)
    if len(samples.durations_ns) != profile.measured_requests:
        raise ValueError("sample runner returned an unexpected number of samples")
    duration_ms = tuple(value / 1_000_000 for value in samples.durations_ns)
    elapsed_seconds = samples.elapsed_ns / 1_000_000_000
    metrics = {
        "error_count": samples.error_count,
        "error_rate": samples.error_count / profile.measured_requests,
        "latency_p50_ms": sorted(duration_ms)[len(duration_ms) // 2],
        "latency_p95_ms": _percentile_95(duration_ms),
        "latency_max_ms": max(duration_ms),
        "measured_requests": profile.measured_requests,
        "observed_duration_seconds": elapsed_seconds,
        "throughput_per_second": profile.measured_requests / elapsed_seconds,
    }
    resources = {
        "peak_traced_bytes": samples.peak_traced_bytes,
        "process_cpu_seconds": samples.process_cpu_seconds,
    }
    values = (*metrics.values(), *resources.values())
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
        for value in values
    ):
        raise RuntimeError("capacity harness produced an invalid metric")
    checks = {
        "duration": elapsed_seconds >= profile.minimum_duration_seconds,
        "error_rate": metrics["error_rate"] <= profile.maximum_error_rate,
        "latency_p95": metrics["latency_p95_ms"] <= profile.latency_p95_budget_ms,
        "peak_traced_bytes": (
            resources["peak_traced_bytes"] <= profile.maximum_peak_traced_bytes
        ),
        "process_cpu_seconds": (
            resources["process_cpu_seconds"] <= profile.maximum_process_cpu_seconds
        ),
        "throughput": (
            metrics["throughput_per_second"]
            >= profile.minimum_throughput_per_second
        ),
    }
    decision = CapacityDecision.PASS if all(checks.values()) else CapacityDecision.STOP
    evidence = {
        "schema_version": CAPACITY_SCHEMA_VERSION,
        "protocol": CAPACITY_PROTOCOL_VERSION,
        "scope": profile.scope.value,
        "decision": decision.value,
        "identities": identity.to_dict(profile.sha256),
        "profile": profile.to_dict(),
        "metrics": metrics,
        "resources": resources,
        "checks": checks,
        "limitations": list(_LIMITATIONS),
    }
    digest = hashlib.sha256(canonical_json_bytes(evidence)).hexdigest()
    return {"evidence": evidence, "evidence_sha256": digest}


def verify_capacity_envelope(envelope: Mapping[str, object]) -> None:
    if set(envelope) != {"evidence", "evidence_sha256"}:
        raise ValueError("capacity envelope keys differ from schema")
    evidence = envelope["evidence"]
    if not isinstance(evidence, dict) or set(evidence) != _EVIDENCE_KEYS:
        raise ValueError("capacity evidence keys differ from schema")
    digest = envelope["evidence_sha256"]
    _require_hex(digest, 64, "evidence_sha256")
    if hashlib.sha256(canonical_json_bytes(evidence)).hexdigest() != digest:
        raise ValueError("capacity evidence digest mismatch")
    if evidence["schema_version"] != CAPACITY_SCHEMA_VERSION:
        raise ValueError("unsupported capacity evidence schema")
    if evidence["protocol"] != CAPACITY_PROTOCOL_VERSION:
        raise ValueError("unsupported capacity protocol")
    try:
        scope = CapacityScope(evidence["scope"])
    except (TypeError, ValueError):
        raise ValueError("capacity scope is invalid") from None
    if evidence["decision"] not in {item.value for item in CapacityDecision}:
        raise ValueError("capacity decision is invalid")
    checks = evidence["checks"]
    if (
        not isinstance(checks, dict)
        or set(checks) != _CHECK_KEYS
        or any(not isinstance(value, bool) for value in checks.values())
    ):
        raise ValueError("capacity checks differ from schema")
    profile_value = evidence["profile"]
    if not isinstance(profile_value, dict) or set(profile_value) != _PROFILE_KEYS:
        raise ValueError("capacity profile differs from schema")
    try:
        profile = CapacityProfile(
            **{**profile_value, "scope": CapacityScope(profile_value["scope"])}
        )
    except (TypeError, ValueError):
        raise ValueError("capacity profile is invalid") from None
    if profile.scope is not scope:
        raise ValueError("capacity profile scope mismatch")
    identities = evidence["identities"]
    if not isinstance(identities, dict) or set(identities) != _IDENTITY_KEYS:
        raise ValueError("capacity identities differ from schema")
    try:
        CapacityIdentity(
            identities["source_commit"],
            identities["artifact_sha256"],
            identities["target_environment_sha256"],
            identities["workload_sha256"],
        )
        _require_hex(identities["profile_sha256"], 64, "profile_sha256")
    except (TypeError, ValueError):
        raise ValueError("capacity identities are invalid") from None
    if identities["profile_sha256"] != profile.sha256:
        raise ValueError("capacity profile digest mismatch")
    metrics = evidence["metrics"]
    resources = evidence["resources"]
    if not isinstance(metrics, dict) or set(metrics) != _METRIC_KEYS:
        raise ValueError("capacity metrics differ from schema")
    if not isinstance(resources, dict) or set(resources) != _RESOURCE_KEYS:
        raise ValueError("capacity resources differ from schema")
    values = (*metrics.values(), *resources.values())
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
        for value in values
    ):
        raise ValueError("capacity metrics are invalid")
    if metrics["measured_requests"] != profile.measured_requests:
        raise ValueError("capacity request count mismatch")
    if metrics["observed_duration_seconds"] <= 0:
        raise ValueError("capacity duration must be positive")
    if metrics["error_count"] > profile.measured_requests:
        raise ValueError("capacity error count exceeds request count")
    expected_error_rate = metrics["error_count"] / profile.measured_requests
    if not math.isclose(metrics["error_rate"], expected_error_rate):
        raise ValueError("capacity error rate mismatch")
    expected_throughput = (
        profile.measured_requests / metrics["observed_duration_seconds"]
    )
    if not math.isclose(metrics["throughput_per_second"], expected_throughput):
        raise ValueError("capacity throughput mismatch")
    if not (
        metrics["latency_p50_ms"]
        <= metrics["latency_p95_ms"]
        <= metrics["latency_max_ms"]
    ):
        raise ValueError("capacity latency ordering is invalid")
    if evidence["limitations"] != list(_LIMITATIONS):
        raise ValueError("capacity limitations differ from schema")
    expected_checks = {
        "duration": (
            metrics["observed_duration_seconds"] >= profile.minimum_duration_seconds
        ),
        "error_rate": metrics["error_rate"] <= profile.maximum_error_rate,
        "latency_p95": metrics["latency_p95_ms"] <= profile.latency_p95_budget_ms,
        "peak_traced_bytes": (
            resources["peak_traced_bytes"] <= profile.maximum_peak_traced_bytes
        ),
        "process_cpu_seconds": (
            resources["process_cpu_seconds"] <= profile.maximum_process_cpu_seconds
        ),
        "throughput": (
            metrics["throughput_per_second"]
            >= profile.minimum_throughput_per_second
        ),
    }
    if checks != expected_checks:
        raise ValueError("capacity checks do not match profile and metrics")
    expected = (
        CapacityDecision.PASS.value
        if all(checks.values())
        else CapacityDecision.STOP.value
    )
    if evidence["decision"] != expected:
        raise ValueError("capacity decision does not match checks")


def write_capacity_evidence(envelope: Mapping[str, object], output: Path) -> None:
    verify_capacity_envelope(envelope)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(canonical_json_bytes(dict(envelope)) + b"\n")


def load_capacity_evidence(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("capacity evidence must be an object")
    verify_capacity_envelope(value)
    return value


def assert_capacity_passed(envelope: Mapping[str, object]) -> None:
    verify_capacity_envelope(envelope)
    evidence = envelope["evidence"]
    assert isinstance(evidence, dict)
    if evidence["decision"] != CapacityDecision.PASS.value:
        checks = evidence["checks"]
        assert isinstance(checks, dict)
        failed = sorted(name for name, passed in checks.items() if not passed)
        raise RuntimeError(f"capacity profile stopped: {','.join(failed)}")


def run_local_smoke(
    *, source_commit: str, artifact_sha256: str, target_environment_sha256: str
) -> dict[str, object]:
    fixture = build_synthetic_fixture(rule_count=20, match_count=5)
    runtime = BoundedSafetyRuntime(fixture.verified_policy)
    identity = CapacityIdentity(
        source_commit,
        artifact_sha256,
        target_environment_sha256,
        fixture.sha256,
    )
    return run_capacity_harness(
        profile=smoke_profile(),
        identity=identity,
        operation=lambda: runtime.normalise(fixture.text),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the local synthetic capacity smoke")
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--artifact-sha256", required=True)
    parser.add_argument("--target-environment-sha256", required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/capacity/local-smoke.json"),
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    envelope = run_local_smoke(
        source_commit=args.source_commit,
        artifact_sha256=args.artifact_sha256,
        target_environment_sha256=args.target_environment_sha256,
    )
    write_capacity_evidence(envelope, args.output)
    print(
        json.dumps(
            {
                "decision": envelope["evidence"]["decision"],  # type: ignore[index]
                "evidence_sha256": envelope["evidence_sha256"],
                "scope": CapacityScope.LOCAL_SMOKE.value,
            },
            sort_keys=True,
        )
    )
    assert_capacity_passed(envelope)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
