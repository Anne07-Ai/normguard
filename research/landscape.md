# Landscape Review

Status: **initial map — evidence collection in progress**

## Capability matrix

| Solution | Stemming / lemmatisation | Custom rules | Task-level evaluation | Retrieval regression gate | Automatic recommendation |
|---|---:|---:|---:|---:|---:|
| NLTK | Yes | Limited by component | No | No | No |
| spaCy | Yes | Yes | No | No | No |
| Elasticsearch | Yes | Yes | Indirectly through search tooling | No dedicated normalisation gate | No |
| OpenSearch | Yes | Yes | Indirectly through search tooling | No dedicated normalisation gate | No |
| Hugging Face Tokenizers | Normalisation primitives | Yes | No | No | No |
| Proposed NormGuard scope | Adapter-based | Yes | Yes | Yes | Evidence-based |

This table is a working hypothesis, not a novelty claim. Every cell must be verified against current primary documentation.

## Primary starting sources

- [spaCy Lemmatizer API](https://spacy.io/api/lemmatizer)
- [NLTK stemming API](https://www.nltk.org/api/nltk.stem.html)
- [Elasticsearch stemming guide](https://www.elastic.co/docs/manage-data/data-store/text-analysis/stemming)
- [Elasticsearch stemmer override filter](https://www.elastic.co/docs/reference/text-analysis/analysis-stemmer-override-tokenfilter)
- [OpenSearch stemmer token filter](https://docs.opensearch.org/latest/analyzers/token-filters/stemmer/)
- [Hugging Face Tokenizers normalizers](https://huggingface.co/docs/tokenizers/api/normalizers)
- [A Task-Oriented Evaluation Framework for Text Normalization in Modern NLP Pipelines](https://arxiv.org/abs/2511.20409)

## Closest known overlap

The 2025 task-oriented evaluation paper proposes measurements for stemming utility, downstream performance change, and semantic damage. NormGuard must not present task-oriented evaluation alone as novel.

The proposed differentiating combination to test is:

1. developer-supplied retrieval tasks;
2. adapters for multiple normalisation engines;
3. protected-term safety analysis;
4. lexical, dense, and hybrid retrieval comparisons;
5. CI-compatible regression decisions;
6. inspectable evidence for every recommendation.

## Required next work

- Expand to at least 15 credible solutions or papers.
- Verify each matrix cell using primary sources.
- Search package registries and GitHub for close open-source implementations.
- Review commercial search-relevance testing platforms.
- Record naming, licensing, maintenance, and integration risks.
- Produce a dated novelty assessment without claiming exhaustive patent clearance.
