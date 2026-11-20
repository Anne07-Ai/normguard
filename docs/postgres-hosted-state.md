# PostgreSQL hosted tenant-state adapter

This adapter implements the authoritative grant, immutable policy and activation ports
for the frozen `aws-eu-west-2-multitenant-v1` profile. It does not by itself constitute
live PostgreSQL, tenant-isolation or production-readiness evidence.

## Runtime contract

- Supply `PostgresTenantGrantRepository` and `PostgresTenantPolicyRepository` with a
  connection factory whose context manager commits on success and rolls back on error.
- Connections must use TLS and short-lived credentials from the approved secret
  boundary.
- Do not use an application role with `SUPERUSER`, `BYPASSRLS` or table ownership.
- Execute `src/normguard/sql/001_hosted_tenant_state.sql` through a separate migration
  role. Grant the minimum required table permissions to distinct data-plane and
  control-plane roles after the migration.
- The adapters set `normguard.principal_id` or `normguard.tenant_id` with transaction-
  local `set_config(..., true)`. Pooling must use transaction pooling or perform an
  equivalent verified reset before reuse.
- Application SQL still contains explicit principal/tenant predicates. RLS is a second
  isolation layer, never a substitute for repository predicates.

## Required role separation

| Role | Required capability | Prohibited capability |
|---|---|---|
| Data plane | Read active grants, policies and activations | Policy writes, activation writes, RLS bypass |
| Control plane | Read policies and CAS activation rows | Grant administration, artifact mutation, RLS bypass |
| Policy publisher | Set its authorized tenant context and insert a verified immutable artifact | Update/delete artifacts, tenant data-plane access |
| Migration owner | Apply reviewed schema changes | Runtime credentials or request processing |

The migration intentionally grants nothing to `PUBLIC`. Deployment automation must
grant explicit privileges to named roles and record the resulting grants as evidence.

## Production-like integration gate

Before this adapter can support B-002 evidence, run against the exact PostgreSQL engine,
parameter group, pooler and roles declared for the production candidate. The evidence
must be bound to the source commit, migration digest, database image/version and target
environment digest.

Required checks:

1. A principal cannot read another principal's grant rows, including through prepared
   statements and pooled-connection reuse.
2. Cross-tenant policy and activation reads return no rows under both explicit
   predicates and RLS.
3. Missing session context returns no protected rows.
4. Concurrent activation updates permit exactly one matching CAS revision.
5. A stale revision and a lower rollback floor cannot mutate state.
6. Policy artifact update, delete and foreign-key substitution attempts fail.
7. Transaction rollback and pool reuse clear principal and tenant context.
8. Database timeout, disconnect, failover and malformed-row injection fail closed
   without exposing SQL, DSN, tenant or policy identifiers.
9. Backup and tenant-scoped restore exercises preserve artifact and activation identity.
10. Logs, metrics and traces contain no raw content, credentials or tenant-labelled
    high-cardinality data.

Synthetic tenants and policies are mandatory. Customer content is prohibited before the
final readiness decision. Passing this gate still does not replace independent security
review or close B-001, B-002, B-003 or NG-T22 by itself.
