# Run calibration and development

Run calibration first with a reviewed policy file:

```console
uv run --locked python -m normguard.experiment_runner \
  ~/esci-data/shopping_queries_dataset artifacts/esci policy.json \
  artifacts/calibration calibration
```

Review the calibration report, then create an approval file containing the exact policy
and calibration evidence digests plus two distinct named reviewers. Only then run:

```console
uv run --locked python -m normguard.experiment_runner \
  ~/esci-data/shopping_queries_dataset artifacts/esci policy.json \
  artifacts/development development --approval calibration-approval.json
```

Calibration uses the frozen BM25 reference pair `(k1=0.9, b=0.75)`. Development selects
the C0 pair from the frozen 12-point grid using nDCG@10 and applies that exact pair to C4.
The runner produces canonical JSON, Markdown, SVG and paired-statistics evidence. It
samples up to 200 inputs after warm-up for p95 normalisation latency. There is no
final-test mode.
