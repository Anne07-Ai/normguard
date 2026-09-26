# Retrieval and paired evaluation

`BM25EvaluationHarness` runs the confirmatory lexical retrieval boundary for
NG-POC-001. It accepts one normalisation adapter and a fixed BM25 configuration,
validates every identifier and partition, normalises both indexed documents and
queries through the same adapter, and emits ranked query-level evidence.

Only `calibration` and `development` partitions are accepted. Any other partition,
including the official ESCI test partition, fails before normalisation or indexing.

The harness reports nDCG@10, RR@10, Precision@10, Recall@10 and Recall@100 through
the pinned `ir-measures` implementation. `compare_paired` aligns results by stable
query ID and calculates candidate-minus-baseline deltas. C0 must select and freeze
the BM25 parameters before matched C0/C4 comparison; parameters must never be tuned
separately per normaliser.

Machine-readable output is available through `RetrievalEvaluation.to_dict()` and
`PairedComparison.to_dict()`. Dataset and configuration checksums remain the
responsibility of the existing `RunManifest` evidence boundary.
