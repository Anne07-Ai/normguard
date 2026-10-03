# Policy input and calibration approval

NormGuard does not invent protected terminology from ESCI. A domain owner must supply a
versioned JSON policy whose rules have an attributable source. The loader is strict:
unknown fields, duplicate rule IDs, invalid policy classes, and invalid P1 outputs fail
closed. The exact policy file SHA-256 becomes run provenance.

Calibration may be used to review policy coverage and failure cases. Development must
not start until an approval record identifies the calibration evidence digest, the exact
policy digest, and at least two distinct named reviewers. Changing one byte of the policy
invalidates the approval and requires calibration review again.

The files under `examples/` are schemas with placeholders, not an approved production
policy or approval. Do not run development by substituting invented reviewer names.
