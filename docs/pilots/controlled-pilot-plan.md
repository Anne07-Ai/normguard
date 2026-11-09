# NormGuard controlled Safety Core pilot

## Decision boundary

This protocol evaluates the Safety Core as an **offline or single-tenant library**.
It does not authorize a hosted multi-tenant service, package publication, an external
production-readiness claim or a retrieval-utility claim. ESCI development and
official-test partitions remain sealed.

The automated decision is `PASS` only when every hard gate passes. Any hard-gate
failure produces `STOP`; operators must not override that result by editing evidence.

## Owners required before execution

| Role | Accountability |
|---|---|
| Policy owner | corpus approval, expected outcomes and P0/P1/P3 safety decisions |
| Security owner | signed-policy negative controls and independent findings |
| Integration owner | explicit success, review and terminal-failure routing |
| Platform owner | declared host, workload and performance budgets |
| Privacy owner | approved data classification and evidence/log review |
| Operations owner | rollback and key-compromise exercises |
| Release owner | commit, artifact, configuration and evidence traceability |

Named people and enterprise system references belong in the adopting organization's
change record, not in the repository's reusable synthetic example.

## Frozen inputs

Before execution, record and freeze:

1. the full 40-character source commit;
2. the installed wheel or deployment artifact SHA-256;
3. signed policy bundle, trust store and activation state;
4. runtime limits and pilot budgets;
5. the synthetic or organization-approved corpus and expected outcomes;
6. rollback and key-compromise drill evidence digests;
7. the declared host and Python environment in the operator's change record.

Raw pilot text stays in the approved corpus boundary. The generated evidence contains
only aggregate counts, allowlisted statuses and cryptographic identities.

## Automated gates

| Gate | Required result | Stop condition |
|---|---|---|
| G-01 Integrity | tampered, unsigned, incompatible and rolled-back policies all fail before runtime construction | any negative control verifies |
| G-02 Safety | 100% P0 retention, zero unauthorized transformations, expected outcomes match | any mismatch or no P0 evidence |
| G-03 Determinism | repeated semantic results are identical | any repeated result differs |
| G-04 Reliability | review-required and terminal limit failures are observed explicitly | missing or silently accepted route |
| G-05 Performance | declared p95 latency and throughput budgets pass | either budget fails |
| G-06 Privacy | evidence uses aggregate/digest-only closed schema | raw pilot text reaches evidence |
| G-07 Operations | rollback and key-compromise exercises have passing evidence digests | either exercise missing or failed |
| G-08 Traceability | commit, artifact, policy, configuration and corpus identities are present | identity missing or malformed |

Performance results apply only to the declared host and workload. They are not a
production SLA or general capacity claim.

## Programmatic execution

Construct `PilotCase`, `PilotConfig` and `OperationalDrill` values, then call
`normguard.run_controlled_pilot`. Verify the returned envelope with
`normguard.verify_pilot_envelope` before storing it. Use
`normguard.write_pilot_artifacts` to write canonical JSON evidence and a Markdown
decision report.

The committed
[`synthetic evidence`](../../examples/pilots/controlled-pilot-evidence.example.json)
and [`synthetic report`](../../examples/pilots/controlled-pilot-report.example.md)
demonstrate the closed output contract. Placeholder commit/artifact/drill digests in
that example are not evidence for a real deployment.

## First synthetic execution workflow

The read-only [`controlled-pilot.yml`](../../.github/workflows/controlled-pilot.yml)
workflow executes the first synthetic pilot on every pull request and `main` push.
It builds and installs the exact wheel, binds the checked-out full commit and wheel
SHA-256 into evidence, creates an ephemeral in-memory signing key, exercises rollback
and revoked-key candidate rejection, and uploads the JSON evidence plus Markdown report.

The job has no publishing credentials and retains artifacts for 30 days. Its evidence
is execution proof for the declared GitHub-hosted runner only; variable shared-runner
hardware means the performance result is a regression signal, not a production SLA.
The ephemeral private key, signed bundle, trust store and raw synthetic cases are not
written to the artifact or job summary.

Local execution against a built wheel uses the same runner:

```bash
uv build --wheel --no-sources
python scripts/run_synthetic_controlled_pilot.py \
  --wheel dist/normguard-0.1.0-py3-none-any.whl \
  --source-commit "$(git rev-parse HEAD)" \
  --output-dir artifacts/controlled-pilot
```

The example corpus at
[`examples/pilots/synthetic-cases.json`](../../examples/pilots/synthetic-cases.json)
contains no customer data. A real pilot must use a separate organization-approved
adapter boundary and must not commit customer inputs.

## Stop and rollback

- Treat `STOP` as terminal for the candidate artifact/policy/configuration.
- Keep the last known-good verified policy active.
- Follow the [policy operations runbook](../policy-operations-runbook.md).
- For signature or key incidents, disable the key, preserve privacy-safe evidence,
  activate only a separately verified policy and record incident ownership.
- Never weaken a limit, gate or expected outcome merely to turn `STOP` into `PASS`.

## Promotion

A passing controlled pilot does not close the Phase 3 blockers. Independent security
review remains required before an external production-readiness claim. NG-T22 remains
blocked until a separately designed hosted service implements and tests authentication,
authorization, tenant isolation, rate limiting and tenant-safe telemetry.
