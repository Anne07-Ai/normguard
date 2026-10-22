"""Command-line adapter for the public NormGuard Safety Core SDK."""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from datetime import UTC, datetime
from enum import IntEnum
from pathlib import Path
from typing import Any, Sequence

from .policy_verification import VerificationCode
from .runtime import RuntimeCode, RuntimeLimits
from .safety_contracts import EvidenceStatus
from .sdk import (
    SDK_SCHEMA_VERSION,
    SDKDocumentLimits,
    SDKError,
    build_safety_core,
    verify_policy,
)


class ExitCode(IntEnum):
    SUCCESS = 0
    REVIEW_REQUIRED = 10
    INVALID_INPUT = 20
    UNTRUSTED_POLICY = 30
    LIMIT_EXCEEDED = 40
    INTERNAL_FAILURE = 50


class CLIUsageError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CLIUsageError(message)


def _add_policy_inputs(parser: argparse.ArgumentParser, *, positional: bool) -> None:
    if positional:
        parser.add_argument("policy", help="signed policy bundle JSON file")
    else:
        parser.add_argument("--policy", required=True, help="signed policy bundle JSON file")
    parser.add_argument("--trust-store", required=True, help="offline trust-store JSON file")
    parser.add_argument(
        "--activation-state",
        required=True,
        help="operator-controlled activation-state JSON file",
    )
    parser.add_argument(
        "--verification-time",
        required=True,
        help="explicit UTC timestamp ending in Z",
    )
    parser.add_argument("--rollback-authorization", help="rollback authorization JSON file")
    parser.add_argument("--rollback-trust-store", help="separate rollback trust-store JSON file")
    parser.add_argument("--json", action="store_true", help="emit schema-versioned JSON")


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="normguard",
        description="Offline verification and bounded deterministic text safety.",
        epilog=(
            "Exit codes: 0 success, 10 review required, 20 invalid input, "
            "30 untrusted policy, 40 limit exceeded, 50 internal failure."
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)

    policy = commands.add_parser("policy", help="policy operations")
    policy_commands = policy.add_subparsers(dest="policy_command", required=True)
    verify = policy_commands.add_parser("verify", help="verify a signed policy offline")
    _add_policy_inputs(verify, positional=True)

    normalise = commands.add_parser(
        "normalise",
        help="verify a policy and normalise file or stdin text",
    )
    _add_policy_inputs(normalise, positional=False)
    input_group = normalise.add_mutually_exclusive_group(required=True)
    input_group.add_argument(
        "--input-file",
        help="regular, non-symlink UTF-8 input file",
    )
    input_group.add_argument(
        "--stdin",
        action="store_true",
        help="read bounded UTF-8 input from stdin",
    )
    normalise.add_argument(
        "--max-input-bytes",
        type=int,
        default=RuntimeLimits().max_input_bytes,
        help="maximum UTF-8 input size",
    )
    return parser


def _verification_time(value: str) -> datetime:
    if not value.endswith("Z"):
        raise SDKError("INVALID_VERIFICATION_TIME")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        raise SDKError("INVALID_VERIFICATION_TIME") from None
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise SDKError("INVALID_VERIFICATION_TIME")
    return parsed


def _read_regular_file(path_value: str, maximum: int, document: str) -> bytes:
    path = Path(path_value)
    try:
        if path.is_symlink():
            raise SDKError("INVALID_FILE", document=document)
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > maximum:
                code = "DOCUMENT_TOO_LARGE" if metadata.st_size > maximum else "INVALID_FILE"
                raise SDKError(code, document=document)
            with os.fdopen(descriptor, "rb", closefd=False) as handle:
                data = handle.read(maximum + 1)
        finally:
            os.close(descriptor)
    except SDKError:
        raise
    except (OSError, ValueError):
        raise SDKError("INVALID_FILE", document=document) from None
    if len(data) > maximum:
        raise SDKError("DOCUMENT_TOO_LARGE", document=document)
    return data


def _read_stdin(maximum: int) -> bytes:
    data = sys.stdin.buffer.read(maximum + 1)
    if len(data) > maximum:
        raise SDKError("LIMIT_EXCEEDED", limit="input_bytes")
    return data


def _policy_documents(
    args: argparse.Namespace,
    limits: SDKDocumentLimits,
) -> dict[str, bytes | None]:
    rollback_authorization = args.rollback_authorization
    rollback_trust_store = args.rollback_trust_store
    if (rollback_authorization is None) != (rollback_trust_store is None):
        raise SDKError("INVALID_ARGUMENT")
    return {
        "policy_bundle_bytes": _read_regular_file(
            args.policy,
            limits.max_policy_bundle_bytes,
            "policy_bundle",
        ),
        "trust_store_bytes": _read_regular_file(
            args.trust_store,
            limits.max_trust_store_bytes,
            "trust_store",
        ),
        "activation_state_bytes": _read_regular_file(
            args.activation_state,
            limits.max_activation_state_bytes,
            "activation_state",
        ),
        "rollback_authorization_bytes": (
            None
            if rollback_authorization is None
            else _read_regular_file(
                rollback_authorization,
                limits.max_rollback_authorization_bytes,
                "rollback_authorization",
            )
        ),
        "rollback_trust_store_bytes": (
            None
            if rollback_trust_store is None
            else _read_regular_file(
                rollback_trust_store,
                limits.max_rollback_trust_store_bytes,
                "rollback_trust_store",
            )
        ),
    }


def _emit(value: dict[str, Any], *, stream: Any = sys.stdout) -> None:
    stream.write(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )


def _policy_success(verified: Any) -> dict[str, Any]:
    return {
        "schema_version": SDK_SCHEMA_VERSION,
        "status": "verified",
        "policy": {
            "policy_id": verified.payload.policy_id,
            "policy_version": verified.payload.policy_version,
            "payload_sha256": verified.payload_sha256,
            "signing_key_id": verified.signing_key_id,
            "signing_key_sha256": verified.signing_key_sha256,
            "rollback_authorized": verified.rollback_authorized,
        },
    }


def _error_payload(error: SDKError) -> dict[str, Any]:
    details = {
        key: value
        for key, value in {
            "document": error.document,
            "policy_id": error.policy_id,
            "key_id": error.key_id,
            "limit": error.limit,
        }.items()
        if value is not None
    }
    return {
        "schema_version": SDK_SCHEMA_VERSION,
        "status": "failed",
        "error": {"code": error.code, "details": details},
    }


def _exit_for(error: SDKError) -> ExitCode:
    if error.code == RuntimeCode.LIMIT_EXCEEDED.value:
        return ExitCode.LIMIT_EXCEEDED
    if error.code in {item.value for item in VerificationCode}:
        return ExitCode.UNTRUSTED_POLICY
    if error.code == RuntimeCode.INTERNAL_FAILURE.value:
        return ExitCode.INTERNAL_FAILURE
    return ExitCode.INVALID_INPUT


def _run(args: argparse.Namespace) -> ExitCode:
    document_limits = SDKDocumentLimits()
    documents = _policy_documents(args, document_limits)
    verification_time = _verification_time(args.verification_time)
    if args.command == "policy":
        verified = verify_policy(
            **documents,
            verification_time=verification_time,
            document_limits=document_limits,
        )
        _emit(_policy_success(verified))
        return ExitCode.SUCCESS

    if args.max_input_bytes < 1:
        raise SDKError("INVALID_ARGUMENT", limit="input_bytes")
    runtime_limits = RuntimeLimits(max_input_bytes=args.max_input_bytes)
    core = build_safety_core(
        **documents,
        verification_time=verification_time,
        document_limits=document_limits,
        runtime_limits=runtime_limits,
    )
    raw_input = (
        _read_stdin(runtime_limits.max_input_bytes)
        if args.stdin
        else _read_regular_file(
            args.input_file,
            runtime_limits.max_input_bytes,
            "input",
        )
    )
    try:
        text = raw_input.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise SDKError("INVALID_INPUT_ENCODING", document="input") from None
    result = core.normalise(text)
    _emit(
        {
            "schema_version": SDK_SCHEMA_VERSION,
            "status": result.status.value,
            "output_text": result.output_text,
            "evidence": result.evidence.to_dict(),
        }
    )
    return (
        ExitCode.REVIEW_REQUIRED
        if result.status is EvidenceStatus.REVIEW_REQUIRED
        else ExitCode.SUCCESS
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        if not args.json:
            raise SDKError("JSON_OUTPUT_REQUIRED")
        return int(_run(args))
    except CLIUsageError:
        error = SDKError("INVALID_ARGUMENT")
    except SDKError as caught:
        error = caught
    except Exception:
        error = SDKError(RuntimeCode.INTERNAL_FAILURE.value)
    _emit(_error_payload(error), stream=sys.stderr)
    return int(_exit_for(error))


if __name__ == "__main__":
    raise SystemExit(main())
