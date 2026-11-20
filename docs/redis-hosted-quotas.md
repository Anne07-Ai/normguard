# Redis hosted quota adapter

`RedisTenantQuotaManager` provides distributed deployment, tenant and principal quota
admission for the frozen AWS hosted-service profile. Its unit tests do not constitute
live Redis, failover, capacity or production-readiness evidence.

## Deployment contract

- Use TLS, authentication, encryption at rest and a dedicated least-privilege Redis
  identity. Keep credentials in the approved secrets boundary.
- Load the exact `REDIS_QUOTA_ACQUIRE_SCRIPT` and `REDIS_QUOTA_RELEASE_SCRIPT` during
  readiness initialization. Verify the returned Redis SHA-1 identifiers and record the
  combined `REDIS_QUOTA_SCRIPT_SHA256` in deployment evidence.
- Runtime uses `EVALSHA` only and fails closed on `NOSCRIPT`, timeout, malformed output
  or script drift. Do not fall back to dynamically supplied Lua.
- All keys share the `{normguard-quota}` cluster hash tag so each admission remains one
  atomic Redis operation. Tenant and principal portions are HMAC pseudonyms; rotate the
  pseudonym key only through a planned quota-state reset or dual-key migration.
- Redis server time drives windows and lease expiry. Service task clocks cannot extend
  admission windows.
- Sorted-set concurrency leases expire after the configured bound. Failed release may
  temporarily reduce availability but cannot over-admit work.
- Redis contains counters, opaque lease tokens and pseudonymous scope references only.
  Request/output content, credentials, policies and raw tenant/principal identifiers are
  prohibited.

## Production-like integration gate

Run against the exact ElastiCache/MemoryDB engine, cluster mode, TLS configuration and
network boundary declared for the production candidate. Bind evidence to source commit,
container digest, Lua SHA-256, quota-profile digest and target-environment digest.

Required tests:

1. Concurrent admissions from multiple tasks never exceed deployment, tenant or
   principal rate/concurrency limits.
2. A noisy tenant cannot consume another tenant's quota; principal rotation cannot
   bypass the shared tenant or deployment scopes.
3. Rejected multi-scope admission consumes no partial counters or leases.
4. Duplicate release is harmless, and abandoned leases recover after the configured
   expiry without over-admission.
5. `NOSCRIPT`, timeout, disconnect, failover, replica promotion and malformed replies
   fail closed and return bounded public errors.
6. Cluster resharding preserves single-slot atomic execution; cross-slot configuration
   is rejected before customer traffic.
7. Script preload and digest mismatch block readiness.
8. Key inspection confirms no raw identifiers, content, credentials or policy material.
9. Metrics expose aggregate saturation without tenant IDs as labels.
10. Sustained production-like load captures Redis latency, CPU, memory, evictions,
    throttling and connection saturation.

Use synthetic identities and content only. Passing this gate does not replace
independent security review or close B-001, B-002, B-003 or NG-T22 by itself.
