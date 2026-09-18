# Evaluation Plan

Status: **draft for team review; no results yet**

## Candidate pipelines

- no normalisation baseline;
- Unicode and case normalisation only;
- Porter stemming;
- Snowball stemming;
- lookup lemmatisation;
- POS-aware lemmatisation;
- domain-protected variants of the above.

Applying both stemming and lemmatisation is not assumed to be useful; it must earn inclusion experimentally.

## Retrieval modes

| Mode | Purpose |
|---|---|
| Lexical / BM25 | Measure vocabulary matching effects |
| Dense | Test whether preprocessing preserves or harms embedding retrieval |
| Hybrid | Measure interaction between lexical and semantic signals |
| RAG retrieval | Evaluate whether relevant evidence reaches the generation stage |

## Primary measures

- Recall@k
- Precision@k
- nDCG@k
- Mean Reciprocal Rank
- protected-term corruption rate
- over-normalisation and under-normalisation error rates
- vocabulary reduction
- p50 and p95 processing latency
- peak memory
- index-size change

LLM token counts may be reported, but cost reduction is not assumed and will not be used as the primary success claim.

## Experimental controls

- fixed queries, corpus and relevance judgements;
- identical retriever settings within each comparison;
- deterministic seeds where supported;
- confidence intervals across queries;
- per-domain and aggregate reporting;
- failure examples alongside aggregate scores;
- no tuning on the final evaluation split.

## Decision policy

A candidate is recommended only when it improves the declared task metric without breaching terminology-safety and latency thresholds. If confidence intervals overlap or trade-offs are material, the report returns **inconclusive** or presents a Pareto set rather than naming a winner.

## Planned figures

1. retrieval quality by pipeline and mode;
2. safety–utility Pareto frontier;
3. protected-term corruption heatmap;
4. latency distribution;
5. vocabulary reduction versus retrieval delta;
6. per-domain error taxonomy.

All final figures must be generated from committed, machine-readable summaries.
