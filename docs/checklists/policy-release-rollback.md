# Policy release and rollback checklist

Copy this checklist into the approved change or incident record. Replace bracketed
references with access-controlled record IDs, never secrets or raw customer text.

## Change identity

- [ ] Change/incident ID: `[record-id]`
- [ ] Environment and owner recorded
- [ ] Policy ID, semantic version and payload SHA-256 recorded
- [ ] Trust-store and activation-state versions/digests recorded
- [ ] Deployment window, canary scope and rollback owner recorded

## Standard release

- [ ] Author is different from the two independent reviewers
- [ ] Reviewer evidence is complete and both decisions agree
- [ ] Signed payload digest equals reviewed payload digest
- [ ] Signing key is active, in validity window and allowed for the policy ID
- [ ] Private key remained inside the approved KMS/HSM boundary
- [ ] Package/SBOM and release artifact gates passed
- [ ] Offline `normguard policy verify ... --json` returned exit `0`
- [ ] Verified identity, version, digests and signing key match the change record
- [ ] `rollback_authorized` is `false`
- [ ] Synthetic P0/P1/P2/P3 canary checks passed
- [ ] Downstream handles exit `10` review-required explicitly
- [ ] Privacy-safe telemetry is present; no raw content or unbounded labels observed
- [ ] Canary window completed without S0 or unexplained failure
- [ ] Progressive promotion and post-deployment validation completed
- [ ] Previous known-good immutable artifacts remain retrievable

## Routine rollback

- [ ] Incident/change owner approved rollback
- [ ] Target is the exact immutable known-good version and digest
- [ ] Target is not below the current activation floor
- [ ] Current trust store accepts the target with no rollback authorization
- [ ] Target passed synthetic canary and telemetry checks
- [ ] Deployment pointer restored progressively
- [ ] Root cause and forward-fix owner recorded

## Emergency rollback

- [ ] S0/S1 incident is open and normal policy promotion is frozen
- [ ] Safe forward fix cannot meet the containment objective
- [ ] Exact target version and payload digest recorded
- [ ] Two distinct approvers authorized that single target
- [ ] Authorization reason, issue time and expiry are bounded and valid
- [ ] Authorization uses the separate rollback trust purpose
- [ ] Both rollback files supplied; neither supplied alone
- [ ] Verification returned exit `0` and `rollback_authorized=true`
- [ ] Activation floor remains unchanged
- [ ] Synthetic canary passed before progressive restoration
- [ ] Authorization expired/removed after recovery
- [ ] Forward fix, review and post-incident actions assigned

## Key revocation/rotation

- [ ] Affected key ID/digest and policy inventory recorded
- [ ] Compromised key marked revoked, not merely removed
- [ ] Updated trust-store digest verified on each deployment stage
- [ ] Synthetic artifact signed by revoked key returns exit `30`
- [ ] Replacement key has narrow policy scope and controlled validity dates
- [ ] Re-signed policies retain prior independent approvals or are re-reviewed
- [ ] KMS/HSM audit evidence preserved in its approved system

## Closure

- [ ] Expected SDK/CLI outcomes confirmed after full restoration
- [ ] Monitoring, alerting and telemetry delivery restored
- [ ] No sealed ESCI development/final data was accessed
- [ ] Temporary access and emergency artifacts removed or expired
- [ ] Stakeholders received impact, recovery and residual-risk statement
- [ ] S0/S1 review scheduled within five business days
