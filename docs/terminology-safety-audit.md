# Terminology-safety audit

`TerminologySafetyAuditor` converts raw observed spans into deterministic safety
evidence. Raw observations use `ObservedSpan` because the strict `SpanTrace` contract
correctly prevents invalid P0 and P3 records from being constructed; an independent
audit must still be able to represent and diagnose corrupted engine output.

The auditor verifies source boundaries, versioned rule identity and policy class
before applying P0, P1 and P3 gates. A changed P0 identifier is F04/S0. Invalid
boundaries and unknown or mismatched rules fail closed as F07/S0 and F10/S0.
Unauthorised P1 and P3 output is F05/S1. Each finding retains its case ID, span index,
source offsets, rule ID, policy class and engine metadata.

Metrics include protected-term violation rate, exact identifier retention, harmful
transformation rate, review/abstention rate and S0 count. A missing denominator is
serialized as `null`, not zero. S0 or S1 findings produce `FAIL`; absence of protected
evidence produces `INCONCLUSIVE`; clean protected evidence produces `PASS`.

The result can be converted into the existing `EvidenceBundle` by supplying a matching
`RunManifest`. The auditor does not access datasets and must run only on approved
calibration or development evidence until final-test access is authorised.
