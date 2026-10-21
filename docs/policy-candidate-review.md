# Calibration policy-candidate review

NormGuard can generate a deterministic, review-only inventory of terminology candidates from
the verified ESCI calibration partition. It reads brand metadata, model/SKU-like tokens, and
ambiguous uppercase terms. Extraction does not classify a candidate as P0–P3 and does not approve
any transformation.

## Generate the worksheet

From the repository root on macOS:

```bash
uv sync --locked --all-extras --dev
uv run --locked python -m normguard.policy_candidates \
  ~/esci-data/shopping_queries_dataset \
  ./artifacts/esci \
  ./artifacts/policy-candidates
```

The command always requests `calibration`; it has no development or test partition option. The
guarded ESCI loader verifies the official-training provenance, frozen split, and sealed-test marker
before reading rows.

Outputs are:

- `policy-candidates.csv`: reviewer worksheet with two independent reviewer columns and separate
  resolution columns, all initially empty.
- `policy-candidates.json`: canonical candidate records for tooling.
- `policy-candidates-manifest.json`: input split digest, output SHA-256 digests, counts, and the
  official-test seal.
- `policy-review-queue.csv`: deterministic human-review worksheet capped at 2,000 records, with
  per-type ranks and inclusion reasons.
- `policy-review-queue.json`: machine-readable equivalent of the bounded review queue.

The complete `policy-candidates.*` inventory remains unchanged for auditability. Review work should
be performed only in `policy-review-queue.csv`. The queue uses explicit minimum frequencies, filters
common quantity/capacity attributes from identifier-like candidates, and applies stable quotas of
1,000 brands, 700 identifier-like terms, and 300 ambiguous terms.

Every candidate starts as `UNREVIEWED`. A candidate may enter a policy file only after two reviewers
record evidence-backed classifications and resolve disagreements. Never infer P0–P3 from extraction
type or frequency.

## External datasets

Kaggle or other external data must not be added to these calibration outputs. If later used for
robustness testing, keep it in a separate workflow with an explicit licence, provenance manifest,
and distinct results. The official ESCI test partition remains sealed.


## Merge independent pilot reviews

Keep each completed reviewer worksheet private and separate. After both reviewers finish, merge them
with the original pilot rather than copying columns manually:

```bash
uv run --locked python -m normguard.policy_review merge \\
  ./artifacts/policy-pilot/policy-review-pilot.csv \\
  ./artifacts/policy-pilot/lakshmi-review-completed.csv \\
  ./artifacts/policy-pilot/padma-review-completed.csv \\
  ./artifacts/policy-pilot/merged
```

The merge command fails closed if candidate IDs, row order, schema, or immutable evidence differ from
the pilot. It also rejects incomplete classifications, missing evidence, cross-reviewer edits, and
pre-populated resolution fields. It emits:

- `policy-review-pilot-reviewed.csv`: both isolated reviewer decisions combined, with resolution
  fields still empty.
- `policy-review-agreement.json`: deterministic input/output digests, agreement rate, class-pair
  counts, and explicit disagreement rows.

The merge never resolves a disagreement. A human must populate `resolution_class` and
`resolution_source` before the existing `resolve` command can create policy rules. For P1, the
human must also provide the exact `approved_output`. Real review files and generated artifacts are
local evidence and must not be committed.
