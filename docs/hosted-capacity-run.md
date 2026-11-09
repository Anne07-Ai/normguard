# Private Hugging Face representative capacity run

This runbook executes `NG-CAPACITY-001` against only the declared private,
single-tenant NormGuard Docker Space on Hugging Face CPU Basic hardware.

It does not approve a public or multi-tenant service, establish a production SLA,
close B-001, or remove the B-002/NG-T22 hosted-service restriction.

## Frozen profile

| Field | Value |
|---|---:|
| Profile | `hf-cpu-basic-private-pilot@1.0.0` |
| Duration | at least 300 seconds |
| Measured requests | 1,000 |
| Warm-up requests | 20 |
| Concurrency | 4 |
| Arrival rate | approximately 3.33 requests/second |
| p95 budget | 1,000 ms |
| Minimum throughput | 3 requests/second |
| Maximum error rate | 0 |

Inputs are bounded synthetic strings defined in
`src/normguard/hosted_capacity.py`. Raw requests, response bodies, credentials and
exception text are excluded from evidence.

## Required identities

Use the exact source commit deployed to the Space. The artifact digest is the SHA-256
of the exact Git archive used as the deployment build context. The target-environment
digest is calculated from a reviewed, non-secret declaration rather than mutable host
details.

For the current controlled pilot:

```bash
cd ~/normguard

DEPLOYED_SOURCE_COMMIT="c0228545f9ca5f6b931189ab59049da57eabdc77"

ARTIFACT_SHA256="$(
  git archive --format=tar "$DEPLOYED_SOURCE_COMMIT" |
    shasum -a 256 |
    awk '{print $1}'
)"

TARGET_ENVIRONMENT_SHA256="$(
  python - <<'PY'
import hashlib
import json

environment = {
    "access": "private",
    "accelerator": "none",
    "cpu": 2,
    "hardware": "cpu-basic",
    "memory_gb": 16,
    "provider": "hugging-face-spaces",
    "repo": "lakshmianne/normguard-private-pilot",
    "runtime": "docker",
    "tenant": "single",
}
payload = json.dumps(
    environment, ensure_ascii=False, separators=(",", ":"), sort_keys=True
).encode()
print(hashlib.sha256(payload).hexdigest())
PY
)"
```

## Authentication

The private Space gateway owns the standard `Authorization` header. NormGuard owns
`X-NormGuard-Token`. The runner reads their values only from `HF_TOKEN` and
`NORMGUARD_BEARER_TOKEN`; neither token is accepted as a command-line argument or
written to evidence.

Verify both variables exist without printing them:

```bash
test -n "$HF_TOKEN"
test -n "$NORMGUARD_BEARER_TOKEN"
```

## Execution

Run from the reviewed runner commit after its CI passes:

```bash
uv run --locked python -m normguard.hosted_capacity \
  --source-commit "$DEPLOYED_SOURCE_COMMIT" \
  --artifact-sha256 "$ARTIFACT_SHA256" \
  --target-environment-sha256 "$TARGET_ENVIRONMENT_SHA256"
```

The canonical output is written to
`artifacts/capacity/hf-private-representative.json`. A non-zero exit means STOP; do
not delete failed samples, edit the evidence, or claim capacity approval.

Verify it independently:

```bash
uv run --locked python - <<'PY'
from pathlib import Path
from normguard.capacity import assert_capacity_passed, load_capacity_evidence

path = Path("artifacts/capacity/hf-private-representative.json")
envelope = load_capacity_evidence(path)
assert_capacity_passed(envelope)
print(envelope["evidence_sha256"])
PY
```

## Resource-observation limitation

The envelope records the load-generator Python process CPU time and traced Python
allocations. These are not server RSS or host CPU measurements. Hugging Face Space
CPU Basic does not expose canonical per-run server RSS, CPU throttling or saturation
telemetry through this runner. If the platform UI exposes observations, record them
separately in the change record and bind that record by digest; do not relabel client
metrics as server metrics.

A passing envelope supports only the declared private CPU Basic pilot and workload.
B-003 remains open for any production SLA or general capacity claim.
