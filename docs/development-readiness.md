# Calibration and development readiness

The official ESCI ranking task supplies a query-specific candidate product set. A
NormGuard query must therefore rank only the product IDs attached to that query in the
official training rows. Ranking against one unrestricted global product corpus changes
the task and invalidates nDCG comparisons.

`RetrievalQuery.candidate_document_ids` records this boundary. The BM25 index may be
shared for efficiency, but scoring output is filtered and deterministically ordered
within each query's declared candidates. Missing, duplicate or unknown candidates fail
before evaluation.

Development parameter selection uses the frozen grid: k1 in `{0.6, 0.9, 1.2, 1.5}`
and b in `{0.25, 0.50, 0.75}`. `select_bm25_parameters` maximises C0 development
nDCG@10 and breaks ties using smaller k1 followed by smaller b. The selected pair must
then be reused unchanged for C4.

`paired_statistics` implements deterministic 10,000-sample paired bootstrap confidence
intervals and a paired sign-flip randomisation sensitivity test using an explicit seed.
It records the NumPy bit generator and rejects empty or non-finite inputs.

The guarded ESCI loader and end-to-end command must not be enabled until candidate-set
semantics are merged and independently reviewed. Official test rows remain sealed.
