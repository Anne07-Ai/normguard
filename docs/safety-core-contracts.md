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


## Bounded runtime SDK

`BoundedSafetyRuntime` accepts only a `VerifiedPolicy`. It never loads, repairs,
discovers or implicitly trusts a raw policy bundle. Construction validates
policy-owned limits before compiling matchers; each call validates request-owned
limits before returning a result.

```python
runtime = BoundedSafetyRuntime(
    verified_policy,
    limits=RuntimeLimits(
        max_input_bytes=65_536,
        max_rules=10_000,
        max_pattern_characters=1_024,
        max_matches=10_000,
        max_output_bytes=131_072,
        max_evidence_spans=20_000,
        max_safety_events=1_000,
    ),
)
result = runtime.normalise(text)
```

Hard limit failures are atomic: callers receive a typed `RuntimeFailure` and no
partial output. Error text contains only a stable code, limit name and policy ID.
The default evidence includes digests, byte counts, rule IDs, offsets, classes,
actions and review events; it excludes raw input, raw output, key material and
signatures.

The runtime deliberately uses identity morphology. P0 is preserved exactly, P1
emits only its approved alias, P3 is preserved with review-required status, and P2
is preserved until a separately governed explicit transformation contract exists.
This does not revive the stopped general-lemmatisation hypothesis.

Decision IDs are deterministic hashes of the runtime version, verified policy
digest, input digest and limit configuration. Timing is excluded from semantic
evidence in this slice so repeated calls are byte reproducible. Runtime and policy
objects are immutable and may be shared for concurrent read-only calls.


## Stable SDK facade

Application code should import the public facade rather than internal parsers:

```python
from datetime import UTC, datetime

from normguard import RuntimeLimits, build_safety_core

core = build_safety_core(
    policy_bundle_bytes,
    trust_store_bytes,
    activation_state_bytes,
    verification_time=datetime.now(UTC),
    runtime_limits=RuntimeLimits(max_input_bytes=65_536),
)
result = core.normalise(text)
```

The three required documents and optional rollback documents must be UTF-8 JSON
`bytes`. `SDKDocumentLimits` rejects oversized documents before decoding.
Duplicate keys, non-finite constants, malformed UTF-8, non-object roots and unknown
closed-schema fields fail without fallback.

`SDKError` is the single public failure boundary. Its `code` preserves the
underlying verifier or runtime code, while its message contains only safe document,
policy, key and limit identifiers. Verification time is required explicitly so
tests and deployments do not depend on an undeclared wall clock.

The SDK is versioned as `0.1.0` with schema version `1`. CLI and service
adapters must delegate to this facade rather than duplicate parsing, trust or
runtime semantics.
