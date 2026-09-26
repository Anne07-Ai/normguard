# Amazon ESCI dataset protocol

This protocol pins the third-party benchmark input used by NormGuard. Corpus files are local
inputs and must never be committed to this repository.

## Provenance and terms

| Field | Pinned value |
|---|---|
| Dataset | Amazon Shopping Queries Dataset (ESCI) |
| Official repository | <https://github.com/amazon-science/esci-data> |
| Upstream commit | `7916cdf6ab75a462e77f20ab40428a10923998d5` |
| NormGuard variant | reduced ranking version (`small_version == 1`) |
| Development source | official `train` partition only |
| Upstream licence | Apache-2.0; retain upstream `LICENSE` and `NOTICE` |
| Official test | sealed; excluded from preparation and development decisions |

The Amazon repository and paper remain the authority for the dataset. NormGuard's licence does
not replace the dataset's upstream terms.

## Immutable artefacts

The files below were downloaded through Git LFS from the pinned upstream commit.

| File | SHA-256 |
|---|---|
| `shopping_queries_dataset_examples.parquet` | `4a735b693b4a424a6fc67f5be6e4c811495c488bbf66d02a602d308b2744263a` |
| `shopping_queries_dataset_products.parquet` | `25124442d064d64b26f74082d6fa09438d679efc0c183cf28d19064a2b65a265` |
| `shopping_queries_dataset_sources.csv` | `a5fed8ecc016443de40bf3c63098f0e3f23bbe4daa4236f1c38b8c3184778c50` |

The examples file must contain `example_id`, `query`, `query_id`, `product_id`,
`product_locale`, `esci_label`, `small_version`, `large_version`, and `split`. Preparation fails
closed when a file is absent, a digest differs, or required schema columns are missing.

## Reproduce locally

```bash
git clone https://github.com/amazon-science/esci-data.git
cd esci-data
git checkout 7916cdf6ab75a462e77f20ab40428a10923998d5
git lfs pull

cd ../normguard
python -m pip install -e '.[esci]'
python -m normguard.esci \
  ../esci-data/shopping_queries_dataset \
  ./artifacts/esci
```

The command verifies all three source files before reading data. It then requests only rows where
`split == "train"` and `small_version == 1` from the examples Parquet file. Product content,
labels from the official test partition, and final-test results are not read or transformed.

## Deterministic split contract

The unit of assignment is the tuple `(product_locale, query_id)`, preventing query-result pairs
for one query from leaking between partitions. The key
`normguard-esci-v1<US>locale<US>query_id` is hashed with SHA-256 and assigned using 10,000 stable
buckets:

- `0000..7999`: train;
- `8000..8999`: calibration;
- `9000..9999`: development.

`query-splits.csv` is sorted by locale and query ID. `split-manifest.json` records the algorithm,
seed, source partition, query counts by split, and SHA-256 of the CSV. No generation timestamp is
included, so identical inputs produce byte-identical outputs.

## Verified manifest evidence

The pinned dataset produced 33,804 unique training queries:

| NormGuard split | Query count |
|---|---:|
| Train | 27,119 |
| Calibration | 3,329 |
| Development | 3,356 |
| **Total** | **33,804** |

The generated `query-splits.csv` SHA-256 is
`e815684de37a398dde2bafa6f4018fc7fdfb6e87a710965151b454f2efc2f276`.

See the versioned
[example manifest](../examples/esci-split-manifest.example.json) for the complete evidence shape.
The manifest contains metadata and checksums only; neither the corpus nor query assignments are
committed.
