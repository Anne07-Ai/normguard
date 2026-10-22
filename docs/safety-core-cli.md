# Safety Core CLI

The CLI is a thin adapter over the public SDK. It does not implement separate
policy parsing, trust or normalisation behavior.

## Verify a policy

```bash
normguard policy verify policy.json \
  --trust-store trust-store.json \
  --activation-state activation-state.json \
  --verification-time 2026-10-22T16:30:00Z \
  --json
```

## Normalise bounded input

Raw text is never accepted as a command-line value. Read it from a regular,
non-symlink UTF-8 file:

```bash
normguard normalise \
  --policy policy.json \
  --trust-store trust-store.json \
  --activation-state activation-state.json \
  --verification-time 2026-10-22T16:30:00Z \
  --input-file input.txt \
  --json
```

Or pass it on stdin:

```bash
normguard normalise \
  --policy policy.json \
  --trust-store trust-store.json \
  --activation-state activation-state.json \
  --verification-time 2026-10-22T16:30:00Z \
  --stdin \
  --json < input.txt
```

Rollback requires both `--rollback-authorization` and
`--rollback-trust-store`. Supplying only one fails closed.

## Output contract

Stdout contains one compact, schema-versioned JSON object on successful policy
verification or completed normalisation. Review-required results include bounded
output and privacy-safe evidence on stdout.

Expected failures emit one compact JSON object on stderr. Errors contain only
stable codes and safe document, policy, key or limit identifiers. They do not
contain input text, policy rules, trust material or signatures.

## Exit codes

| Code | Meaning |
|---:|---|
| 0 | Successful verification or normalisation |
| 10 | Normalisation completed but requires human review |
| 20 | Invalid CLI arguments, file, encoding or document |
| 30 | Untrusted, unsigned, incompatible or rejected policy |
| 40 | Runtime resource limit exceeded |
| 50 | Unexpected internal failure |

## File safety

The CLI rejects symlinks, directories and other non-regular files. File size is
checked before reading, reads are bounded, and platforms supporting
`O_NOFOLLOW` use it to reduce symlink race risk. Verification time is explicit
and must be UTC with a `Z` suffix.

The CLI performs no network calls, policy discovery or online KMS operations.
