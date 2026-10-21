# Two-reviewer policy pilot

The pilot validates the review protocol on 100 calibration candidates before review expands to the
complete queue. Selection is deterministic: 40 brands, 40 model/SKU-like terms, and 20 ambiguous
terms, ordered by the frozen per-type review rank.

## Generate the pilot

```bash
uv run --locked python -m normguard.policy_review pilot \
  ./artifacts/policy-candidates/policy-review-queue.csv \
  ./artifacts/policy-pilot
```

Make one copy of `policy-review-pilot.csv` for Reviewer 1 and another for Reviewer 2. Reviewers must
work independently. Combine only their reviewer-specific class and source columns into a final
`policy-review-pilot-reviewed.csv`; immutable candidate and evidence columns must not change.

Allowed decisions are P0 (exact preserve), P1 (approved alias), P2 (context normalisable), and P3
(review or abstain). Each reviewer must provide an evidence source. Disagreements require both
`resolution_class` and `resolution_source`. Only a resolved P1 row may contain `approved_output`.

## Validate and resolve

```bash
uv run --locked python -m normguard.policy_review resolve \
  ./artifacts/policy-candidates/policy-review-queue.csv \
  ./artifacts/policy-pilot/policy-review-pilot-reviewed.csv \
  ./artifacts/policy-pilot/policy-review-pilot-manifest.json \
  ./artifacts/policy-pilot/resolved \
  --policy-id retail-en-pilot \
  --policy-version 0.1.0
```

The command fails closed on incomplete reviews, changed evidence, duplicate or missing candidates,
unresolved disagreements, or invalid alias decisions. Successful resolution creates
`pilot-policy.json` and `pilot-review-evidence.json`. These are calibration evidence only and do not
authorise development evaluation without the separate two-person approval gate.
