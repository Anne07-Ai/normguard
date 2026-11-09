---
title: NormGuard Private Pilot
emoji: 🛡️
colorFrom: blue
colorTo: green
sdk: docker
app_port: 7860
---

# NormGuard private Hugging Face pilot

This Docker Space is a private, single-tenant controlled pilot. It is not approved
for public, multi-tenant or customer-data use.

## Required Space secrets

- `NORMGUARD_BEARER_TOKEN`: randomly generated value containing at least 32 characters.
- `NORMGUARD_POLICY_B64`: base64 of the reviewed signed policy bundle.
- `NORMGUARD_TRUST_STORE_B64`: base64 of the public trust store.
- `NORMGUARD_ACTIVATION_STATE_B64`: base64 of the activation floor document.

Never upload a private signing key. The process fails startup when any document is
missing, malformed, unsigned, expired, rolled back or otherwise untrusted.

## Space settings

1. Create the Space as **Private**, SDK **Docker**, hardware **CPU Basic**.
2. Add the four secrets above in Settings; never add them as public Variables.
3. Copy the reviewed repository contents and use `deploy/huggingface/Dockerfile` as
   the Space Dockerfile.
4. Keep the default single Uvicorn worker. Do not enable repository persistence.
5. Confirm `/health` reports ready before running synthetic tests.

The API surface is intentionally limited to unauthenticated `/health` and authenticated
`POST /v1/normalise`. OpenAPI and interactive documentation are disabled. Access logs
are disabled so request paths and transport metadata are not retained by the process.

Hugging Face platform logs and proxy behavior remain platform-controlled. Use synthetic
inputs only and never treat a private Space as an enterprise identity, tenant-isolation
or production-SLA boundary.
