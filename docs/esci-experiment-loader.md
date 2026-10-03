# Guarded ESCI experiment loader

The loader accepts only `calibration` and `development`. Before reading Parquet rows it
verifies all three pinned Amazon artifacts, the deterministic split CSV checksum, the
frozen split seed, the official-training source declaration, and the final-test seal.

The first POC is deliberately English-only (`product_locale == "us"`) and uses only
official `split == "train"`, `small_version == 1` rows. Relevance grades are Exact=3,
Substitute=2, Complement=1 and Irrelevant=0. Query and product identifiers are prefixed
with the locale. Ranking is restricted to each query's official candidate products.

The document representation is frozen to `product_title` only. Additional fields or
boosts require a new reviewed protocol version.

After generating the split artifacts, inspect one partition without running the sealed
test data:

```console
uv run --locked python -m normguard.esci_experiment \
  ~/esci-data/shopping_queries_dataset artifacts/esci calibration
```

Repeat with `development` only after calibration review. The command has no final-test
mode.
