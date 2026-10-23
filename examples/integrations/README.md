# Production integration example

`service.py` is a framework-neutral application boundary around NormGuard. It is
tested as executable repository code and uses only the core installation profile.

## Lifecycle

Create one service during application startup with policy documents obtained from
a trusted, read-only configuration boundary:

```python
from datetime import UTC, datetime

from examples.integrations.service import PolicyDocuments, SafetyCoreService
from normguard import RuntimeLimits

documents = PolicyDocuments(
    policy_bundle=policy_bytes,
    trust_store=trust_store_bytes,
    activation_state=activation_state_bytes,
)

service = SafetyCoreService(
    documents,
    verification_time=datetime.now(UTC),
    runtime_limits=RuntimeLimits(max_input_bytes=16_384),
    telemetry=privacy_safe_sink,
)
```

Startup raises `SDKError` if verification fails. The process should remain
unready and terminate; it must not serve with an unsigned or stale fallback.

Reuse the service for every request. `accepted`, `review_required`,
`invalid_input`, `limit_exceeded` and `unavailable` are distinct application
outcomes. An HTTP host might map them to `200`, `202`, `400`, `413` and `503`,
respectively, but that transport choice belongs outside NormGuard. A queue worker
can route review-required messages to an approved human-review queue.

## Atomic policy reload

Call `reload()` with immutable replacement bytes and an explicit verification
time. The candidate is parsed, verified and constructed before the lock is taken.
Only a complete candidate is swapped in. A failed reload returns a bounded error
code and leaves the last known-good core active.

An in-flight call retains a reference to one immutable core; later calls observe
the replacement. There is no partially updated policy state. Across multiple
processes or pods, use the platform's rolling deployment/canary mechanism because
the in-process lock cannot coordinate separate replicas.

## Trust and privacy boundaries

- Authenticate and authorize callers before invoking this adapter.
- Enforce transport/body limits at the ingress and again with `RuntimeLimits`.
- Mount policy, trust and activation documents read-only and verify their release
  digests in the deployment system.
- Never mount a private signing key. Signing belongs in a managed KMS/HSM workflow.
- Do not log request text, output text, signatures, policy digests, decision IDs,
  evidence spans or keys. Inject only a `TelemetrySink` implementing NormGuard's
  closed [observability contract](../../docs/observability.md).
- Treat `review_required` as governed completion, not ordinary success.
- Treat reload failure as an operational alert; do not discard the active core.

## FastAPI or worker wiring

Framework startup should build `SafetyCoreService` before readiness becomes true.
Store the instance in application state or dependency injection, not a mutable
module global. A request handler passes its validated string to `normalise()` and
maps the returned enum to its transport contract. Do not catch and convert startup
verification errors into a ready service.

For asynchronous frameworks, the core call is CPU-local and bounded. Benchmark in
the target workload before deciding whether to call inline or use a bounded thread
pool. Never create an unbounded executor or one core per request.

## Docker and Kubernetes guidance

- Use the core wheel only; do not install `research` or `esci` extras in the
  serving image.
- Use a minimal Python 3.12 image, pin it by digest, run as a non-root user, use a
  read-only root filesystem and drop Linux capabilities.
- Mount the signed policy, public trust store and activation state from a read-only
  immutable volume. Do not bake environment-specific trust state into the image.
- A startup probe should remain false until `SafetyCoreService` constructs.
- Readiness reports only `HealthResponse`; liveness checks process health and must
  not repeatedly reverify policy or include sensitive identifiers.
- Set CPU/memory requests and limits from the performance gate plus workload tests.
- Roll out by immutable image and policy digests, canary first. Retain the previous
  known-good artifacts and follow the
  [operations runbook](../../docs/policy-operations-runbook.md) for rollback.
- Use a PodDisruptionBudget and graceful termination so in-flight calls finish on
  their captured immutable core.

This example is an integration pattern, not a hosted service, tenant boundary or
production approval.
