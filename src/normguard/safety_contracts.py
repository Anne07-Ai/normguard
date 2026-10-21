"""Strict Phase 3 policy-bundle and privacy-safe evidence contracts."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any

from .contracts import PolicyClass, PolicyRule

POLICY_BUNDLE_SCHEMA_VERSION = 1
EVIDENCE_SCHEMA_VERSION = 1
RUNTIME_SCHEMA_VERSION = 1
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_SEMVER = re.compile(
    r"^(0|[1-9]\\d*)\\.(0|[1-9]\\d*)\\.(0|[1-9]\\d*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\\+[0-9A-Za-z.-]+)?$"
)
_ID = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,126}[a-z0-9])?$")


class EvidenceStatus(StrEnum):
    SUCCESS = "success"
    REVIEW_REQUIRED = "review_required"
    FAILED = "failed"


def _require_digest(value: str, name: str) -> None:
    if not isinstance(value, str) or _HEX64.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")


def _require_identifier(value: str, name: str) -> None:
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise ValueError(f"{name} must be a stable lowercase identifier")


def _exact_keys(value: dict[str, Any], expected: set[str], name: str) -> None:
    actual = set(value)
    if actual != expected:
        raise ValueError(
            f"{name} keys differ from schema; missing={sorted(expected - actual)}, "
            f"unknown={sorted(actual - expected)}"
        )


def canonical_json_bytes(value: Any) -> bytes:
    """Return the single canonical JSON representation used for hashing."""
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_canonical(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


@dataclass(frozen=True, slots=True)
class SchemaCompatibility:
    minimum_runtime_schema: int
    maximum_runtime_schema: int

    def __post_init__(self) -> None:
        if self.minimum_runtime_schema < 1:
            raise ValueError("minimum_runtime_schema must be positive")
        if self.maximum_runtime_schema < self.minimum_runtime_schema:
            raise ValueError("runtime schema range is invalid")

    def supports(self, runtime_schema: int = RUNTIME_SCHEMA_VERSION) -> bool:
        return self.minimum_runtime_schema <= runtime_schema <= self.maximum_runtime_schema

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class PolicyProvenance:
    source_id: str
    source_version: str
    created_at: str

    def __post_init__(self) -> None:
        _require_identifier(self.source_id, "source_id")
        if not self.source_version.strip() or not self.created_at.strip():
            raise ValueError("source_version and created_at must not be empty")

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ReviewerApproval:
    reviewer_id: str
    role: str
    approved_at: str
    payload_sha256: str

    def __post_init__(self) -> None:
        _require_identifier(self.reviewer_id, "reviewer_id")
        if not self.role.strip() or not self.approved_at.strip():
            raise ValueError("approval role and approved_at must not be empty")
        _require_digest(self.payload_sha256, "payload_sha256")

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SignatureEnvelope:
    algorithm: str
    key_id: str
    signature: str

    def __post_init__(self) -> None:
        if not self.algorithm.strip() or not self.key_id.strip() or not self.signature.strip():
            raise ValueError("signature envelope values must not be empty")

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class PolicyPayload:
    policy_id: str
    policy_version: str
    compatibility: SchemaCompatibility
    provenance: PolicyProvenance
    rules: tuple[PolicyRule, ...]
    schema_version: int = POLICY_BUNDLE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != POLICY_BUNDLE_SCHEMA_VERSION:
            raise ValueError("unsupported policy bundle schema_version")
        _require_identifier(self.policy_id, "policy_id")
        if _SEMVER.fullmatch(self.policy_version) is None:
            raise ValueError("policy_version must be semantic versioning")
        if not self.compatibility.supports():
            raise ValueError("policy is incompatible with this runtime schema")
        if not self.rules:
            raise ValueError("policy rules must not be empty")
        rule_ids = [rule.rule_id for rule in self.rules]
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError("policy rule_id values must be unique")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "compatibility": self.compatibility.to_dict(),
            "provenance": self.provenance.to_dict(),
            "rules": [
                {
                    "rule_id": rule.rule_id,
                    "policy_class": rule.policy_class.value,
                    "match_type": rule.match_type,
                    "pattern": rule.pattern,
                    "approved_output": rule.approved_output,
                    "source": rule.source,
                }
                for rule in self.rules
            ],
        }

    @property
    def sha256(self) -> str:
        return sha256_canonical(self.to_dict())


@dataclass(frozen=True, slots=True)
class PolicyBundle:
    payload: PolicyPayload
    payload_sha256: str
    approvals: tuple[ReviewerApproval, ...]
    activation_floor: str
    signature: SignatureEnvelope | None

    def __post_init__(self) -> None:
        _require_digest(self.payload_sha256, "payload_sha256")
        if self.payload_sha256 != self.payload.sha256:
            raise ValueError("policy payload digest mismatch")
        if _SEMVER.fullmatch(self.activation_floor) is None:
            raise ValueError("activation_floor must be semantic versioning")
        if len(self.approvals) < 2:
            raise ValueError("publication requires two reviewer approvals")
        reviewer_ids = [item.reviewer_id for item in self.approvals]
        if len(reviewer_ids) != len(set(reviewer_ids)):
            raise ValueError("reviewer approvals must be from distinct reviewers")
        if any(item.payload_sha256 != self.payload_sha256 for item in self.approvals):
            raise ValueError("reviewer approval digest does not match policy payload")

    @property
    def publication_ready(self) -> bool:
        """Structural readiness only; signature trust is verified separately."""
        return self.signature is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "payload": self.payload.to_dict(),
            "payload_sha256": self.payload_sha256,
            "approvals": [item.to_dict() for item in self.approvals],
            "activation_floor": self.activation_floor,
            "signature": None if self.signature is None else self.signature.to_dict(),
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_dict())


@dataclass(frozen=True, slots=True)
class EvidenceSpan:
    rule_id: str
    start: int
    end: int
    policy_class: PolicyClass
    action: str

    def __post_init__(self) -> None:
        if not self.rule_id.strip():
            raise ValueError("rule_id must not be empty")
        if self.start < 0 or self.end <= self.start:
            raise ValueError("evidence span offsets are invalid")
        if self.action not in {"preserved", "transformed", "review", "abstained"}:
            raise ValueError("unsupported evidence span action")

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "start": self.start,
            "end": self.end,
            "policy_class": self.policy_class.value,
            "action": self.action,
        }


@dataclass(frozen=True, slots=True)
class DecisionEvidence:
    decision_id: str
    runtime_version: str
    policy_id: str
    policy_version: str
    policy_sha256: str
    input_sha256: str
    input_bytes: int
    status: EvidenceStatus
    spans: tuple[EvidenceSpan, ...]
    safety_events: tuple[str, ...]
    duration_ns: int
    evidence_schema_version: int = EVIDENCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.evidence_schema_version != EVIDENCE_SCHEMA_VERSION:
            raise ValueError("unsupported evidence_schema_version")
        _require_identifier(self.decision_id, "decision_id")
        _require_identifier(self.policy_id, "policy_id")
        if _SEMVER.fullmatch(self.runtime_version) is None:
            raise ValueError("runtime_version must be semantic versioning")
        if _SEMVER.fullmatch(self.policy_version) is None:
            raise ValueError("policy_version must be semantic versioning")
        _require_digest(self.policy_sha256, "policy_sha256")
        _require_digest(self.input_sha256, "input_sha256")
        if self.input_bytes < 0 or self.duration_ns < 0:
            raise ValueError("input_bytes and duration_ns must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_schema_version": self.evidence_schema_version,
            "decision_id": self.decision_id,
            "runtime_version": self.runtime_version,
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "policy_sha256": self.policy_sha256,
            "input_sha256": self.input_sha256,
            "input_bytes": self.input_bytes,
            "status": self.status.value,
            "spans": [span.to_dict() for span in self.spans],
            "safety_events": list(self.safety_events),
            "duration_ns": self.duration_ns,
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_dict())


def policy_payload_from_dict(value: dict[str, Any]) -> PolicyPayload:
    """Strictly parse a policy payload, rejecting every unknown field."""
    _exact_keys(
        value,
        {"schema_version", "policy_id", "policy_version", "compatibility", "provenance", "rules"},
        "policy payload",
    )
    compatibility = value["compatibility"]
    provenance = value["provenance"]
    if not isinstance(compatibility, dict) or not isinstance(provenance, dict):
        raise ValueError("compatibility and provenance must be objects")
    _exact_keys(
        compatibility,
        {"minimum_runtime_schema", "maximum_runtime_schema"},
        "compatibility",
    )
    _exact_keys(provenance, {"source_id", "source_version", "created_at"}, "provenance")
    rows = value["rules"]
    if not isinstance(rows, list):
        raise ValueError("rules must be a list")
    rules: list[PolicyRule] = []
    expected_rule = {
        "rule_id", "policy_class", "match_type", "pattern", "approved_output", "source"
    }
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"policy rule {index} must be an object")
        _exact_keys(row, expected_rule, f"policy rule {index}")
        try:
            policy_class = PolicyClass(row["policy_class"])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"policy rule {index} has invalid policy_class") from exc
        rules.append(PolicyRule(
            rule_id=row["rule_id"],
            policy_class=policy_class,
            match_type=row["match_type"],
            pattern=row["pattern"],
            approved_output=row["approved_output"],
            source=row["source"],
        ))
    return PolicyPayload(
        schema_version=value["schema_version"],
        policy_id=value["policy_id"],
        policy_version=value["policy_version"],
        compatibility=SchemaCompatibility(**compatibility),
        provenance=PolicyProvenance(**provenance),
        rules=tuple(rules),
    )


def policy_bundle_from_dict(value: dict[str, Any]) -> PolicyBundle:
    """Strictly parse a bundle; this validates structure, not signer trust."""
    _exact_keys(
        value,
        {"payload", "payload_sha256", "approvals", "activation_floor", "signature"},
        "policy bundle",
    )
    if not isinstance(value["payload"], dict) or not isinstance(value["approvals"], list):
        raise ValueError("payload must be an object and approvals must be a list")
    approvals: list[ReviewerApproval] = []
    for index, row in enumerate(value["approvals"]):
        if not isinstance(row, dict):
            raise ValueError(f"approval {index} must be an object")
        _exact_keys(
            row, {"reviewer_id", "role", "approved_at", "payload_sha256"}, f"approval {index}"
        )
        approvals.append(ReviewerApproval(**row))
    signature_value = value["signature"]
    signature = None
    if signature_value is not None:
        if not isinstance(signature_value, dict):
            raise ValueError("signature must be an object or null")
        _exact_keys(signature_value, {"algorithm", "key_id", "signature"}, "signature")
        signature = SignatureEnvelope(**signature_value)
    return PolicyBundle(
        payload=policy_payload_from_dict(value["payload"]),
        payload_sha256=value["payload_sha256"],
        approvals=tuple(approvals),
        activation_floor=value["activation_floor"],
        signature=signature,
    )
