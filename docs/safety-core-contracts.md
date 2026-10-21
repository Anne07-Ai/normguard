# Safety Core policy and evidence contracts

Issue #64 introduces Phase 3 contracts without changing the Phase 2 research-policy
format in `policy_io.py`.

## Canonical policy payload

The canonical policy payload contains its schema version, immutable policy ID,
semantic version, runtime compatibility range, provenance and ordered rules.
Canonical JSON uses UTF-8, sorted object keys, compact separators and rejects
non-finite numbers. Its SHA-256 digest is the value reviewers approve.

The digest proves content integrity only. It does not authorize the policy.

## Bundle envelope

A bundle contains:

- the canonical payload;
- its SHA-256 digest;
- at least two distinct reviewer approvals bound to that digest;
- an activation floor;
- an optional signature envelope.

`publication_ready` means the signature envelope is structurally present. It does
not mean that the signer is trusted. Cryptographic verification and rollback-state
enforcement are separate Phase 3 controls.

All schema objects reject unknown keys when parsed from dictionaries. Invalid
digests, duplicate rule IDs, incompatible runtime schemas, invalid semantic
versions, repeated reviewers and approval-digest mismatches fail closed.

## Evidence

Decision evidence contains policy and input digests, input byte length, rule IDs,
offsets, P0–P3 classes, actions, safety-event identifiers, duration and terminal
status.

Raw input, raw output and matched surface text are deliberately absent. Consumers
must not reconstruct or attach those fields to default evidence. Diagnostic content
capture requires a separately governed design.

## Compatibility

The Phase 3 bundle currently uses schema version 1 and supports runtime schema
version 1. Unsupported ranges fail during payload construction, before the policy
can reach the runtime.

## Verification

```bash
uv run --locked ruff check src tests
uv run --locked python -m unittest discover -s tests -v
```

The tests cover deterministic serialization, strict nested schemas, digest
tampering, duplicate rules, two-reviewer binding, compatibility failure and
privacy-safe evidence.
