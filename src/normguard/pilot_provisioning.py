"""Local-only provisioning for the private Hugging Face pilot."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from .contracts import PolicyClass, PolicyRule
from .policy_verification import ALGORITHM
from .safety_contracts import (
    PolicyBundle,
    PolicyPayload,
    PolicyProvenance,
    ReviewerApproval,
    SchemaCompatibility,
    SignatureEnvelope,
    canonical_json_bytes,
)
from .sdk import verify_policy

DEFAULT_POLICY_ID = "normguard-private-pilot"
DEFAULT_POLICY_VERSION = "0.1.0"
SECRET_NAMES = (
    "NORMGUARD_BEARER_TOKEN",
    "NORMGUARD_POLICY_B64",
    "NORMGUARD_TRUST_STORE_B64",
    "NORMGUARD_ACTIVATION_STATE_B64",
)


@dataclass(frozen=True, slots=True)
class ProvisionedPilot:
    secrets: dict[str, str]
    policy_sha256: str
    public_key_sha256: str


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def provision_private_pilot(*, now: datetime | None = None) -> ProvisionedPilot:
    """Create a short-lived signed pilot configuration without persisting the private key."""

    if now is not None and now.tzinfo is None:
        raise ValueError("provisioning time must be timezone-aware")
    created_at = datetime.now(UTC) if now is None else now.astimezone(UTC)
    private_key = ec.generate_private_key(ec.SECP256R1())
    payload = PolicyPayload(
        policy_id=DEFAULT_POLICY_ID,
        policy_version=DEFAULT_POLICY_VERSION,
        compatibility=SchemaCompatibility(1, 1),
        provenance=PolicyProvenance(
            "private-pilot-provisioner", DEFAULT_POLICY_VERSION, _timestamp(created_at)
        ),
        rules=(
            PolicyRule(
                "pilot-protected-identifier",
                PolicyClass.EXACT_PRESERVE,
                "exact",
                "NORMGUARD-PILOT",
                source="synthetic-private-pilot",
            ),
        ),
    )
    signature = private_key.sign(
        canonical_json_bytes(payload.to_dict()), ec.ECDSA(hashes.SHA256())
    )
    approvals = tuple(
        ReviewerApproval(
            reviewer_id,
            "private-pilot-reviewer",
            _timestamp(created_at),
            payload.sha256,
        )
        for reviewer_id in ("pilot-reviewer-one", "pilot-reviewer-two")
    )
    bundle = PolicyBundle(
        payload,
        payload.sha256,
        approvals,
        DEFAULT_POLICY_VERSION,
        SignatureEnvelope(
            ALGORITHM,
            "private-pilot-key",
            base64.b64encode(signature).decode("ascii"),
        ),
    )
    public_der = private_key.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    public_fingerprint = hashlib.sha256(public_der).hexdigest()
    trust_store = {
        "schema_version": 1,
        "keys": [
            {
                "key_id": "private-pilot-key",
                "algorithm": ALGORITHM,
                "public_key_spki_der_base64": base64.b64encode(public_der).decode("ascii"),
                "public_key_sha256": public_fingerprint,
                "allowed_policy_ids": [DEFAULT_POLICY_ID],
                "status": "active",
                "not_before": _timestamp(created_at - timedelta(minutes=5)),
                "not_after": _timestamp(created_at + timedelta(days=30)),
            }
        ],
    }
    activation_state = {
        "schema_version": 1,
        "policy_id": DEFAULT_POLICY_ID,
        "minimum_version": DEFAULT_POLICY_VERSION,
        "updated_at": _timestamp(created_at),
        "change_id": "private-pilot-provisioning",
        "allow_prerelease": False,
    }
    policy_bytes = bundle.canonical_bytes()
    trust_bytes = canonical_json_bytes(trust_store)
    activation_bytes = canonical_json_bytes(activation_state)
    verify_policy(
        policy_bytes,
        trust_bytes,
        activation_bytes,
        verification_time=created_at,
    )
    values = {
        "NORMGUARD_BEARER_TOKEN": secrets.token_urlsafe(48),
        "NORMGUARD_POLICY_B64": base64.b64encode(policy_bytes).decode("ascii"),
        "NORMGUARD_TRUST_STORE_B64": base64.b64encode(trust_bytes).decode("ascii"),
        "NORMGUARD_ACTIVATION_STATE_B64": base64.b64encode(activation_bytes).decode("ascii"),
    }
    return ProvisionedPilot(values, payload.sha256, public_fingerprint)


def write_secret_file(output_dir: Path, provisioned: ProvisionedPilot) -> Path:
    """Write one owner-only env file and never overwrite existing secret material."""

    if output_dir.is_symlink():
        raise ValueError("secret output directory must not be a symlink")
    output_dir = output_dir.resolve()
    output_dir.mkdir(mode=0o700, parents=False, exist_ok=False)
    destination = output_dir / "huggingface-secrets.env"
    content = "".join(f"{name}={provisioned.secrets[name]}\n" for name in SECRET_NAMES)
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args(argv)
    provisioned = provision_private_pilot()
    destination = write_secret_file(args.output_dir, provisioned)
    print(
        json.dumps(
            {
                "created": str(destination),
                "secret_names": list(SECRET_NAMES),
                "policy_sha256": provisioned.policy_sha256,
                "public_key_sha256": provisioned.public_key_sha256,
                "private_key_persisted": False,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
