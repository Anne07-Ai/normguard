"""Tamper-evident implementer evidence for synthetic tenant-isolation tests."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path

from .safety_contracts import canonical_json_bytes

ISOLATION_EVIDENCE_SCHEMA_VERSION = 1
ISOLATION_EVIDENCE_PROTOCOL = "NG-TENANT-ISOLATION-001"
ASSURANCE_LEVEL = "IMPLEMENTER_TESTED"
_HEX = frozenset("0123456789abcdef")
_EVIDENCE_KEYS = {
    "assurance_level",
    "decision",
    "identities",
    "limitations",
    "protocol",
    "scenarios",
    "schema_version",
}
_IDENTITY_KEYS = {
    "artifact_sha256",
    "production_profile_sha256",
    "source_commit",
}
_SCENARIO_KEYS = {"assertion_count", "passed", "scenario_id"}
_LIMITATIONS = (
    "implementer-authored evidence is not independent security assurance",
    "synthetic tenant tests do not authorize customer data or production traffic",
    "evidence applies only to the exact source artifact and production profile identities",
    "B-001 B-002 B-003 and NG-T22 remain unresolved by this evidence alone",
)


class IsolationScenario(StrEnum):
    CROSS_TENANT_AUTHORIZATION = "cross_tenant_authorization"
    PRINCIPAL_GRANT_BOUNDARY = "principal_grant_boundary"
    POLICY_SUBSTITUTION = "policy_substitution"
    QUOTA_ISOLATION = "quota_isolation"
    DEPENDENCY_FAIL_CLOSED = "dependency_fail_closed"
    ERROR_AND_AUDIT_PRIVACY = "error_and_audit_privacy"
    CONCURRENT_RESULT_ISOLATION = "concurrent_result_isolation"
    ACTIVATION_ROLLBACK_ISOLATION = "activation_rollback_isolation"


class IsolationDecision(StrEnum):
    PASS = "PASS"
    STOP = "STOP"


def _require_hex(value: object, length: int, name: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != length
        or any(character not in _HEX for character in value)
    ):
        raise ValueError(f"{name} must be a lowercase {length}-character digest")


@dataclass(frozen=True, slots=True)
class IsolationEvidenceIdentity:
    source_commit: str
    artifact_sha256: str
    production_profile_sha256: str

    def __post_init__(self) -> None:
        _require_hex(self.source_commit, 40, "source_commit")
        _require_hex(self.artifact_sha256, 64, "artifact_sha256")
        _require_hex(
            self.production_profile_sha256, 64, "production_profile_sha256"
        )

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class IsolationScenarioResult:
    scenario_id: IsolationScenario
    passed: bool
    assertion_count: int

    def __post_init__(self) -> None:
        if not isinstance(self.scenario_id, IsolationScenario):
            raise ValueError("scenario_id must be supported")
        if not isinstance(self.passed, bool):
            raise ValueError("passed must be boolean")
        if (
            isinstance(self.assertion_count, bool)
            or not isinstance(self.assertion_count, int)
            or not 1 <= self.assertion_count <= 1_000_000
        ):
            raise ValueError("assertion_count must be a bounded positive integer")

    def to_dict(self) -> dict[str, object]:
        return {
            "scenario_id": self.scenario_id.value,
            "passed": self.passed,
            "assertion_count": self.assertion_count,
        }


def build_isolation_evidence(
    *,
    identity: IsolationEvidenceIdentity,
    results: tuple[IsolationScenarioResult, ...],
) -> dict[str, object]:
    expected = set(IsolationScenario)
    observed = {item.scenario_id for item in results}
    if len(results) != len(observed) or observed != expected:
        raise ValueError("evidence requires every isolation scenario exactly once")
    ordered = tuple(sorted(results, key=lambda item: item.scenario_id.value))
    decision = (
        IsolationDecision.PASS
        if all(item.passed for item in ordered)
        else IsolationDecision.STOP
    )
    evidence: dict[str, object] = {
        "schema_version": ISOLATION_EVIDENCE_SCHEMA_VERSION,
        "protocol": ISOLATION_EVIDENCE_PROTOCOL,
        "assurance_level": ASSURANCE_LEVEL,
        "identities": identity.to_dict(),
        "scenarios": [item.to_dict() for item in ordered],
        "decision": decision.value,
        "limitations": list(_LIMITATIONS),
    }
    return {
        "evidence": evidence,
        "evidence_sha256": hashlib.sha256(canonical_json_bytes(evidence)).hexdigest(),
    }


def verify_isolation_evidence(
    envelope: object,
    *,
    expected_identity: IsolationEvidenceIdentity | None = None,
) -> None:
    if not isinstance(envelope, dict) or set(envelope) != {
        "evidence",
        "evidence_sha256",
    }:
        raise ValueError("isolation evidence envelope has unknown or missing fields")
    evidence = envelope["evidence"]
    if not isinstance(evidence, dict) or set(evidence) != _EVIDENCE_KEYS:
        raise ValueError("isolation evidence has unknown or missing fields")
    _require_hex(envelope["evidence_sha256"], 64, "evidence_sha256")
    expected_digest = hashlib.sha256(canonical_json_bytes(evidence)).hexdigest()
    if envelope["evidence_sha256"] != expected_digest:
        raise ValueError("isolation evidence digest mismatch")
    if evidence["schema_version"] != ISOLATION_EVIDENCE_SCHEMA_VERSION:
        raise ValueError("unsupported isolation evidence schema_version")
    if evidence["protocol"] != ISOLATION_EVIDENCE_PROTOCOL:
        raise ValueError("unsupported isolation evidence protocol")
    if evidence["assurance_level"] != ASSURANCE_LEVEL:
        raise ValueError("isolation evidence assurance level is invalid")
    if evidence["limitations"] != list(_LIMITATIONS):
        raise ValueError("isolation evidence limitations changed")

    identities = evidence["identities"]
    if not isinstance(identities, dict) or set(identities) != _IDENTITY_KEYS:
        raise ValueError("isolation evidence identities are invalid")
    identity = IsolationEvidenceIdentity(**identities)
    if expected_identity is not None and identity != expected_identity:
        raise ValueError("isolation evidence identity does not match expected target")

    scenarios = evidence["scenarios"]
    if not isinstance(scenarios, list):
        raise ValueError("isolation evidence scenarios must be a list")
    parsed: list[IsolationScenarioResult] = []
    for scenario in scenarios:
        if not isinstance(scenario, dict) or set(scenario) != _SCENARIO_KEYS:
            raise ValueError("isolation scenario has unknown or missing fields")
        parsed.append(
            IsolationScenarioResult(
                IsolationScenario(scenario["scenario_id"]),
                scenario["passed"],
                scenario["assertion_count"],
            )
        )
    rebuilt = build_isolation_evidence(identity=identity, results=tuple(parsed))
    if rebuilt != envelope:
        raise ValueError("isolation evidence decision or canonical form drifted")


def write_isolation_evidence(envelope: dict[str, object], path: Path) -> None:
    verify_isolation_evidence(envelope)
    path.write_bytes(canonical_json_bytes(envelope) + b"\n")


def load_isolation_evidence(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    verify_isolation_evidence(value)
    return value
