# ADR 0004: Verify policy bundles with ECDSA P-256 and offline trust stores

- **Status:** Proposed
- **Date:** 2026-10-21
- **Decision owners:** NormGuard maintainers
- **Issue:** #66
- **Related:** ADR 0003 and Issue #64

## Context

Phase 3 policy bundles have canonical payload bytes, SHA-256 integrity, reviewer
approvals, activation metadata and a structural signature envelope. A digest
detects change but does not establish who authorized a policy. NormGuard therefore
needs offline authenticity verification, signer scoping, key lifecycle controls
and downgrade protection before a bundle can reach the normalisation runtime.

The design must work with common enterprise key-management systems without making
the runtime depend on a cloud service.

## Decision

NormGuard will verify policy payloads using **ECDSA with the NIST P-256 curve and
SHA-256**, identified in NormGuard envelopes as `ECDSA_P256_SHA256`.

The signature is calculated over the exact canonical policy payload bytes returned
by `PolicyPayload.to_dict()` and `canonical_json_bytes()`. The bundle's
`payload_sha256`, approvals, activation floor and signature envelope are not part
of the signed payload. Instead:

- the signature authenticates the immutable payload;
- reviewer approvals independently bind to its SHA-256;
- the locally controlled activation state enforces the minimum accepted version;
- the trust store binds the signing key to permitted policy identities.

This separation avoids circular serialization while preserving explicit control
boundaries.

ECDSA P-256/SHA-256 is supported by AWS KMS, Google Cloud KMS and Azure Key Vault,
and public keys can be used for offline verification. ([AWS KMS](https://docs.aws.amazon.com/kms/latest/developerguide/supported-algorithms.html), [Google Cloud KMS](https://cloud.google.com/kms/docs/algorithms), [Azure Key Vault](https://learn.microsoft.com/en-us/azure/key-vault/keys/about-keys-details)).

## Encoding

- Public keys: DER SubjectPublicKeyInfo, base64-encoded in the trust store.
- Key fingerprint: lowercase SHA-256 of the DER SubjectPublicKeyInfo bytes.
- Signature: ASN.1 DER ECDSA signature, base64-encoded.
- Payload: canonical UTF-8 JSON bytes defined by Issue #64.
- Hash: SHA-256 exactly once as part of ECDSA verification.
- All base64 uses the standard alphabet with required padding.
- Parsers reject non-canonical encodings, trailing data and unsupported algorithms.

The implementation will use the maintained `cryptography` package. It will not
implement elliptic-curve arithmetic or signature parsing itself.

## Trust-store contract

The offline trust store is a closed, versioned JSON document:

```json
{
  "schema_version": 1,
  "keys": [
    {
      "key_id": "policy-prod-2026-q4",
      "algorithm": "ECDSA_P256_SHA256",
      "public_key_spki_der_base64": "...",
      "public_key_sha256": "...",
      "allowed_policy_ids": ["retail-en"],
      "status": "active",
      "not_before": "2026-10-21T00:00:00Z",
      "not_after": "2027-01-31T23:59:59Z"
    }
  ]
}
```

Rules:

- unknown fields fail closed;
- key IDs and fingerprints are unique;
- the decoded key must be P-256 and match its fingerprint;
- a key is usable only when active and within its validity interval;
- `allowed_policy_ids` is non-empty and exact; wildcards are forbidden;
- unknown, disabled, expired, not-yet-valid and revoked keys fail closed;
- the verifier does not download keys or revocation data;
- trust-store changes follow an independently audited deployment path.

## Verification order

Verification is atomic and ordered:

1. Parse the bundle and trust store with closed schemas and size limits.
2. Confirm policy/runtime schema compatibility.
3. Recompute and compare the canonical payload digest.
4. Require a signature envelope with the selected algorithm.
5. Resolve exactly one trust-store key by `key_id`.
6. Validate fingerprint, curve, status, validity and allowed policy ID.
7. Verify the ECDSA signature over canonical payload bytes.
8. Validate reviewer approvals against the same payload digest.
9. Compare policy semantic version with activation state.
10. Return an immutable `VerifiedPolicy` or a typed terminal failure.

No output policy is returned on failure. There is no unsigned fallback.

## Activation and rollback protection

Activation state is operator-controlled and stored separately from the signed
bundle:

```json
{
  "schema_version": 1,
  "policy_id": "retail-en",
  "minimum_version": "1.4.0",
  "updated_at": "2026-10-21T13:00:00Z",
  "change_id": "CHG-12345"
}
```

Normal verification rejects any policy version below `minimum_version`.
Pre-release versions are rejected for production activation unless the activation
state explicitly permits them.

An emergency rollback requires a separate, closed rollback authorization:

- matching policy ID;
- exact target version and target payload SHA-256;
- incident/change identifier;
- reason code;
- issue and expiry timestamps;
- two distinct approvers;
- authorization signature verified under a rollback-specific trust purpose.

Rollback authorization is single-target, time-bounded and evidence-producing. It
does not lower or mutate the activation floor. Reuse after expiry or for another
digest fails closed.

## Key rotation and revocation

Rotation uses a bounded overlap:

1. add the next key as active with a future `not_before`;
2. publish policies with the next key after it becomes valid;
3. keep the previous key active only for a declared overlap;
4. mark the previous key disabled after migration;
5. mark a compromised key revoked immediately.

Revocation is local and explicit. A valid signature from a revoked key is rejected.
Historical evidence may record that a key was valid at verification time, but it
must not cause the runtime to accept newly presented artifacts from that key.

Trust stores must not implicitly trust every historical key.

## Typed outcomes

The verifier will expose stable error codes without embedding sensitive content:

- `INVALID_BUNDLE`
- `UNSUPPORTED_SCHEMA`
- `DIGEST_MISMATCH`
- `SIGNATURE_REQUIRED`
- `UNSUPPORTED_ALGORITHM`
- `UNKNOWN_KEY`
- `KEY_NOT_ACTIVE`
- `KEY_REVOKED`
- `KEY_OUTSIDE_VALIDITY`
- `POLICY_NOT_ALLOWED`
- `INVALID_SIGNATURE`
- `APPROVAL_INVALID`
- `ROLLBACK_REJECTED`
- `ROLLBACK_AUTHORIZATION_INVALID`

Exceptions and logs include identifiers and codes only, never policy text, public
key material, signatures or private input.

## Private-key boundary

Private signing keys are never accepted by the verification API and must not appear
in the repository, policy bundle, trust store, container image, CI log or evidence.

Production signing should use a managed KMS/HSM. The private key remains inside the
provider; the exported public key is converted once to the approved SPKI form and
deployed through the trust-store workflow. AWS documents that asymmetric KMS
public keys can be downloaded for external verification. ([AWS KMS key specification guidance](https://docs.aws.amazon.com/kms/latest/developerguide/symm-asymm-choose-key-spec.html)).

Tests use an explicitly labelled ephemeral or fixture-only key generated for the
test process. No fixture private key is packaged in a release artifact.

## Dependency decision

Add a narrow direct dependency on `cryptography` only in the implementation PR,
with the version resolved and committed through `uv.lock`. The dependency PR must
record licence and supply-chain review. Verification must use high-level ECDSA
interfaces and reject malformed DER.

## Alternatives rejected

### Ed25519

Technically attractive and recommended by the `cryptography` documentation when
legacy interoperability is not a concern. ([cryptography Ed25519 documentation](https://cryptography.io/en/latest/hazmat/primitives/asymmetric/ed25519/)).
It was not selected as the required v1 algorithm because P-256/SHA-256 has the
clearest common support across the project's AWS, Azure and GCP enterprise targets.

### RSA-PSS

Supported broadly and preferable to legacy RSA PKCS#1 v1.5 when RSA is required.
It has larger keys and signatures without providing a benefit for this compact
policy format.

### HMAC

Rejected because every verifier would possess signing capability, weakening
separation of duties and increasing blast radius.

### Checksums only

Rejected because SHA-256 integrity does not authenticate the publisher.

### Online KMS verification

Rejected for the core because it adds availability, latency, credential and cloud
coupling. Signing may use KMS; verification remains offline using the public key.

### Embedded public key in the policy bundle

Rejected because an attacker could replace both payload and key. Trust anchors must
arrive through a separate operator-controlled path.

## Implementation plan

1. Add strict trust-store, activation-state and rollback-authorization contracts.
2. Add `cryptography` and update the locked dependency set.
3. Implement ECDSA verification and key parsing.
4. Return immutable `VerifiedPolicy` only after all gates pass.
5. Add generated known-answer vectors and negative vectors.
6. Test tampering, unknown keys, wrong policy scope, validity, revocation and
   downgrade/replay behavior.
7. Add emergency rollback tests, audit evidence and expiry handling.
8. Add dependency, licence and documentation updates.

## Required test vectors

- valid signed bundle;
- single-byte payload mutation;
- digest changed without resigning;
- signature changed;
- wrong algorithm and curve;
- unknown, disabled and revoked keys;
- before `not_before` and after `not_after`;
- signer not allowed for the policy ID;
- duplicate key IDs or fingerprints;
- version below activation floor;
- valid and invalid emergency rollback authorizations;
- approval digest mismatch;
- malformed DER and base64;
- repeated verification produces identical semantic result.

## Consequences

### Positive

- Verifiers need only public material.
- Verification works without network or cloud credentials.
- Policy authorization is separated from content integrity.
- The algorithm maps cleanly to major enterprise KMS platforms.
- Rollback exceptions remain narrow and auditable.

### Negative

- Trust-store distribution and activation state become operational assets.
- ECDSA signatures are not necessarily byte-identical across signing operations;
  signed bundles are immutable after publication rather than regenerated.
- Key rotation, revocation and emergency rollback require maintained processes.
- A new cryptographic dependency needs supply-chain ownership.

## Review triggers

Revisit this ADR when post-quantum requirements are established, a target platform
cannot support P-256/SHA-256, canonicalization changes, a transparency log is
introduced, or online verification becomes an explicit product requirement.
