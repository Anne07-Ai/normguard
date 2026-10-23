# Safety Core Phase 3 release-readiness review

- **Review date:** 23 October 2026
- **Candidate:** NormGuard Safety Core 0.1.0
- **Decision:** `READY_FOR_CONTROLLED_PILOT`
- **Publication:** not performed or authorized by this review

## Executive decision

The Safety Core engineering vertical slice is complete enough for a controlled,
offline or single-tenant library pilot using signed policies, bounded runtime
limits, explicit review routing and the documented operational controls.

It is **not approved** as a hosted multi-tenant service, an unrestricted external
production release or a production retrieval recommendation. Independent security
review is not yet recorded. Production capacity, identity, authorization, tenant
isolation, network controls and deployment-specific compliance belong to the
adopting system and remain release gates for those scopes.

This is deliberately a conditional architectural decision: the core library has
strong internal contracts, while the controls that only exist at a service or
enterprise platform boundary are not misrepresented as implemented.

The canonical machine-readable decision is
[`safety-core-phase-3-readiness.json`](safety-core-phase-3-readiness.json).

## Approved use

- Controlled evaluation or pilot in an offline or single-tenant application.
- Verification of immutable signed policies before deterministic normalisation.
- Synthetic and organization-approved safety testing.
- Integration through the SDK/CLI with bounded inputs and explicit handling of
  success, review-required and terminal failure.
- Privacy-safe telemetry and the documented release/rollback process.

Adopters must name owners for signing, deployment, incident response, security
review and platform operation before a real workload is introduced.

## Prohibited use and claims

- No hosted or multi-tenant service until NG-T22 authentication, authorization,
  tenant isolation, storage/cache scoping, rate limiting and tenant-safe telemetry
  are designed and independently tested.
- No claim that the stopped general-lemmatisation approach improves retrieval.
- No bypass of signatures, trust stores, activation floors or review-required
  outcomes.
- No access to the sealed ESCI development or official-test partitions.
- No PyPI publication, release tag or public promotion from this review.
- No claim of universal semantic correctness, production SLA or formal security
  certification.

## Evidence-based gates

| Gate | Result | Evidence |
|---|---|---|
| Minimal core installation | PASS | [install profiles](../install-profiles.md), contract install matrix |
| Signed policy and rollback trust | PASS | ADR 0004, verifier contracts and mutation tests |
| Bounded deterministic runtime | PASS | runtime limits, typed failures, parallel determinism tests |
| Stable SDK and CLI | PASS | SDK/CLI tests, closed JSON and distinct exit statuses |
| Adversarial resilience | PASS | deterministic Hypothesis suite and synthetic injection corpus |
| Performance regression budget | PASS | versioned synthetic fixture and CI gate |
| Privacy-safe observability | PASS | closed events/metrics and sink-isolation tests |
| Operations and incident response | PASS | runbook and change-ticket checklist |
| Production integration pattern | PASS | fail-closed startup, atomic reload and concurrent tests |
| Package artifact gate | PASS | clean wheel install, metadata, SBOM and digest evidence |

The complete path-level evidence inventory and named control owners are in the
machine-readable record. CI outcomes on the review pull request are release evidence;
the JSON does not hard-code transient workflow run IDs.

## Threat-model reconciliation

NG-T01–T10 and NG-T12–T21/23/24 have implemented controls exercised through the
contract, adversarial, performance, release and integration suites. NG-T11 is an
accepted residual risk: documentation prohibits policy secrets, but adopters still
need classification and secret scanning in their policy-authoring workflow.

NG-T22 remains an explicit blocker for any hosted multi-tenant service. The core
library has no tenant concept and therefore cannot provide tenant authorization or
isolation. Building a web endpoint around it does not satisfy that threat.

## Open release blockers

| Blocker | Required before | Owner | Closure evidence |
|---|---|---|---|
| Independent security review | External production-readiness claim | Security review owner | Signed review record; findings resolved or risk-accepted |
| NG-T22 service controls | Hosted/multi-tenant deployment | Adopting service owner | AuthN/AuthZ, isolation, rate-limit and tenant-leakage tests |
| Representative capacity test | Production SLA/capacity claim | Adopting platform owner | Sustained workload report on target infrastructure |

These blockers do not prevent synthetic evaluation or a controlled single-tenant
pilot, but they prevent stronger claims.

## Residual risk ownership

1. A correctly signed but poorly reviewed policy can be unsafe. Two-person review,
   independent security review and governed canaries remain required.
2. A compromised host can inspect process memory or replace runtime behavior. The
   platform owner must harden the host, use read-only mounts and keep signing keys
   in a managed KMS/HSM boundary.
3. Synthetic CI performance budgets are regression tripwires, not capacity plans.
4. Downstream systems can mishandle review/failure statuses; adopters must test
   their routing contract.
5. Locked, audited dependencies reduce but do not eliminate supply-chain risk.

## Data and research boundary

Phase 3 tests use synthetic fixtures. The ESCI development and official-test
partitions were not read or transformed. Phase 2's retrieval decision remains
`STOP`: the utility hypothesis was not supported, and Phase 3 does not revive it.

## Promotion path

Before any external production claim:

1. complete independent security review and close/accept every finding;
2. bind runbook roles to named operators and enterprise systems;
3. complete adopter-specific threat modelling, privacy review and capacity testing;
4. implement/test NG-T22 if a service or tenant boundary is introduced;
5. create a separate protected publication issue/workflow using OIDC trusted
   publishing and immutable tag-to-artifact verification;
6. rerun all four required workflows on the exact release commit;
7. approve release notes containing these supported scopes and non-claims.

Until then, the honest status is controlled-pilot candidate—not “production ready.”
