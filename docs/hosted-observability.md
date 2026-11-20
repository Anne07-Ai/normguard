# Hosted observability boundary

Hosted telemetry is operational evidence, not an audit log. The implementation uses
closed schemas for logs, metrics and traces so callers cannot attach arbitrary fields.

## Data classification

Allowed telemetry fields are operation, bounded outcome, public error code, duration,
schema version and a server-controlled correlation identifier in structured logs.
Metrics expose only operation, outcome and public error code as low-cardinality labels.
Trace attributes use the same bounded label set and exclude the correlation identifier.

The following data is prohibited from all hosted telemetry:

- request and response text;
- raw tenant or principal identifiers and audit pseudonyms;
- JWTs, claims, authorization headers and session material;
- policy bodies, signatures, verification keys and artifact identifiers;
- DSNs, SQL, dependency exceptions, stack traces and internal topology.

Mandatory privileged audit remains a separate fail-closed control. Telemetry is
best-effort: a log, metric or trace sink outage must not change an authorized data-plane
response.

## Production adapter

Use an OpenTelemetry Collector as the single egress point. Export structured logs and
metrics to CloudWatch using workload identity, TLS and least-privilege IAM. Keep the
collector configuration versioned and deny unknown attributes before export. Do not
enable automatic request-body, response-body, HTTP-header or exception capture.

Recommended alerts are based on bounded aggregates only:

- rejected and unavailable outcome rate;
- p95 duration against the declared service objective;
- telemetry exporter drop/failure rate;
- absence of request counters for an otherwise healthy workload.

Dashboards must not group by correlation identifier. Correlation IDs are diagnostic log
keys only and must have bounded retention and role-restricted access. Retention,
encryption, deletion and access-review settings remain platform-owner responsibilities.

This contract does not deploy AWS resources and does not close B-001, B-002, B-003 or
NG-T22.
