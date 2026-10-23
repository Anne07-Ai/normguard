# Safety Core performance contract

Issue #82 freezes a synthetic performance regression gate before runtime optimisation.
It does not reopen the stopped retrieval experiment and does not access ESCI data.

## Frozen fixture

The fixture contains 100 deterministic exact-preserve rules, 20 matches and bounded
synthetic filler text. Its identity is derived from the policy digest, text digest and
declared dimensions. Raw fixture text is excluded from evidence.

## Protocol and budgets

CI performs 10 warm-up calls, 25 policy-construction samples and 100 steady-state
normalisation samples. The initial conservative regression budgets are:

| Measure | Budget |
|---|---:|
| Construction p95 | <= 250 ms |
| Normalisation p95 | <= 100 ms |
| Normalisation maximum | <= 500 ms |
| Throughput | >= 10 operations/second |
| Peak traced Python allocation | <= 32 MB |
| Canonical decision evidence | <= 200 KB |

These thresholds are regression tripwires, not target architecture capacity. Changes to
the fixture, protocol or budgets require explicit review and a new fixture version when
semantic comparability changes.

## Evidence

Run locally with the core-only installation:

```bash
uv sync --locked --no-dev
uv run --locked --no-dev python -m normguard.performance
```

The command writes canonical JSON to
`artifacts/performance/safety-core-performance.json`. The envelope contains a SHA-256
digest of its evidence body and fails when any budget check fails.

## Interpretation limits

GitHub-hosted runners have variable neighbours and hardware. Their results identify large
regressions but do not establish a production SLA, concurrency limit or capacity plan.
Production sizing requires representative hardware, policy distributions, text sizes,
traffic concurrency and sustained load tests. `tracemalloc` measures Python allocations,
not total process RSS.
