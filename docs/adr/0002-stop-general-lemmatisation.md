# ADR 0002: Stop the general-lemmatisation retrieval hypothesis

- **Status:** Accepted
- **Date:** 2026-10-21
- **Decision:** STOP — utility hypothesis not supported
- **Protocol:** NG-POC-001
- **Scope:** Amazon ESCI calibration partition only

## Context

NormGuard evaluated whether policy-first English lemmatisation improves BM25 retail
retrieval while preserving protected terminology. The protocol froze the dataset
split, BM25 parameters, statistical seed, safety gates, utility threshold, latency
gates and reviewed policy before confirmatory evaluation.

The official ESCI test partition was not read or transformed. Development was not
opened because calibration did not pass.

## Evidence

Both runs used 2,052 queries from the same frozen calibration partition, BM25
`k1=0.9`, `b=0.75`, statistical seed 7 and policy SHA-256
`7eeb232eb27d1e50f6e5ab6e8519b0fd11c4454bb5cb3908a8d9fac1f339ada7`.

| Measure | v0.1.0 | v0.2.0 conservative refinement |
|---|---:|---:|
| C0 nDCG@10 | 0.812533 | 0.812533 |
| C4 nDCG@10 | 0.814379 | 0.813144 |
| Mean paired delta | +0.001846 | +0.000611 |
| CI95 lower | -0.000913 | -0.000811 |
| CI95 upper | +0.004656 | +0.002195 |
| Randomisation p-value | 0.192581 | 0.457654 |
| Exact identifier retention | 1.000000 | 1.000000 |
| Protected-term violation rate | 0.000000 | 0.000000 |
| S0 events | 0 | 0 |
| Decision | REVIEW | REVIEW |

The pre-registered utility gate required a mean nDCG@10 improvement of at least
`+0.005` and a bootstrap CI95 lower bound above zero. Neither run passed. The
single permitted diagnostic-driven refinement reduced the estimated utility gain.

Recorded v0.1.0 evidence-envelope SHA-256:
`139a256f04f8ed4095ff0146aca6ba4501432db3727334feb76983e1ace12e36`.

Recorded v0.2.0 evidence-envelope SHA-256:
`f093d20e9a96acd21e137d6f4350496beef8bc35130e797d610a3333470ca8cf`.

Recorded v0.2.0 evidence artifact SHA-256:
`32cc53af614a1a0004a97574094fa7f89dc51445180ad8198bd4008703bba3e4`.

## Decision

Stop the general-lemmatisation retrieval hypothesis for this protocol.

General lemmatisation is not approved for promotion into the NormGuard retrieval
pipeline. No additional rule, morphology or threshold tuning may be performed
against the frozen calibration partition. Development and official-test partitions
remain sealed.

This is a negative utility decision, not a safety failure. Both evaluated
configurations satisfied the protected-terminology safety evidence.

## Consequences

- No development approval file will be issued.
- No official-test execution is authorised.
- Existing thresholds will not be relaxed after observing the results.
- Calibration subgroups will not be promoted as the primary result.
- v0.1.0, v0.2.0, policy-review and diagnostic artifacts remain private,
  immutable research evidence.
- The conservative v0.2.0 implementation remains available for reproducibility,
  but is not a production recommendation.

## Reusable outcomes

The rejected utility hypothesis does not invalidate these independently useful
components:

- deterministic protected-term policies and span traces;
- two-reviewer policy workflow and disagreement controls;
- sealed dataset partitions and approval gates;
- paired retrieval evaluation and reproducible statistical evidence;
- terminology-safety auditing;
- tamper-evident reports and calibration diagnostics.

## Future research boundary

Future retrieval-normalisation research requires a new protocol version and a new
data boundary. Acceptable directions include narrowly authorised, intent-specific
normalisation; query classification before transformation; or evaluation with a
different retrieval architecture. Such work must define new hypotheses and
pre-register thresholds before inspecting new calibration evidence.

This ADR does not authorise use of the current development or official-test
partitions for exploratory tuning.

## Alternatives rejected

- **Tune again on the current calibration data:** rejected because it would violate
  the single-refinement rule and increase overfitting risk.
- **Relax the +0.005 or confidence-interval gates:** rejected as post-hoc threshold
  manipulation.
- **Proceed to development because safety passed:** rejected because safety is
  necessary but does not establish retrieval utility.
- **Select only favorable query subgroups:** rejected because descriptive subgroup
  evidence cannot replace the pre-registered primary result.
