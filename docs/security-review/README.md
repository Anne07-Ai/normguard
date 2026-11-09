# Independent security review package

## Purpose and decision boundary

This package prepares NormGuard Safety Core for review by a qualified reviewer who
is independent of the implementation and self-assessment. It is not a completed
review, certification, penetration test or production authorization.

The reviewer must obtain the exact source revision independently, verify its commit
identity, and record the reviewed artifact digest. Repository maintainers may answer
questions and remediate findings, but must not populate the reviewer identity,
independence statement, conclusion or sign-off on the reviewer's behalf.

The current release decision remains `CONTROLLED_PILOT_COMPLETED`. Blocker B-001
remains `OPEN`; NG-T22 remains `BLOCKED_FOR_HOSTED_SERVICE`.

## Package contents

- [`evidence-index.json`](evidence-index.json) maps every NG-T01–NG-T24 threat to
  repository evidence and its current engineering disposition.
- [`review-record.schema.json`](review-record.schema.json) is the closed contract for
  reviewer identity, findings, risk acceptance and sign-off.
- [`review-record.template.json`](review-record.template.json) is an unreviewed,
  non-attesting starting point that validates against the schema.
- The canonical threat model is
  [`../threat-model-safety-core.md`](../threat-model-safety-core.md).
- The current readiness record is
  [`../releases/safety-core-phase-3-readiness.json`](../releases/safety-core-phase-3-readiness.json).

## Reviewer procedure

1. Confirm organizational independence and disclose conflicts before testing.
2. Freeze the full source commit, wheel SHA-256 and review scope in the record.
3. Reproduce the locked install and required CI/security suites from repository
   instructions; record commands and digest-only evidence references.
4. Review all 24 threat rows. Treat an engineering `CONTROLLED` disposition as a
   claim to verify, not proof.
5. Record each finding with severity, affected threat IDs, evidence references,
   remediation status and owner. Never copy secrets or customer text into findings.
6. Risk acceptance requires an identified accepting authority, rationale and expiry.
   The implementer cannot silently convert an unresolved finding into acceptance.
7. Complete the conclusion and sign-off only after every finding is remediated,
   explicitly risk-accepted or declared blocking.

## Required independent outputs

The completed record must identify the reviewer and organization, state independence,
bind the exact commit and artifact, cover NG-T01–NG-T24, and contain an explicit
conclusion. Any blocking finding, missing threat coverage, unverifiable evidence or
invalid signature leaves B-001 open.

Even a successful independent core review does not close B-002 or B-003. Hosted or
multi-tenant approval still requires NG-T22 service controls and representative
capacity evidence on the adopting platform.

## Privacy and handling

Use synthetic inputs. Store only bounded descriptions, repository paths and digests.
Do not store private keys, signatures copied from production, credentials, customer
content, raw pilot text, local filesystem paths or transient CI run URLs in this
package.
