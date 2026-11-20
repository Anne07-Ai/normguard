# Protected Hugging Face staging deployment

The Hugging Face Space is a production-like staging target for controlled synthetic
validation. It is not the enterprise production boundary and does not authorize customer
data, public multi-tenant traffic or an SLA claim.

## One-time configuration

Keep `lakshmianne/normguard-private-pilot` on **Protected** access and CPU Basic hardware.
Create a GitHub Environment named `huggingface-staging`, require GitHub Environment
approval, restrict it to `main`, and add these environment secrets:

- `HF_TOKEN`: a dedicated, revocable Hugging Face token with write access only to the
  staging Space;
- `NORMGUARD_BEARER_TOKEN`: the same independently generated application token stored in
  the Space secret of that name.

The signed policy, trust store and activation state remain Space secrets. Never copy them
into GitHub, a deployment artifact or a workflow input. Never upload a signing key.

## Deployment

1. Merge the reviewed change to `main` and copy its full 40-character commit SHA.
2. Open **Actions → Deploy protected Hugging Face staging → Run workflow**.
3. Select `main`, enter the exact commit SHA and approve the protected environment.
4. The workflow confirms the commit belongs to `main`, builds a secret-free Git archive,
   uploads it, waits for readiness, proves denial without the application credential, and
   sends one bounded synthetic request with dual authentication.
5. Retain the non-secret `deployment-manifest.json` artifact with the change record.

Any failed step is a deployment failure. Do not bypass the verification or manually edit
the generated bundle.

## Rollback

Choose the exact previously passing commit from a retained deployment manifest. Run the
same manual workflow from `main` with that full SHA, obtain GitHub Environment approval,
and require all health and dual-authentication checks to pass. Do not roll back by editing
files in the Space UI, moving an unverified branch, or reusing an unknown local directory.

After rollback, record the failed and restored source commits and both workflow run URLs.
Rotate either credential if exposure is suspected; do not include credential values in the
incident record.

This staging workflow uses synthetic data only and does not close B-001, B-002, B-003 or
NG-T22. AWS remains the recommended eventual enterprise production boundary.
