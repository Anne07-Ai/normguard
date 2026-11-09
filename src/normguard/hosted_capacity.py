"""Representative capacity runner for the private Hugging Face pilot."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import threading
import time
import tracemalloc
import urllib.request
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

from .capacity import (
    CapacityIdentity,
    CapacityProfile,
    CapacitySamples,
    CapacityScope,
    assert_capacity_passed,
    run_capacity_harness,
    write_capacity_evidence,
)
from .safety_contracts import canonical_json_bytes

HF_TOKEN_ENV = "HF_TOKEN"
SERVICE_TOKEN_ENV = "NORMGUARD_BEARER_TOKEN"
RATE_LIMIT_RESET_SECONDS = 61
DEFAULT_ENDPOINT = (
    "https://lakshmianne-normguard-private-pilot.hf.space/v1/normalise"
)
_WORKLOAD = {
    "content_type": "application/json",
    "inputs": [
        "SKU-42 ACME LTD",
        "NORMGUARD-PILOT",
        "USB-C 60W",
        "R134A 110V",
    ],
    "response_contract": {
        "keys": ["error_code", "output_text", "schema_version", "status"],
        "schema_version": 1,
        "status": "accepted",
    },
}
WORKLOAD_SHA256 = hashlib.sha256(canonical_json_bytes(_WORKLOAD)).hexdigest()
Opener = Callable[..., object]


def representative_profile() -> CapacityProfile:
    """Return the frozen CPU Basic controlled-pilot profile."""

    return CapacityProfile(
        profile_id="hf-cpu-basic-private-pilot",
        profile_version="1.0.0",
        scope=CapacityScope.REPRESENTATIVE,
        concurrency=4,
        warmup_requests=20,
        measured_requests=1_000,
        minimum_duration_seconds=600,
        latency_p95_budget_ms=1_000,
        minimum_throughput_per_second=1.5,
        maximum_error_rate=0,
        maximum_peak_traced_bytes=64_000_000,
        maximum_process_cpu_seconds=180,
    )


def _require_secret(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name, "")
    if not value or value != value.strip() or len(value) < 16:
        raise ValueError(f"{name} is missing or invalid")
    return value


def _validate_endpoint(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "lakshmianne-normguard-private-pilot.hf.space"
        or parsed.path != "/v1/normalise"
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
    ):
        raise ValueError("endpoint differs from the declared private pilot")
    return value


def _validate_response(value: object) -> None:
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "status",
        "output_text",
        "error_code",
    }:
        raise RuntimeError("hosted response differs from the closed schema")
    if (
        value["schema_version"] != 1
        or value["status"] != "accepted"
        or not isinstance(value["output_text"], str)
        or value["error_code"] is not None
    ):
        raise RuntimeError("hosted response was not accepted")


def build_operation(
    *,
    endpoint: str,
    hf_token: str,
    service_token: str,
    opener: Opener = urllib.request.urlopen,
) -> Callable[[], None]:
    """Build a bounded synthetic operation without exposing either credential."""

    endpoint = _validate_endpoint(endpoint)
    if not hf_token or not service_token:
        raise ValueError("both authentication tokens are required")
    counter = 0
    lock = threading.Lock()

    def operation() -> None:
        nonlocal counter
        with lock:
            text = _WORKLOAD["inputs"][counter % len(_WORKLOAD["inputs"])]
            counter += 1
        request = urllib.request.Request(
            endpoint,
            data=canonical_json_bytes({"text": text}),
            method="POST",
            headers={
                "Authorization": f"Bearer {hf_token}",
                "Content-Type": "application/json",
                "X-NormGuard-Token": service_token,
            },
        )
        with opener(request, timeout=20) as response:  # type: ignore[attr-defined]
            if response.status != 200:  # type: ignore[attr-defined]
                raise RuntimeError("hosted request failed")
            if response.headers.get_content_type() != "application/json":  # type: ignore[attr-defined]
                raise RuntimeError("hosted response media type is invalid")
            payload = response.read(4_097)  # type: ignore[attr-defined]
        if len(payload) > 4_096:
            raise RuntimeError("hosted response exceeds the bounded size")
        try:
            value = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise RuntimeError("hosted response is not valid JSON") from None
        _validate_response(value)

    return operation


def paced_sample_runner(
    profile: CapacityProfile,
    operation: Callable[[], object],
) -> CapacitySamples:
    """Run a representative profile at a deterministic, bounded arrival rate."""

    # Reset the single-principal fixed window before warm-up and before measurement.
    # This prevents unrelated operator checks or warm-up traffic from consuming the
    # representative profile's declared request budget.
    time.sleep(RATE_LIMIT_RESET_SECONDS)
    for _ in range(profile.warmup_requests):
        operation()
    time.sleep(RATE_LIMIT_RESET_SECONDS)

    interval_ns = int(
        profile.minimum_duration_seconds
        * 1_000_000_000
        / (profile.measured_requests - 1)
    )
    started_ns = time.perf_counter_ns()
    cpu_started = time.process_time()

    def measured_call(target_ns: int) -> tuple[int, bool]:
        remaining_ns = target_ns - time.perf_counter_ns()
        if remaining_ns > 0:
            time.sleep(remaining_ns / 1_000_000_000)
        request_started = time.perf_counter_ns()
        failed = False
        try:
            operation()
        except Exception:
            failed = True
        return time.perf_counter_ns() - request_started, failed

    tracemalloc.start()
    try:
        with ThreadPoolExecutor(max_workers=profile.concurrency) as executor:
            futures = tuple(
                executor.submit(measured_call, started_ns + index * interval_ns)
                for index in range(profile.measured_requests)
            )
            results = tuple(future.result() for future in futures)
        _, peak_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    return CapacitySamples(
        durations_ns=tuple(duration for duration, _ in results),
        error_count=sum(failed for _, failed in results),
        elapsed_ns=time.perf_counter_ns() - started_ns,
        peak_traced_bytes=peak_bytes,
        process_cpu_seconds=time.process_time() - cpu_started,
    )


def run_hosted_capacity(
    *,
    source_commit: str,
    artifact_sha256: str,
    target_environment_sha256: str,
    endpoint: str,
    hf_token: str,
    service_token: str,
    sample_runner: Callable[
        [CapacityProfile, Callable[[], object]], CapacitySamples
    ] = paced_sample_runner,
    opener: Opener = urllib.request.urlopen,
) -> dict[str, object]:
    identity = CapacityIdentity(
        source_commit=source_commit,
        artifact_sha256=artifact_sha256,
        target_environment_sha256=target_environment_sha256,
        workload_sha256=WORKLOAD_SHA256,
    )
    operation = build_operation(
        endpoint=endpoint,
        hf_token=hf_token,
        service_token=service_token,
        opener=opener,
    )
    return run_capacity_harness(
        profile=representative_profile(),
        identity=identity,
        operation=operation,
        sample_runner=sample_runner,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the private Hugging Face representative capacity profile"
    )
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--artifact-sha256", required=True)
    parser.add_argument("--target-environment-sha256", required=True)
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/capacity/hf-private-representative.json"),
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    envelope = run_hosted_capacity(
        source_commit=args.source_commit,
        artifact_sha256=args.artifact_sha256,
        target_environment_sha256=args.target_environment_sha256,
        endpoint=args.endpoint,
        hf_token=_require_secret(os.environ, HF_TOKEN_ENV),
        service_token=_require_secret(os.environ, SERVICE_TOKEN_ENV),
    )
    write_capacity_evidence(envelope, args.output)
    evidence = envelope["evidence"]
    assert isinstance(evidence, dict)
    print(
        json.dumps(
            {
                "decision": evidence["decision"],
                "evidence_sha256": envelope["evidence_sha256"],
                "output": str(args.output),
                "scope": evidence["scope"],
            },
            sort_keys=True,
        )
    )
    assert_capacity_passed(envelope)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
