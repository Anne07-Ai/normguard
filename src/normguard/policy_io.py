"""Strict, versioned policy input and calibration approval gates."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contracts import PolicyClass, PolicyRule
from .esci import sha256_file

POLICY_SCHEMA_VERSION = 1
APPROVAL_SCHEMA_VERSION = 1
_POLICY_KEYS = {"schema_version", "policy_id", "policy_version", "rules"}
_RULE_KEYS = {
    "rule_id", "policy_class", "match_type", "pattern", "approved_output", "source"
}
_APPROVAL_KEYS = {
    "schema_version", "partition", "status", "policy_sha256", "evidence_sha256", "reviewers"
}


@dataclass(frozen=True, slots=True)
class LoadedPolicy:
    policy_id: str
    policy_version: str
    sha256: str
    rules: tuple[PolicyRule, ...]


def _object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} must contain one JSON object")
    return payload


def _exact_keys(payload: dict[str, Any], expected: set[str], name: str) -> None:
    actual = set(payload)
    if actual != expected:
        raise ValueError(
            f"{name} keys differ from schema; missing={sorted(expected - actual)}, "
            f"unknown={sorted(actual - expected)}"
        )


def load_policy(path: Path) -> LoadedPolicy:
    """Load a strict policy file and retain its content digest for provenance."""
    payload = _object(path)
    _exact_keys(payload, _POLICY_KEYS, "policy")
    if payload["schema_version"] != POLICY_SCHEMA_VERSION:
        raise ValueError("unsupported policy schema_version")
    if not isinstance(payload["policy_id"], str) or not payload["policy_id"].strip():
        raise ValueError("policy_id must be a non-empty string")
    if not isinstance(payload["policy_version"], str) or not payload["policy_version"].strip():
        raise ValueError("policy_version must be a non-empty string")
    rows = payload["rules"]
    if not isinstance(rows, list) or not rows:
        raise ValueError("policy rules must be a non-empty list")
    rules: list[PolicyRule] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"policy rule {index} must be an object")
        _exact_keys(row, _RULE_KEYS, f"policy rule {index}")
        try:
            policy_class = PolicyClass(row["policy_class"])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"policy rule {index} has an invalid policy_class") from exc
        rules.append(
            PolicyRule(
                rule_id=row["rule_id"],
                policy_class=policy_class,
                match_type=row["match_type"],
                pattern=row["pattern"],
                approved_output=row["approved_output"],
                source=row["source"],
            )
        )
    rule_ids = [rule.rule_id for rule in rules]
    if len(rule_ids) != len(set(rule_ids)):
        raise ValueError("policy rule_id values must be unique")
    return LoadedPolicy(
        policy_id=payload["policy_id"],
        policy_version=payload["policy_version"],
        sha256=sha256_file(path),
        rules=tuple(rules),
    )


def require_development_approval(path: Path, policy_sha256: str) -> dict[str, Any]:
    """Require two-person approval of calibration evidence before development."""
    payload = _object(path)
    _exact_keys(payload, _APPROVAL_KEYS, "approval")
    if payload["schema_version"] != APPROVAL_SCHEMA_VERSION:
        raise ValueError("unsupported approval schema_version")
    if payload["partition"] != "calibration" or payload["status"] != "approved":
        raise ValueError("development requires approved calibration evidence")
    if payload["policy_sha256"] != policy_sha256:
        raise ValueError("approval policy digest does not match the loaded policy")
    evidence = payload["evidence_sha256"]
    if not isinstance(evidence, str) or len(evidence) != 64 or any(
        char not in "0123456789abcdef" for char in evidence.lower()
    ):
        raise ValueError("evidence_sha256 must be a SHA-256 digest")
    reviewers = payload["reviewers"]
    if (
        not isinstance(reviewers, list)
        or len(reviewers) < 2
        or any(not isinstance(item, str) or not item.strip() for item in reviewers)
        or len(reviewers) != len(set(reviewers))
    ):
        raise ValueError("development requires at least two distinct named reviewers")
    return payload
