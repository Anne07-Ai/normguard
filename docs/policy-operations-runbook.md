# Policy operations and incident-response runbook

**Runbook version:** 1.0

**Applies to:** NormGuard Safety Core 0.1.x

**Review cadence:** after every policy incident or at least every 90 days

This runbook governs offline policy verification, activation, rollback and
trust-store operations. It does not authorize production deployment by itself.
Every organization must bind the roles below to named people, its change system,
managed signing service, artifact store and alerting platform.

## Safety invariants

1. Runtime nodes receive signed bundles, public trust stores and activation
   state; they never receive a private signing key.
2. Policy author, two reviewers, signer and deployment approver are separate
   duties. Emergency rollback also requires two distinct approvers.
3. Verification fails closed. Never bypass an error, lower an activation floor,
   accept an unsigned fallback or reuse an expired rollback authorization.
4. Release artifacts are immutable and addressed by SHA-256. A mutable alias may
   point to an immutable artifact but must not replace it.
5. Logs and metrics follow the [privacy-safe observability contract](observability.md).
   Do not copy raw customer text, output, signatures, key material or policy rules
   into tickets, chat or telemetry.
6. The ESCI development and official-test partitions remain sealed. They are not
   operational test fixtures.

## Roles and separation of duties

| Role | Accountable action | Must not perform alone |
|---|---|---|
| Policy author | Propose bounded rule changes and provenance | Approve or sign own change |
| Reviewer 1 and 2 | Independently approve semantics and evidence | Share one review or edit each other's record |
| Signing custodian | Sign approved payload in managed KMS/HSM | Change payload, approvals or activation floor |
| Release manager | Assemble immutable release and change record | Hold/export private signing material |
| Deployment operator | Verify, canary and activate approved release | Override failed verification |
| Incident commander | Classify, contain and coordinate recovery | Approve emergency rollback alone |
| Security/key custodian | Rotate or revoke trust roots | Deploy an unreviewed policy |
| Service owner | Accept residual risk and close incident | Suppress required post-incident evidence |

One person may hold multiple roles in a small team, but no person may both author
and solely approve/sign/deploy the same policy. Record compensating approval when
staffing prevents stronger separation.

## Required artifacts

Keep these artifacts in access-controlled, immutable storage:

- signed policy bundle;
- offline public trust store;
- activation state and its change ID;
- two independent review records;
- release manifest, package/SBOM evidence and artifact digests;
- rollback authorization and rollback-specific trust store when applicable;
- verification output and deployment decision;
- incident record for any emergency change.

Retention must follow organizational policy. Store sensitive source evidence in
its approved system, referenced by access-controlled record ID rather than copied.

## Standard policy promotion

### 1. Prepare and review

The policy author produces a candidate with a unique semantic version and
provenance. Two reviewers independently validate P0–P3 treatment, approved aliases,
source evidence and the absence of private data. The release manager confirms that
the reviewed payload digest is the payload being presented for signing.

Stop if approvals disagree, evidence changed, the schema is incompatible or the
candidate reuses a released version with different content.

### 2. Sign and assemble

The signing custodian signs the canonical payload with an active approved key in
the managed signing boundary. Never export the private key. Assemble the signature,
approvals and payload as the policy bundle, then record its immutable digest.

### 3. Verify offline before deployment

Use an explicit UTC time from the controlled deployment context:

```bash
normguard policy verify policy.json \
  --trust-store trust-store.json \
  --activation-state activation-state.json \
  --verification-time 2026-10-23T09:00:00Z \
  --json
```

Expected result: exit `0` and JSON status `verified`. Confirm policy identity,
version, payload digest, signing-key identity and `rollback_authorized=false`
against the approved change record. Treat every other exit as a stop condition;
see the [CLI exit-code contract](safety-core-cli.md#exit-codes).

### 4. Canary and activate

1. Deploy the immutable artifacts to a bounded canary group.
2. Re-run offline verification on the canary host.
3. Exercise synthetic P0, P1, P2 and P3 inputs only.
4. Confirm success/review/failure handling and privacy-safe telemetry delivery.
5. Observe one agreed canary window with no S0 safety event or unexplained failure.
6. Promote progressively, preserving the previous known-good artifacts.
7. Update activation state only through the approved change. The minimum version
   is monotonic during normal operation.

If any gate fails, stop promotion and restore the previous deployment pointer.
Do not lower activation state to make the candidate pass.

## Routine rollback

Use routine rollback only when the previous artifact still satisfies the current
activation floor. Repoint to the immutable known-good bundle, verify it using the
current trust store and activation state, then canary before wider restoration.

If the target is below the activation floor, routine rollback is forbidden. Use
the emergency process below.

## Emergency rollback

Emergency rollback is for a confirmed or strongly suspected unsafe policy when a
safe forward fix cannot meet the containment objective.

1. Incident commander opens an incident/change record and freezes policy promotion.
2. Security identifies the exact target policy version and payload digest.
3. Two distinct rollback approvers authorize that one target, with bounded issue
   and expiry times and an incident-bound reason code.
4. The rollback custodian signs the authorization using the separate rollback
   trust purpose.
5. Operator verifies with both rollback documents:

```bash
normguard policy verify policy.json \
  --trust-store trust-store.json \
  --activation-state activation-state.json \
  --verification-time 2026-10-23T09:00:00Z \
  --rollback-authorization rollback-authorization.json \
  --rollback-trust-store rollback-trust-store.json \
  --json
```

Expected result: exit `0`, status `verified`, and
`rollback_authorized=true`. Any mismatch, expiry or missing rollback document is a
terminal failure. Canary the target, restore service progressively, retain the
activation floor unchanged, and expire/remove the one-target authorization after
recovery.

Prefer a signed forward fix after containment. Emergency rollback is not a way to
bypass review or permanently weaken anti-replay protection.

## Key rotation and revocation

### Planned rotation

1. Security adds the next public key with a future `not_before` and the narrowest
   allowed policy IDs.
2. Operators distribute the trust store and verify its digest before use.
3. After `not_before`, sign and canary a policy with the new key.
4. Keep the old key active only for the approved overlap window.
5. Mark the old key disabled, confirm rejection with a synthetic old-key artifact,
   then close the change.

### Suspected compromise

1. Declare severity S0 and stop policy deployments/signing with the affected key.
2. Mark the public-key record revoked and distribute the new trust store through
   the emergency change path.
3. Verify that artifacts signed by the revoked key return exit `30`.
4. Identify every policy signed by that key from artifact metadata—never from raw
   customer logs—and re-sign approved content with a clean key if safe.
5. Rotate related credentials, preserve KMS/HSM audit evidence and investigate the
   signing boundary.

Do not delete the historical key record in a way that destroys forensic context.
Revocation must prevent new acceptance while historical evidence remains immutable.

## Monitoring and alerting

Only use bounded fields defined in `TelemetryEvent` and `TelemetryMetric`.
Thresholds below are starting controls and must be baselined per deployment.

| Signal | Initial trigger | Severity | Operator action |
|---|---:|---:|---|
| Policy verification failed | Any production activation | S0 | Block activation; classify code; validate artifact chain |
| `limit_exceeded` normalisation | >1% for 5 minutes or abrupt increase | S1 | Check input/rule growth and availability; do not raise limits blindly |
| `internal_failure` | Any sustained event or >3 in 5 minutes | S1 | Stop rollout; preserve bounded diagnostics; revert candidate |
| Review-required rate | >2× 7-day baseline for 15 minutes | S2 | Inspect policy/release correlation using approved evidence systems |
| Telemetry absent | 5 minutes while request health is non-zero | S2 | Check sink/collector; decisions continue independently |
| Safety event | Any S0-class event | S0 | Contain affected policy and begin incident workflow |

Telemetry loss must not change SDK results, but prolonged loss removes operational
assurance and blocks further promotion.

## Incident response matrix

| Scenario | Detect | Contain | Recover | Minimum evidence |
|---|---|---|---|---|
| Unsafe transformation | Safety event, user report or governed regression | Freeze promotion; isolate policy version; route affected flow to review/abstain | Forward fix or authorized rollback; canary | Runtime/policy versions and digests, bounded decision IDs, timeline, change IDs |
| Tampered policy/evidence | Digest/signature/approval failure | Reject artifact; quarantine source; block activation | Restore from immutable approved artifact; verify chain | Error code, artifact digest, repository/object version, actor and timestamps |
| Signing key compromise | KMS alert, unauthorized signature or key audit anomaly | Revoke key; stop signing/deployment | Rotate key/trust store; re-sign approved policies | KMS/HSM audit record, key ID/digest, affected policy inventory |
| Sensitive-data logging | DLP alert or confirmed raw text in logs | Disable affected sink/export; restrict access; preserve secured evidence | Purge under approved procedure; deploy corrected configuration | Data categories, systems, retention/purge proof; avoid copying exposed values |
| Dependency compromise | Advisory, SBOM or integrity alert | Freeze builds/releases; quarantine artifacts | Pin clean version, rebuild and verify artifacts | Advisory, package/version/hash, SBOM, build provenance |
| Availability degradation | Limit/internal failures, latency or error-rate alert | Halt rollout; revert known-good pointer; apply upstream backpressure | Repair cause; canary; restore progressively | Counts, bounded reason/limit, versions, capacity timeline |
| Rollback failure | Exit `30`, authorization mismatch/expiry or unhealthy canary | Keep current safe route/review mode; do not lower floor | Issue new valid authorization or deploy forward fix | Verification code, target version/digest, authorization/change IDs |

### Severity objectives

| Severity | Meaning | Acknowledge | Containment target | Authority |
|---|---|---:|---:|---|
| S0 | Integrity/safety breach, key compromise or active sensitive-data leak | 15 min | 60 min | Incident commander + security owner |
| S1 | Material availability failure or repeated internal errors | 30 min | 4 h | Service owner + on-call engineer |
| S2 | Degraded assurance, telemetry loss or anomalous review volume | 4 h | 1 business day | Service owner |

Local contractual obligations override these initial targets.

## Triage sequence

1. Record detection time, reporter, environment and bounded event/reason code.
2. Identify runtime version, policy version/digest, trust-store version/digest and
   activation change ID without copying policy/customer content.
3. Determine whether the issue is integrity, safety, confidentiality or availability.
4. Stop promotion and preserve immutable artifacts/audit records.
5. Select forward fix, routine rollback, emergency rollback or review/abstain mode.
6. Execute the matching checklist with a second operator.
7. Verify containment using synthetic fixtures and privacy-safe telemetry.
8. Communicate impact and residual risk; do not claim recovery before validation.

## Closure and post-incident review

Close only after containment and recovery checks pass, temporary authorizations are
expired, revoked keys are rejected, monitoring is restored, and the service owner
accepts residual risk. Complete a blameless review within five business days for S0
or S1 incidents. Update tests, threat mappings, alerts and this runbook; rotate any
exposed secret through the owning platform rather than recording it here.

Use the [release and rollback checklist](checklists/policy-release-rollback.md) in
every change ticket. Architectural rationale remains in
[ADR 0004](adr/0004-policy-signing-and-rollback.md), and threat coverage is defined
in the [Safety Core threat model](threat-model-safety-core.md).
