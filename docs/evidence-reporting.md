# Evidence reporting and CI decisions

`EvidenceReporter` combines matched C0/C4 retrieval evidence, paired query deltas,
the terminology-safety audit, statistical evidence, operational latency and immutable
run manifests. It validates configuration, dataset, split, partition and BM25
alignment before producing a decision.

PASS requires every pre-registered H1-H4 gate: a positive nDCG@10 bootstrap lower
bound, at least 0.005 mean absolute improvement, exact identifier retention of 1.0,
zero protected-term violations, zero S0 events, and candidate p95 latency within both
2x baseline and 10 ms additional latency. Safety failure forces FAIL. Missing mandatory
evidence produces INCONCLUSIVE. Mixed utility or latency with clean safety produces
REVIEW.

The reporter atomically writes canonical `evidence.json`, `report.md`, and
`report.svg`. JSON contains a SHA-256 digest of the canonical report and
`verify_json()` detects tampering. CI exit codes are 0 for PASS/REVIEW, 1 for FAIL,
and 2 for INCONCLUSIVE.

Development evidence does not authorize official final-test access or production
deployment. Final-test execution still requires the complete approval checklist and
two-person protocol freeze review.
