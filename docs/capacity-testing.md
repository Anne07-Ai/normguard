# Representative capacity-test contract

`NG-CAPACITY-001` is a deployment-neutral harness for measuring the bounded Safety
Core on infrastructure selected by an adopting platform. It produces digest-verifiable,
aggregate evidence. It does not provision infrastructure, replay customer traffic or
authorize a production service-level agreement.

## Profiles

| Scope | Purpose | Minimum requirements | Permitted claim |
|---|---|---|---|
| `local-smoke` | Validate harness wiring and evidence generation | Bounded synthetic requests | Harness operational on the observed host |
| `representative` | Measure an adopter-declared deployment candidate | At least 300 seconds and 1,000 measured requests | Evidence for only the declared target environment and workload |

A profile freezes concurrency, warm-up count, request count, minimum duration,
latency p95, throughput, error-rate, traced-memory and process-CPU budgets. The
evidence binds the full source commit, artifact, target-environment, workload and
profile SHA-256 identities. Raw inputs, outputs and exception text are excluded.

## Decision

The harness returns `PASS` only when every declared check passes. Otherwise it returns
`STOP`; operators must not remove failed samples or edit evidence to promote a result.
The canonical envelope digest detects mutation, and the verifier rejects unknown
top-level evidence fields and decisions inconsistent with their checks.

## Local smoke

Build the wheel and derive identities without placing raw environment details in the
evidence:

```bash
uv build --wheel --no-sources
wheel=$(find dist -name '*.whl' -type f -print -quit)
artifact_sha=$(sha256sum "$wheel" | cut -d' ' -f1)
environment_sha=$(python - <<'PY'
import hashlib
import platform
print(hashlib.sha256(platform.platform().encode()).hexdigest())
PY
)
python -m normguard.capacity \
  --source-commit "$(git rev-parse HEAD)" \
  --artifact-sha256 "$artifact_sha" \
  --target-environment-sha256 "$environment_sha"
```

Local smoke evidence is **not** production capacity or SLA evidence. It exists only
to verify the harness, closed schema and artifact bindings.

## Representative execution requirements

Before executing a representative profile, the adopting platform owner must freeze:

1. target compute, operating system, container/runtime limits and deployment topology;
2. policy-size and input-size distributions without customer text;
3. concurrency, arrival pattern, warm-up, sustained duration and request count;
4. latency, throughput, error-rate, memory and CPU acceptance budgets;
5. the exact source commit, installed artifact and workload generator identities;
6. an approved monitoring method for total RSS, host CPU and saturation signals.

The built-in `tracemalloc` observation covers Python allocations only. Representative
capacity review must add platform-native RSS, CPU, throttling and saturation evidence
outside the library envelope and bind that evidence in the adopter's change record.

## Blocker status

Release blocker B-003 remains `OPEN`. This harness prepares the reproducible contract;
only a passing run on declared representative infrastructure, reviewed by the adopting
platform owner, can support a capacity claim. It does not close B-001 independent
security review or B-002/NG-T22 hosted-service controls.
