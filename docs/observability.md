# Privacy-safe observability

NormGuard exposes optional structured telemetry through `TelemetrySink`. It is
disabled unless a caller supplies a sink. Telemetry is best effort: a broken
sink never changes a successful result or the SDK error returned to the caller.

The versioned, closed event schema contains only operation, status, a bounded
reason, and non-negative counts. It deliberately excludes query and output
text, hashes, policy/key/decision identifiers, signatures, evidence spans,
rule names, timestamps, and caller-defined labels. Metrics have four fixed
names and three enum-valued labels, preventing unbounded label cardinality.

`JsonLinesTelemetrySink` emits canonical JSON Lines to a caller-owned stream.
It performs no network or filesystem access by itself. Applications remain
responsible for retention, access control, transport, and regional compliance.

```python
from normguard import JsonLinesTelemetrySink, build_safety_core

with open("normguard-safe.jsonl", "a", encoding="utf-8") as stream:
    core = build_safety_core(
        policy_bytes,
        trust_bytes,
        activation_bytes,
        verification_time=now,
        telemetry=JsonLinesTelemetrySink(stream),
    )
    result = core.normalise(query)
```

This boundary mitigates raw-content leakage (NG-T10), high-cardinality or
attacker-controlled telemetry (NG-T18), and observability-induced runtime
failure (NG-T19). Tests assert leakage resistance, deterministic serialization,
closed labels, and fail-open sink isolation while the safety decision itself
continues to fail closed.
