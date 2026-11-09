"""Controlled-pilot protocol for the offline, single-tenant Safety Core."""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from .contracts import PolicyClass
from .runtime import RuntimeLimits
from .safety_contracts import EvidenceStatus, canonical_json_bytes
from .sdk import SDKError, build_safety_core, verify_policy

PILOT_SCHEMA_VERSION = 1
PILOT_PROTOCOL_VERSION = "NG-PILOT-001"
SEALED_DATA_STATUS = "development-and-official-test-not-read-or-transformed"
_HEX40 = frozenset("0123456789abcdef")


class PilotDecision(StrEnum):
    PASS = "PASS"
    STOP = "STOP"


class GateStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class DataClassification(StrEnum):
    SYNTHETIC = "synthetic"
    ORGANIZATION_APPROVED = "organization-approved"


@dataclass(frozen=True, slots=True)
class PilotCase:
    text: str
    expected_output: str
    expected_status: EvidenceStatus

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not self.text:
            raise ValueError("pilot text must be a non-empty string")
        if not isinstance(self.expected_output, str):
            raise ValueError("expected_output must be a string")
        if not isinstance(self.expected_status, EvidenceStatus):
            raise ValueError("expected_status must be an EvidenceStatus")

    def identity(self) -> dict[str, object]:
        return {
            "input_sha256": hashlib.sha256(self.text.encode()).hexdigest(),
            "expected_output_sha256": hashlib.sha256(
                self.expected_output.encode()
            ).hexdigest(),
            "expected_status": self.expected_status.value,
        }


@dataclass(frozen=True, slots=True)
class OperationalDrill:
    name: str
    passed: bool
    evidence_sha256: str

    def __post_init__(self) -> None:
        if self.name not in {"rollback", "key-compromise"}:
            raise ValueError("unsupported operational drill")
        if not isinstance(self.passed, bool):
            raise ValueError("passed must be boolean")
        _require_sha256(self.evidence_sha256, "evidence_sha256")


@dataclass(frozen=True, slots=True)
class PilotConfig:
    source_commit: str
    artifact_sha256: str
    data_classification: DataClassification
    repeat_count: int = 2
    latency_p95_budget_ms: float = 100.0
    minimum_throughput_per_second: float = 10.0

    def __post_init__(self) -> None:
        if (
            not isinstance(self.source_commit, str)
            or len(self.source_commit) != 40
            or any(character not in _HEX40 for character in self.source_commit)
        ):
            raise ValueError("source_commit must be a full lowercase Git SHA")
        _require_sha256(self.artifact_sha256, "artifact_sha256")
        if not isinstance(self.data_classification, DataClassification):
            raise ValueError("data_classification must be allowlisted")
        if (
            isinstance(self.repeat_count, bool)
            or not isinstance(self.repeat_count, int)
            or self.repeat_count < 2
        ):
            raise ValueError("repeat_count must be at least two")
        for name in ("latency_p95_budget_ms", "minimum_throughput_per_second"):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError(f"{name} must be finite and positive")

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["data_classification"] = self.data_classification.value
        return value


@dataclass(frozen=True, slots=True)
class PilotGate:
    gate_id: str
    name: str
    status: GateStatus
    owner: str
    hard_gate: bool
    evidence: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.gate_id,
            "name": self.name,
            "status": self.status.value,
            "owner": self.owner,
            "hard_gate": self.hard_gate,
            "evidence": list(self.evidence),
        }


def _require_sha256(value: str, name: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in _HEX40 for character in value)
    ):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")


def _percentile_95(values: Sequence[float]) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def _negative_policy_documents(
    policy_bundle: bytes, activation_state: bytes
) -> tuple[tuple[str, bytes, bytes], ...]:
    try:
        bundle = json.loads(policy_bundle)
        activation = json.loads(activation_state)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("pilot policy documents must be valid JSON") from None
    if not isinstance(bundle, dict) or not isinstance(activation, dict):
        raise ValueError("pilot policy documents must be JSON objects")

    unsigned = json.loads(json.dumps(bundle))
    unsigned["signature"] = None
    incompatible = json.loads(json.dumps(bundle))
    incompatible["payload"]["compatibility"] = {
        "minimum_runtime_schema": 9999,
        "maximum_runtime_schema": 9999,
    }
    rollback = json.loads(json.dumps(activation))
    rollback["minimum_version"] = "9999.0.0"
    return (
        ("tampered", policy_bundle + b"!", activation_state),
        ("unsigned", canonical_json_bytes(unsigned), activation_state),
        ("incompatible", canonical_json_bytes(incompatible), activation_state),
        ("rolled-back", policy_bundle, canonical_json_bytes(rollback)),
    )


def _integrity_controls_pass(
    policy_bundle: bytes,
    trust_store: bytes,
    activation_state: bytes,
    verification_time: datetime,
) -> bool:
    for _, changed_bundle, changed_activation in _negative_policy_documents(
        policy_bundle, activation_state
    ):
        try:
            verify_policy(
                changed_bundle,
                trust_store,
                changed_activation,
                verification_time=verification_time,
            )
        except SDKError:
            continue
        return False
    return True


def _gate(
    gate_id: str,
    name: str,
    passed: bool,
    owner: str,
    *evidence: str,
) -> PilotGate:
    return PilotGate(
        gate_id,
        name,
        GateStatus.PASS if passed else GateStatus.FAIL,
        owner,
        True,
        tuple(evidence),
    )


def run_controlled_pilot(
    *,
    policy_bundle: bytes,
    trust_store: bytes,
    activation_state: bytes,
    verification_time: datetime,
    cases: Sequence[PilotCase],
    config: PilotConfig,
    drills: Sequence[OperationalDrill],
    runtime_limits: RuntimeLimits | None = None,
    monotonic_ns: Callable[[], int] = time.perf_counter_ns,
) -> dict[str, object]:
    """Run the frozen pilot and return a digest-verifiable, privacy-safe envelope."""

    if not cases:
        raise ValueError("at least one pilot case is required")
    if len({drill.name for drill in drills}) != len(drills):
        raise ValueError("operational drills must be unique")

    integrity_passed = _integrity_controls_pass(
        policy_bundle, trust_store, activation_state, verification_time
    )
    core = build_safety_core(
        policy_bundle,
        trust_store,
        activation_state,
        verification_time=verification_time,
        runtime_limits=runtime_limits,
    )
    selected_limits = core.runtime_limits

    results = []
    durations_ns: list[int] = []
    determinism_mismatches = functional_mismatches = 0
    exact_total = exact_retained = unauthorized_transformations = 0
    status_counts: Counter[str] = Counter()
    for case in cases:
        first = None
        for repetition in range(config.repeat_count):
            started = monotonic_ns()
            result = core.normalise(case.text)
            finished = monotonic_ns()
            if finished < started:
                raise RuntimeError("monotonic clock moved backwards")
            if repetition == 0:
                durations_ns.append(finished - started)
                first = result
            elif result != first:
                determinism_mismatches += 1
        assert first is not None
        results.append(first)
        status_counts[first.status.value] += 1
        functional_mismatches += (
            first.output_text != case.expected_output
            or first.status is not case.expected_status
        )
        for span in first.evidence.spans:
            if span.policy_class is PolicyClass.EXACT_PRESERVE:
                exact_total += 1
                exact_retained += span.action == "preserved"
                unauthorized_transformations += span.action != "preserved"
            elif span.policy_class is PolicyClass.APPROVED_ALIAS:
                unauthorized_transformations += span.action != "transformed"
            elif span.policy_class is PolicyClass.REVIEW_OR_ABSTAIN:
                unauthorized_transformations += span.action == "transformed"

    terminal_failure_passed = False
    try:
        core.normalise("x" * (selected_limits.max_input_bytes + 1))
    except SDKError as exc:
        terminal_failure_passed = exc.code == "LIMIT_EXCEEDED"

    latency_ms = [duration / 1_000_000 for duration in durations_ns]
    total_seconds = sum(durations_ns) / 1_000_000_000
    throughput = len(durations_ns) / total_seconds if total_seconds else math.inf
    exact_retention = exact_retained / exact_total if exact_total else None
    corpus_identity = [case.identity() for case in cases]
    corpus_sha256 = hashlib.sha256(canonical_json_bytes(corpus_identity)).hexdigest()
    config_sha256 = hashlib.sha256(
        canonical_json_bytes(config.to_dict())
    ).hexdigest()

    review_seen = status_counts[EvidenceStatus.REVIEW_REQUIRED.value] > 0
    operation_results = {drill.name: drill.passed for drill in drills}
    drill_by_name = {drill.name: drill for drill in drills}
    operation_evidence = tuple(
        item
        for name in ("rollback", "key-compromise")
        for item in (
            f"{name}:{'pass' if name in drill_by_name and drill_by_name[name].passed else 'fail'}",
            *(
                (drill_by_name[name].evidence_sha256,)
                if name in drill_by_name
                else ()
            ),
        )
    )
    gates = (
        _gate(
            "G-01",
            "policy integrity negative controls",
            integrity_passed,
            "security-owner",
            "tampered",
            "unsigned",
            "incompatible",
            "rolled-back",
        ),
        _gate(
            "G-02",
            "terminology safety",
            exact_total > 0
            and exact_retention == 1.0
            and unauthorized_transformations == 0
            and functional_mismatches == 0,
            "policy-owner",
            f"protected-spans:{exact_total}",
        ),
        _gate(
            "G-03",
            "deterministic outcomes",
            determinism_mismatches == 0,
            "core-owner",
            f"repeat-count:{config.repeat_count}",
        ),
        _gate(
            "G-04",
            "explicit review and terminal failure routing",
            review_seen and terminal_failure_passed,
            "integration-owner",
            "review-required-observed",
            "limit-failure-observed",
        ),
        _gate(
            "G-05",
            "pilot performance budget",
            _percentile_95(latency_ms) <= config.latency_p95_budget_ms
            and throughput >= config.minimum_throughput_per_second,
            "platform-owner",
            "declared-hardware-required",
        ),
        _gate(
            "G-06",
            "privacy-safe evidence",
            True,
            "privacy-owner",
            "aggregate-and-digest-only-schema",
        ),
        _gate(
            "G-07",
            "rollback and key-compromise drills",
            operation_results == {"rollback": True, "key-compromise": True},
            "operations-owner",
            *operation_evidence,
        ),
        _gate(
            "G-08",
            "artifact and configuration traceability",
            True,
            "release-owner",
            config.source_commit,
            config.artifact_sha256,
            config_sha256,
            core.verified_policy.payload_sha256,
        ),
    )
    decision = (
        PilotDecision.PASS
        if all(gate.status is GateStatus.PASS for gate in gates if gate.hard_gate)
        else PilotDecision.STOP
    )
    record: dict[str, object] = {
        "schema_version": PILOT_SCHEMA_VERSION,
        "protocol": PILOT_PROTOCOL_VERSION,
        "decision": decision.value,
        "approved_scope": "controlled-offline-or-single-tenant-library-pilot",
        "data_classification": config.data_classification.value,
        "sealed_data_status": SEALED_DATA_STATUS,
        "identities": {
            "source_commit": config.source_commit,
            "artifact_sha256": config.artifact_sha256,
            "policy_sha256": core.verified_policy.payload_sha256,
            "configuration_sha256": config_sha256,
            "corpus_sha256": corpus_sha256,
        },
        "counts": {
            "cases": len(cases),
            "success": status_counts[EvidenceStatus.SUCCESS.value],
            "review_required": status_counts[EvidenceStatus.REVIEW_REQUIRED.value],
            "functional_mismatches": functional_mismatches,
            "determinism_mismatches": determinism_mismatches,
            "unauthorized_transformations": unauthorized_transformations,
        },
        "metrics": {
            "exact_identifier_retention": exact_retention,
            "latency_p95_ms": _percentile_95(latency_ms),
            "throughput_per_second": throughput,
        },
        "budgets": {
            "latency_p95_ms": config.latency_p95_budget_ms,
            "minimum_throughput_per_second": config.minimum_throughput_per_second,
        },
        "gates": [gate.to_dict() for gate in gates],
        "limitations": [
            "pilot evidence is not an external production-readiness certification",
            "pilot performance is valid only for the declared host and workload",
            "hosted multi-tenant use remains blocked by NG-T22",
            "no retrieval-utility claim is made",
        ],
    }
    encoded = canonical_json_bytes(record)
    for case in cases:
        for forbidden in (case.text, case.expected_output):
            if len(forbidden.encode()) >= 4 and forbidden.encode() in encoded:
                raise RuntimeError("privacy-safe evidence unexpectedly contains pilot text")
    return {
        "record": record,
        "record_sha256": hashlib.sha256(encoded).hexdigest(),
    }


def verify_pilot_envelope(envelope: dict[str, object]) -> None:
    if set(envelope) != {"record", "record_sha256"}:
        raise ValueError("pilot envelope keys differ from schema")
    record = envelope["record"]
    if not isinstance(record, dict):
        raise ValueError("pilot record must be an object")
    expected = hashlib.sha256(canonical_json_bytes(record)).hexdigest()
    if envelope["record_sha256"] != expected:
        raise ValueError("pilot evidence digest mismatch")
    required = {
        "schema_version",
        "protocol",
        "decision",
        "approved_scope",
        "data_classification",
        "sealed_data_status",
        "identities",
        "counts",
        "metrics",
        "budgets",
        "gates",
        "limitations",
    }
    if set(record) != required:
        raise ValueError("pilot record keys differ from schema")
    if record["schema_version"] != PILOT_SCHEMA_VERSION:
        raise ValueError("unsupported pilot schema version")
    if record["decision"] not in {item.value for item in PilotDecision}:
        raise ValueError("invalid pilot decision")
    identities = record["identities"]
    counts = record["counts"]
    metrics = record["metrics"]
    budgets = record["budgets"]
    gates = record["gates"]
    if not isinstance(identities, dict) or set(identities) != {
        "source_commit",
        "artifact_sha256",
        "policy_sha256",
        "configuration_sha256",
        "corpus_sha256",
    }:
        raise ValueError("pilot identities differ from schema")
    source_commit = identities["source_commit"]
    if (
        not isinstance(source_commit, str)
        or len(source_commit) != 40
        or any(character not in _HEX40 for character in source_commit)
    ):
        raise ValueError("invalid source commit identity")
    for name in (
        "artifact_sha256",
        "policy_sha256",
        "configuration_sha256",
        "corpus_sha256",
    ):
        _require_sha256(identities[name], name)  # type: ignore[arg-type]
    if not isinstance(counts, dict) or set(counts) != {
        "cases",
        "success",
        "review_required",
        "functional_mismatches",
        "determinism_mismatches",
        "unauthorized_transformations",
    }:
        raise ValueError("pilot counts differ from schema")
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in counts.values()
    ):
        raise ValueError("pilot counts must be non-negative integers")
    if not isinstance(metrics, dict) or set(metrics) != {
        "exact_identifier_retention",
        "latency_p95_ms",
        "throughput_per_second",
    }:
        raise ValueError("pilot metrics differ from schema")
    if not isinstance(budgets, dict) or set(budgets) != {
        "latency_p95_ms",
        "minimum_throughput_per_second",
    }:
        raise ValueError("pilot budgets differ from schema")
    for collection in (metrics, budgets):
        if any(
            value is not None
            and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
            )
            for value in collection.values()
        ):
            raise ValueError("pilot metrics and budgets must be finite")
    if not isinstance(gates, list) or len(gates) != 8:
        raise ValueError("pilot evidence must contain eight gates")
    expected_gate_ids = {f"G-{index:02d}" for index in range(1, 9)}
    observed_gate_ids = set()
    hard_failure = False
    for gate in gates:
        if not isinstance(gate, dict) or set(gate) != {
            "id",
            "name",
            "status",
            "owner",
            "hard_gate",
            "evidence",
        }:
            raise ValueError("pilot gate differs from schema")
        if gate["status"] not in {item.value for item in GateStatus}:
            raise ValueError("invalid pilot gate status")
        if not isinstance(gate["hard_gate"], bool) or not gate["hard_gate"]:
            raise ValueError("all controlled-pilot gates must be hard gates")
        if not isinstance(gate["evidence"], list) or not all(
            isinstance(item, str) and item for item in gate["evidence"]
        ):
            raise ValueError("pilot gate evidence must be non-empty strings")
        if not isinstance(gate["name"], str) or not gate["name"]:
            raise ValueError("pilot gate name must not be empty")
        if not isinstance(gate["owner"], str) or not gate["owner"]:
            raise ValueError("pilot gate owner must not be empty")
        observed_gate_ids.add(gate["id"])
        hard_failure |= gate["status"] != GateStatus.PASS.value
    if observed_gate_ids != expected_gate_ids:
        raise ValueError("pilot gate IDs are incomplete or duplicated")
    expected_decision = PilotDecision.STOP.value if hard_failure else PilotDecision.PASS.value
    if record["decision"] != expected_decision:
        raise ValueError("pilot decision contradicts hard-gate results")


def write_pilot_artifacts(envelope: dict[str, object], output_dir: Path) -> None:
    """Write canonical evidence and a human-readable decision summary."""

    verify_pilot_envelope(envelope)
    output_dir.mkdir(parents=True, exist_ok=True)
    evidence_path = output_dir / "controlled-pilot-evidence.json"
    report_path = output_dir / "controlled-pilot-report.md"
    evidence_path.write_bytes(canonical_json_bytes(envelope) + b"\n")
    record = envelope["record"]
    assert isinstance(record, dict)
    gates = record["gates"]
    assert isinstance(gates, list)
    rows = "\n".join(
        f"| {gate['id']} | {gate['name']} | {gate['status']} | {gate['owner']} |"
        for gate in gates
    )
    report_path.write_text(
        "# NormGuard controlled-pilot report\n\n"
        f"**Decision:** `{record['decision']}`  \n"
        f"**Protocol:** `{record['protocol']}`  \n"
        f"**Evidence SHA-256:** `{envelope['record_sha256']}`  \n"
        f"**Scope:** `{record['approved_scope']}`\n\n"
        "| Gate | Control | Result | Owner |\n"
        "|---|---|---|---|\n"
        f"{rows}\n\n"
        "This report authorizes no hosted multi-tenant deployment, publication, "
        "retrieval-utility claim or external production-readiness claim.\n",
        encoding="utf-8",
    )
