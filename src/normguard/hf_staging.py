"""Exact-commit Hugging Face staging deployment and bounded verification."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

HF_STAGING_SCHEMA_VERSION = 1
DEFAULT_ENDPOINT = "https://lakshmianne-normguard-private-pilot.hf.space"
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_FORBIDDEN_PARTS = frozenset({".git", "artifacts", "pilot-secrets", "__pycache__"})


def _canonical_json(value: Mapping[str, object]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode()


def _git(root: Path, *args: str) -> bytes:
    return subprocess.run(
        ("git", "-C", str(root), *args),
        check=True,
        capture_output=True,
    ).stdout


def build_space_bundle(
    source_root: Path,
    output_dir: Path,
    *,
    source_commit: str,
) -> dict[str, object]:
    """Materialize a secret-free Space bundle from one exact Git commit."""

    root = source_root.resolve()
    output = output_dir.resolve()
    resolved_commit = _git(root, "rev-parse", f"{source_commit}^{{commit}}").decode().strip()
    if _COMMIT.fullmatch(source_commit) is None or resolved_commit != source_commit:
        raise ValueError("source_commit must be an exact lowercase 40-character commit")
    if output.exists() and any(output.iterdir()):
        raise ValueError("output_dir must be absent or empty")
    output.mkdir(parents=True, exist_ok=True)

    archive = _git(root, "archive", "--format=tar", source_commit)
    archive_sha256 = hashlib.sha256(archive).hexdigest()
    with tempfile.NamedTemporaryFile(suffix=".tar") as handle:
        handle.write(archive)
        handle.flush()
        with tarfile.open(handle.name) as bundle:
            bundle.extractall(output, filter="data")

    for path in output.rglob("*"):
        relative = path.relative_to(output)
        if _FORBIDDEN_PARTS.intersection(relative.parts) or path.name.endswith(".env"):
            shutil.rmtree(output)
            raise ValueError(f"forbidden deployment path: {relative}")

    deploy = output / "deploy" / "huggingface"
    shutil.copyfile(deploy / "Dockerfile", output / "Dockerfile")
    shutil.copyfile(deploy / "README.md", output / "README.md")
    manifest: dict[str, object] = {
        "schema_version": HF_STAGING_SCHEMA_VERSION,
        "provider": "hugging-face-spaces",
        "repository": "lakshmianne/normguard-private-pilot",
        "profile": "protected-single-tenant-cpu-basic",
        "source_commit": source_commit,
        "source_archive_sha256": archive_sha256,
    }
    (output / "deployment-manifest.json").write_bytes(_canonical_json(manifest))
    return manifest


def _required_secret(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name, "")
    if not isinstance(value, str) or not 32 <= len(value) <= 4096:
        raise ValueError(f"{name} is missing or invalid")
    return value


def _endpoint(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.hostname is None
        or not parsed.hostname.endswith(".hf.space")
    ):
        raise ValueError("endpoint must be an HTTPS Hugging Face Space origin")
    return value.rstrip("/")


def _request_json(
    url: str,
    *,
    headers: Mapping[str, str],
    payload: Mapping[str, object] | None = None,
    timeout: float = 20,
) -> tuple[int, dict[str, Any]]:
    data = _canonical_json(payload) if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method="POST" if payload is not None else "GET",
        headers=dict(headers),
    )
    try:
        response = urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        body = response.read(16_384)
        if response.read(1):
            raise RuntimeError("staging response exceeds the bounded verifier limit")
        try:
            value = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise RuntimeError("staging returned a non-JSON response") from exc
        if not isinstance(value, dict):
            raise RuntimeError("staging returned an invalid response shape")
        return response.status, value


def verify_staging(
    endpoint: str,
    *,
    hf_token: str,
    application_token: str,
    attempts: int = 60,
    delay_seconds: float = 10,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, object]:
    """Verify readiness and dual authentication without returning response content."""

    origin = _endpoint(endpoint)
    if not 1 <= attempts <= 120 or not 0 <= delay_seconds <= 60:
        raise ValueError("verification retry bounds are invalid")
    if len(hf_token) < 32 or len(application_token) < 32:
        raise ValueError("staging credentials are missing or invalid")
    platform_headers = {"Authorization": f"Bearer {hf_token}"}

    last_status = 0
    for attempt in range(attempts):
        status, health = _request_json(origin + "/health", headers=platform_headers)
        last_status = status
        if (
            status == 200
            and isinstance(health.get("sdk_version"), str)
            and health
            == {
                "schema_version": 1,
                "status": "ready",
                "ready": True,
                "sdk_version": health["sdk_version"],
            }
        ):
            break
        if attempt + 1 < attempts:
            sleep(delay_seconds)
    else:
        raise RuntimeError(f"staging did not become ready; bounded status={last_status}")

    unauthenticated_status, unauthenticated = _request_json(
        origin + "/v1/normalise",
        headers={**platform_headers, "Content-Type": "application/json"},
        payload={"text": "SKU-42 ACME LTD"},
    )
    if unauthenticated_status != 401 or unauthenticated != {
        "schema_version": 1,
        "error_code": "UNAUTHENTICATED",
    }:
        raise RuntimeError("application authentication boundary validation failed")

    accepted_status, accepted = _request_json(
        origin + "/v1/normalise",
        headers={
            **platform_headers,
            "X-NormGuard-Token": application_token,
            "Content-Type": "application/json",
        },
        payload={"text": "SKU-42 ACME LTD"},
    )
    if (
        accepted_status not in {200, 202}
        or set(accepted)
        != {"schema_version", "status", "output_text", "error_code"}
        or accepted.get("schema_version") != 1
        or accepted.get("status") not in {"accepted", "review_required"}
        or not isinstance(accepted.get("output_text"), str)
        or accepted.get("error_code") is not None
    ):
        raise RuntimeError("authenticated synthetic staging validation failed")
    return {
        "schema_version": HF_STAGING_SCHEMA_VERSION,
        "ready": True,
        "dual_authentication": True,
        "synthetic_request": "passed",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    bundle = commands.add_parser("bundle")
    bundle.add_argument("--source-root", type=Path, default=Path.cwd())
    bundle.add_argument("--output-dir", type=Path, required=True)
    bundle.add_argument("--source-commit", required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "bundle":
        result = build_space_bundle(
            args.source_root,
            args.output_dir,
            source_commit=args.source_commit,
        )
    else:
        result = verify_staging(
            args.endpoint,
            hf_token=_required_secret(os.environ, "HF_TOKEN"),
            application_token=_required_secret(
                os.environ, "NORMGUARD_BEARER_TOKEN"
            ),
        )
    print(_canonical_json(result).decode(), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
